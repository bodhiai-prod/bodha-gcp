"""One-off migration: give every ingested competitive document an owner.

Competitive documents used to be reachable from any account. `past_questions`
carried no owner field at all, and every query against it filtered on `exam_id`
alone, so an uploaded PDF's extracted text surfaced in another account's exam
view, its RAG anchors, and the rag-preview endpoint. This script establishes the
ownership the new access checks rely on.

Four passes:

1. Stamp `past_papers.owner_id` from `uploaded_by`.
2. Stamp `past_questions.owner_id` from its parent paper, joined on `paper_id`.
3. Repair cross-uploads. `upload_past_paper` never checked who owned the exam,
   so account B's papers may sit inside account A's exam. Making exams
   owner-only without handling that would strand B's own uploads inside an exam
   B can no longer open. For each such (exam, uploader) pair a private exam is
   created for the uploader and their documents are re-pointed to it.
4. Recompute `papers_count` / `questions_count`, since pass 3 moves documents
   between exams.

Read-only by default. The cross-upload count printed by a dry run is also the
answer to "did anything actually leak" -- run it before deciding anything else.

    python scripts/migrate_competitive_ownership.py            # report only
    python scripts/migrate_competitive_ownership.py --apply    # write

Questions whose parent paper no longer exists are left UNOWNED on purpose: they
match no scope and are invisible to everyone, which is the right default for
data whose provenance is gone. They are counted so they can be cleaned up as a
deliberate decision rather than a side effect of this script.

Safe to re-run; every pass skips rows already carrying an owner.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "backend" / ".env")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Report:
    """Counters printed at the end, and the only thing a dry run produces."""

    def __init__(self) -> None:
        self.papers_stamped = 0
        self.papers_already = 0
        self.papers_no_uploader = 0
        self.questions_stamped = 0
        self.questions_already = 0
        self.questions_orphaned = 0
        self.exams_cloned = 0
        self.cross_uploads = 0
        self.counters_fixed = 0

    def render(self, applied: bool) -> str:
        mode = "APPLIED" if applied else "DRY RUN (nothing written)"
        lines = [
            "",
            "=" * 68,
            f"  Competitive document ownership migration -- {mode}",
            "=" * 68,
            "",
            "past_papers",
            f"  owner_id stamped from uploaded_by ....... {self.papers_stamped}",
            f"  already owned, skipped .................. {self.papers_already}",
            f"  no uploader recorded, left unowned ...... {self.papers_no_uploader}",
            "",
            "past_questions",
            f"  owner_id stamped from parent paper ...... {self.questions_stamped}",
            f"  already owned, skipped .................. {self.questions_already}",
            f"  parent paper missing, left UNOWNED ...... {self.questions_orphaned}",
            "",
            "cross-uploads (documents inside somebody else's exam)",
            f"  documents affected ...................... {self.cross_uploads}",
            f"  private exams created for their owners .. {self.exams_cloned}",
            "",
            f"exam counters corrected ................... {self.counters_fixed}",
            "",
        ]
        if self.questions_orphaned:
            lines += [
                f"NOTE: {self.questions_orphaned} extracted question(s) have no",
                "      surviving parent paper. They are left unowned and are now",
                "      invisible to every account, including admins. Delete them",
                "      deliberately if you want them gone.",
                "",
            ]
        if self.cross_uploads:
            lines += [
                f"NOTE: {self.cross_uploads} document(s) were uploaded into an exam",
                "      owned by a different account. That is the exposure the new",
                "      access checks close; each uploader keeps their own files in",
                "      a private exam created for them.",
                "",
            ]
        else:
            lines += [
                "No cross-account uploads found: every document already sat in an",
                "exam owned by the account that uploaded it.",
                "",
            ]
        return "\n".join(lines)


async def stamp_papers(db, apply: bool, rep: Report) -> None:
    async for p in db.past_papers.find({}, {"_id": 0, "id": 1, "owner_id": 1, "uploaded_by": 1}):
        if p.get("owner_id"):
            rep.papers_already += 1
            continue
        uploader = p.get("uploaded_by")
        if not uploader:
            rep.papers_no_uploader += 1
            continue
        rep.papers_stamped += 1
        if apply:
            await db.past_papers.update_one(
                {"id": p["id"]}, {"$set": {"owner_id": uploader}}
            )


async def stamp_questions(db, apply: bool, rep: Report) -> None:
    # paper_id -> owner, built once. The collection is small enough to hold in
    # memory (it is capped by what teachers have uploaded), and the alternative
    # is a lookup per question.
    owners: dict = {}
    async for p in db.past_papers.find({}, {"_id": 0, "id": 1, "owner_id": 1, "uploaded_by": 1}):
        owner = p.get("owner_id") or p.get("uploaded_by")
        if owner:
            owners[p["id"]] = owner

    async for q in db.past_questions.find({}, {"_id": 0, "id": 1, "owner_id": 1, "paper_id": 1}):
        if q.get("owner_id"):
            rep.questions_already += 1
            continue
        owner = owners.get(q.get("paper_id"))
        if not owner:
            rep.questions_orphaned += 1
            continue
        rep.questions_stamped += 1
        if apply:
            await db.past_questions.update_one(
                {"id": q["id"]}, {"$set": {"owner_id": owner}}
            )


async def repair_cross_uploads(db, apply: bool, rep: Report) -> None:
    """Move documents out of exams their uploader does not own."""
    exams = {}
    async for e in db.competitive_exams.find({}, {"_id": 0}):
        exams[e["id"]] = e

    # (exam_id, uploader) -> [paper ids]
    misplaced: dict = {}
    async for p in db.past_papers.find(
        {"is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "exam_id": 1, "owner_id": 1, "uploaded_by": 1},
    ):
        exam = exams.get(p.get("exam_id"))
        if not exam:
            continue
        uploader = p.get("owner_id") or p.get("uploaded_by")
        if not uploader or uploader == exam.get("owner_id"):
            continue
        misplaced.setdefault((p["exam_id"], uploader), []).append(p["id"])

    for (exam_id, uploader), paper_ids in sorted(misplaced.items()):
        source = exams[exam_id]
        rep.cross_uploads += len(paper_ids)
        rep.exams_cloned += 1
        new_id = str(uuid.uuid4())
        clone = {
            "id": new_id,
            "name": f"{source.get('name', 'Competitive Exam')} (my copy)",
            "description": source.get("description", ""),
            "exam_type": (source.get("exam_type") or "GENERIC").upper(),
            "owner_id": uploader,
            "papers_count": 0,
            "questions_count": 0,
            "created_at": utcnow_iso(),
            "is_deleted": False,
            "migrated_from_exam_id": exam_id,
        }
        print(
            f"  exam {exam_id[:8]}... has {len(paper_ids)} paper(s) uploaded by "
            f"{uploader[:8]}...; moving them to a private exam {new_id[:8]}..."
        )
        if not apply:
            continue
        await db.competitive_exams.insert_one(clone)
        await db.past_papers.update_many(
            {"id": {"$in": paper_ids}},
            {"$set": {"exam_id": new_id, "owner_id": uploader}},
        )
        await db.past_questions.update_many(
            {"paper_id": {"$in": paper_ids}},
            {"$set": {"exam_id": new_id, "owner_id": uploader}},
        )


async def recount(db, apply: bool, rep: Report) -> None:
    async for e in db.competitive_exams.find({}, {"_id": 0, "id": 1, "papers_count": 1, "questions_count": 1}):
        papers = await db.past_papers.count_documents(
            {"exam_id": e["id"], "is_deleted": {"$ne": True}}
        )
        questions = await db.past_questions.count_documents({"exam_id": e["id"]})
        if papers == (e.get("papers_count") or 0) and questions == (
            e.get("questions_count") or 0
        ):
            continue
        rep.counters_fixed += 1
        if apply:
            await db.competitive_exams.update_one(
                {"id": e["id"]},
                {"$set": {"papers_count": papers, "questions_count": questions}},
            )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply", action="store_true", help="write changes (default: report only)"
    )
    args = ap.parse_args()

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("MONGO_URL and DB_NAME must be set (backend/.env)", file=sys.stderr)
        return 2

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    rep = Report()
    try:
        await db.command("ping")
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach Mongo: {e}", file=sys.stderr)
        return 2

    try:
        print(f"database: {db_name}")
        print("pass 1/4  past_papers.owner_id")
        await stamp_papers(db, args.apply, rep)
        print("pass 2/4  past_questions.owner_id")
        await stamp_questions(db, args.apply, rep)
        print("pass 3/4  cross-upload repair")
        await repair_cross_uploads(db, args.apply, rep)
        print("pass 4/4  exam counters")
        await recount(db, args.apply, rep)
    finally:
        client.close()

    print(rep.render(args.apply))
    if not args.apply:
        print("Re-run with --apply to write these changes.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
