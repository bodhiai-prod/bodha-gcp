"""Competitive-exam endpoints + RAG-calibrated paper generation.

Conceptual model:
    competitive_exams (e.g. "JEE Main", "NEET", "CAT")
    └─ past_papers (PDFs uploaded by teachers; ingested in the background)
        └─ past_questions (extracted, tagged with topic+difficulty,
                           embedded for similarity search)

When a teacher generates a fresh practice paper for an exam, we retrieve
similar past questions (per topic) and pass them to the LLM as anchors so
the model calibrates difficulty against what *that specific exam* really
looks like, rather than relying on its priors.
"""
from __future__ import annotations

import asyncio as _asyncio
import uuid
from typing import Optional

from fastapi import Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from auth import get_current_user
from deps import api_router, db, logger, utcnow_iso
from diagram_policy import apply_diagram_policy
from exam_formats import EXAM_FORMATS, get_format
from latex_repair import repair_sections
from llm_adapter import chain_for_exam, chat_complete, parse_json_response
from pdf_utils import chunk_text, extract_text
from prompts import (
    QPAPER_EXTRACT_SYSTEM,
    avoid_list_block,
    qpaper_extract_prompt,
    competitive_qgen_prompt,
    competitive_system_for_exam,
)
from instruction_spec import parse_instructions, verify as verify_instructions
from question_dedupe import dedupe_sections
from question_repair import complete_sections, validate_question
from rag import top_k_similar, difficulty_distribution
from storage import APP_NAME, put_object
from topic_balance import measure_coverage, partition_topics as _partition_topics
from token_usage import tag
from workers import (
    count_questions,
    flatten_question_texts,
    generate_diagrams_background,
    merge_topup_sections,
    paper_target_count,
    reconcile_section_plan,
    _auto_topup_if_short,
    _fail_topup,
    _finish_topup,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ExamCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: str = ""
    exam_type: str = "GENERIC"  # JEE_MAINS | JEE_ADV | CAT | UPSC | NEET | GENERIC


class CompetitivePaperRequest(BaseModel):
    exam_id: str
    title: str = "Practice Paper"
    topics: list[str] = Field(..., min_length=1)
    difficulty: str = "medium"
    question_count: int = Field(10, ge=3, le=200)
    duration_minutes: int = Field(60, ge=5, le=300)
    format_distribution: dict = Field(default_factory=lambda: {"mcq": 100})
    custom_instructions: str = ""


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
# An exam and everything ingested into it belong to exactly one account.
# These two helpers are the only sanctioned way to reach either, because the
# leaks they replace were all the same mistake: a query that filtered on
# exam_id and forgot there was an owner.
async def _owned_exam(exam_id: str, user: dict, *, include_deleted: bool = False) -> dict:
    """Fetch an exam the caller is allowed to touch, or raise.

    Answers 404 rather than 403 for an exam belonging to somebody else. A
    403 confirms the id exists, which is itself a disclosure -- and the ids
    are handed out in URLs.
    """
    q: dict = {"id": exam_id}
    if not include_deleted:
        q["is_deleted"] = False
    exam = await db.competitive_exams.find_one(q, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    if user.get("role") != "admin" and exam.get("owner_id") != user.get("id"):
        raise HTTPException(status_code=404, detail="Exam not found")
    exam.setdefault("exam_type", "GENERIC")
    return exam


def _docs_of(exam: dict) -> dict:
    """Query fragment selecting the documents that belong to this exam.

    Scoped to the exam's OWNER rather than to the caller, and deliberately
    so: every document in an exam belongs to the exam owner, which the
    upload guard maintains and the migration establishes. One rule covers
    every caller, admins included -- an admin opening an exam sees that
    exam's real corpus, not a different view of it.

    A document with no owner_id (an orphan the migration could not resolve)
    matches nothing here and is invisible to everyone. Failing closed is the
    right default for data whose provenance was lost.
    """
    return {"exam_id": exam.get("id"), "owner_id": exam.get("owner_id")}


@api_router.get("/competitive-exams/formats")
async def competitive_exam_formats(user: dict = Depends(get_current_user)):
    """Locked-format catalogue. UI uses this to render the canonical
    question-count/duration/marks for each preset exam and to hide editable
    inputs for those. Auth-required (no PII, but no need to leak internals)."""
    return EXAM_FORMATS


# ---------------------------------------------------------------------------
# Exam CRUD
# ---------------------------------------------------------------------------
@api_router.post("/competitive-exams")
async def create_exam(payload: ExamCreate, user: dict = Depends(get_current_user)):
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Teacher or admin only")
    exam_id = str(uuid.uuid4())
    doc = {
        "id": exam_id,
        "name": payload.name.strip(),
        "description": payload.description.strip(),
        "exam_type": (payload.exam_type or "GENERIC").upper(),
        "owner_id": user["id"],
        "papers_count": 0,
        "questions_count": 0,
        "created_at": utcnow_iso(),
        "is_deleted": False,
    }
    await db.competitive_exams.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.get("/competitive-exams")
async def list_exams(user: dict = Depends(get_current_user)):
    # One rule for everyone: your own exams, or all of them if you are an
    # admin. Students own none (creation is teacher/admin only), so they get
    # an empty list -- the nav never offered them this section anyway.
    q: dict = {"is_deleted": False}
    if user["role"] != "admin":
        q["owner_id"] = user["id"]
    rows = await db.competitive_exams.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    for r in rows:
        r.setdefault("exam_type", "GENERIC")
    return rows


@api_router.get("/competitive-exams/{exam_id}")
async def get_exam(exam_id: str, user: dict = Depends(get_current_user)):
    exam = await _owned_exam(exam_id, user)
    # Topic histogram across indexed past questions. Scoped like everything
    # else here -- an unscoped histogram still discloses what another account
    # uploaded, in aggregate.
    topic_counts: dict = {}
    diff_counts = {"easy": 0, "medium": 0, "hard": 0}
    async for q in db.past_questions.find(
        _docs_of(exam), {"_id": 0, "topic": 1, "difficulty": 1}
    ):
        t = (q.get("topic") or "Uncategorised").strip()
        topic_counts[t] = topic_counts.get(t, 0) + 1
        d = (q.get("difficulty") or "").lower()
        if d in diff_counts:
            diff_counts[d] += 1
    exam["topic_counts"] = topic_counts
    exam["difficulty_counts"] = diff_counts
    # storage_path is the object-store key. Nothing in the UI uses it and it
    # has no business leaving the server.
    papers = (
        await db.past_papers.find(
            {**_docs_of(exam), "is_deleted": False},
            {"_id": 0, "storage_path": 0},
        )
        .sort("created_at", -1)
        .to_list(200)
    )
    exam["papers"] = papers
    return exam


# ---------------------------------------------------------------------------
# Upload past paper -> ingest -> embed
# ---------------------------------------------------------------------------
async def _ingest_past_paper(
    paper_id: str, exam_id: str, pdf_bytes: bytes, owner_id: str
) -> None:
    """Background ingestion: extract text, LLM-parse questions, persist."""
    tag("past_paper", paper_id)
    try:
        text = extract_text(pdf_bytes)
        # Cap so we don't blow past prompt limits — for big papers process
        # in chunks of ~6000 chars each.
        chunks = chunk_text(text, chunk_size=6000, overlap=200)[:6]
        all_questions: list = []
        for ch in chunks:
            try:
                raw = await chat_complete(
                    system_message=QPAPER_EXTRACT_SYSTEM,
                    user_text=qpaper_extract_prompt(ch),
                )
                data = parse_json_response(raw)
                for q in data.get("questions") or []:
                    qt = (q.get("question") or "").strip()
                    if not qt or len(qt) < 8:
                        continue
                    all_questions.append({
                        "id": str(uuid.uuid4()),
                        "exam_id": exam_id,
                        "paper_id": paper_id,
                        # Without this the extracted text is reachable from
                        # any account that knows the exam id.
                        "owner_id": owner_id,
                        "text": qt,
                        "marks": int(q.get("marks", 2) or 2),
                        "type": q.get("type", "concept"),
                        "difficulty": (q.get("difficulty") or "medium").lower(),
                        "topic": (q.get("topic") or "").strip(),
                        "created_at": utcnow_iso(),
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning(f"past-paper chunk parse failed: {e}")
                continue

        if all_questions:
            await db.past_questions.insert_many(all_questions)
        await db.past_papers.update_one(
            {"id": paper_id},
            {"$set": {
                "status": "ready" if all_questions else "extraction_empty",
                "questions_count": len(all_questions),
            }},
        )
        await db.competitive_exams.update_one(
            {"id": exam_id},
            {"$inc": {"questions_count": len(all_questions)}},
        )
        logger.info(
            f"Ingested {len(all_questions)} questions into exam={exam_id} paper={paper_id}"
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Past-paper ingestion failed")
        await db.past_papers.update_one(
            {"id": paper_id},
            {"$set": {"status": "failed", "error": str(e)[:300]}},
        )


@api_router.post("/competitive-exams/{exam_id}/papers/upload")
async def upload_past_paper(
    exam_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Teacher or admin only")
    # Used to check only that the exam existed, so anyone could upload into
    # anyone's exam -- and inflate their counters doing it.
    exam = await _owned_exam(exam_id, user)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(pdf_bytes) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 100MB)")

    paper_id = str(uuid.uuid4())
    path = f"{APP_NAME}/competitive/{exam_id}/{paper_id}.pdf"
    try:
        put_object(path, pdf_bytes, "application/pdf")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"storage failed (non-fatal): {e}")

    doc = {
        "id": paper_id,
        "exam_id": exam_id,
        "original_filename": file.filename,
        # owner_id is the field every access check reads and matches the
        # convention used by every other collection. uploaded_by is kept in
        # step with it so documents written before the migration and after it
        # look the same to anything still reading the old name.
        "owner_id": exam.get("owner_id"),
        "uploaded_by": user["id"],
        "storage_path": path,
        "status": "ingesting",
        "questions_count": 0,
        "error": None,
        "created_at": utcnow_iso(),
        "is_deleted": False,
    }
    await db.past_papers.insert_one(doc)
    await db.competitive_exams.update_one(
        {"id": exam_id}, {"$inc": {"papers_count": 1}}
    )

    _asyncio.create_task(
        _ingest_past_paper(paper_id, exam_id, pdf_bytes, exam.get("owner_id"))
    )
    doc.pop("_id", None)
    return doc


@api_router.delete("/competitive-exams/{exam_id}/papers/{paper_id}")
async def delete_past_paper(
    exam_id: str, paper_id: str, user: dict = Depends(get_current_user)
):
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Teacher or admin only")
    await _owned_exam(exam_id, user)
    p = await db.past_papers.find_one({"id": paper_id, "exam_id": exam_id})
    if not p:
        raise HTTPException(status_code=404, detail="Past paper not found")
    # owner_id is the current field; uploaded_by covers a row the migration
    # has not reached yet.
    owner = p.get("owner_id") or p.get("uploaded_by")
    if owner != user["id"] and user["role"] != "admin":
        raise HTTPException(status_code=404, detail="Past paper not found")
    await db.past_papers.update_one({"id": paper_id}, {"$set": {"is_deleted": True}})
    # Tombstone the questions too so RAG retrieval ignores them.
    res = await db.past_questions.delete_many({"paper_id": paper_id})
    await db.competitive_exams.update_one(
        {"id": exam_id},
        {"$inc": {"papers_count": -1, "questions_count": -res.deleted_count}},
    )
    return {"deleted_questions": res.deleted_count}


# ---------------------------------------------------------------------------
# RAG-calibrated paper generation
# ---------------------------------------------------------------------------
async def _retrieve_anchors(
    exam: dict, topics: list[str], k_per_topic: int = 3
) -> list[dict]:
    """For each topic build a focused similarity query and grab top-K
    anchors. Deduplicate by question_id.

    Takes the exam DOCUMENT, not its id, because the corpus has to be scoped
    to the owner. This used to select on exam_id alone, which put another
    account's extracted question text verbatim into this account's generation
    prompt -- the quietest of the leaks, since it never surfaced in a
    response.
    """
    corpus = await db.past_questions.find(
        _docs_of(exam),
        {"_id": 0, "id": 1, "text": 1, "topic": 1, "difficulty": 1, "marks": 1, "type": 1},
    ).to_list(5000)
    if not corpus:
        return []
    seen: set = set()
    anchors: list = []
    for t in topics:
        # Use topic name itself as the query; works well for short topic anchors.
        hits = top_k_similar(t, corpus, k=k_per_topic)
        for h in hits:
            if h["id"] in seen:
                continue
            seen.add(h["id"])
            anchors.append(h)
    return anchors


# Only the structural faults _normalise_questions already dropped for. A stem
# that reads oddly still renders and is still answerable, so it is reported
# and left alone rather than silently deleted from the teacher's paper.
_STRICT_DROP_ISSUES = {"mcq_missing_options", "blank_option", "bad_correct_option"}


def _reject(rejects: Optional[list], q: dict) -> None:
    """Hand a dropped question to the caller, if the caller wanted it."""
    if rejects is not None:
        rejects.append(q)


def _drop_unfinished(sections: list) -> int:
    """Remove strict-MCQ questions the completion pass could not finish.

    The last gate before a locked-format paper is saved: an item that still
    has no fourth option cannot be shown on a JEE or NEET paper. Returns how
    many went, for the completeness_check stats.
    """
    dropped = 0
    for s in sections or []:
        kept = []
        for q in s.get("questions", []) or []:
            if _STRICT_DROP_ISSUES & set(validate_question(q, strict_mcq=True)):
                dropped += 1
                continue
            kept.append(q)
        s["questions"] = kept
    return dropped


async def _complete_and_gate(
    sections: list,
    *,
    ref: str,
    strict_mcq: bool,
    exam_type: str,
    difficulty: str,
    rejects: Optional[list] = None,
    room: Optional[int] = None,
) -> dict:
    """Fold rejects back in, finish what is unfinished, then re-gate.

    Rejects are merged BEFORE the completion call rather than repaired
    separately, so a paper that lost questions two different ways still only
    pays for one repair call.
    """
    if rejects and sections:
        take = rejects if room is None else rejects[:max(0, room)]
        sections[0].setdefault("questions", []).extend(take)
    stats = await complete_sections(
        sections,
        ref=ref,
        strict_mcq=strict_mcq,
        context={"exam_type": exam_type, "difficulty": difficulty},
    )
    if strict_mcq:
        stats["dropped"] = _drop_unfinished(sections)
    return stats


def _normalise_questions(
    sections: list,
    default_difficulty: str,
    strict_mcq: bool = False,
    rejects: Optional[list] = None,
) -> list:
    """In-place normalise sections + return the flat question list.

    When `strict_mcq` is True (used for JEE_MAINS / JEE_ADV / CAT / UPSC /
    NEET — all locked to 100% 4-option MCQ), any question that arrives
    without exactly 4 options or with no valid correct_option is DROPPED
    rather than degraded. Batching gives us extra questions to absorb the
    occasional reject.

    Pass `rejects` to collect those drops instead of losing them. A question
    that arrives with two options is not junk, it is an unfinished question,
    and question_repair can usually finish it -- but only if something kept
    hold of it."""
    for s in sections:
        kept: list = []
        for q in s.get("questions", []):
            q["id"] = str(uuid.uuid4())
            q.setdefault("important", False)
            q.setdefault("marks", 1)
            q.setdefault("type", "concept")
            q.setdefault("difficulty", default_difficulty)
            q.setdefault("needs_diagram", False)
            q.setdefault("format", "")

            opts = q.get("options") or []
            if isinstance(opts, list):
                opts = [str(o).strip() for o in opts if str(o).strip()][:4]
            else:
                opts = []

            if strict_mcq:
                # In MCQ-only mode every question MUST be a valid 4-option MCQ.
                # Write the cleaned values back before any drop decision, so a
                # rejected question travels to the repair pass in the same
                # shape a kept one would.
                q["format"] = "mcq"
                q["options"] = opts
                if len(opts) != 4:
                    _reject(rejects, q)
                    continue
                try:
                    co = int(q.get("correct_option", 0))
                except (TypeError, ValueError):
                    # Was 0, which reads as a valid index and shipped a wrong
                    # answer key. -1 fails validate_question instead, so the
                    # repair pass is asked to solve the question properly.
                    co = -1
                if not (0 <= co <= 3):
                    q["correct_option"] = co
                    _reject(rejects, q)
                    continue
                q["correct_option"] = co
                kept.append(q)
                continue

            # Lenient path (GENERIC exams): keep MCQ if options present,
            # otherwise treat as a free-form question.
            if (q.get("format") or "").lower() == "mcq":
                q["options"] = opts
                try:
                    co = int(q.get("correct_option", 0))
                except (TypeError, ValueError):
                    co = 0
                q["correct_option"] = co if 0 <= co < len(opts) else 0
            else:
                q.pop("options", None)
                q.pop("correct_option", None)
            kept.append(q)
        s["questions"] = kept
    return [q for s in sections for q in s.get("questions", [])]


def _truncate_sections(sections: list, total: int) -> int:
    """Keep the first `total` questions across `sections`, in place.

    Returns how many were dropped. Batches over-produce, and something has to
    cut them back; the only thing that matters is that this runs AFTER the
    dedupe pass, so what gets cut is surplus rather than the unique question a
    duplicate happened to be sitting in front of.
    """
    if total <= 0:
        return 0
    kept = 0
    dropped = 0
    for s in sections or []:
        questions = s.get("questions") or []
        room = max(0, total - kept)
        if len(questions) > room:
            dropped += len(questions) - room
            s["questions"] = questions[:room]
        kept += len(s.get("questions") or [])
    return dropped


async def _generate_one_batch(
    exam, exam_type: str, batch_topics: list[str], batch_count: int,
    batch_difficulty: str, batch_duration: int, anchors: list,
    rag_dist: dict, format_distribution: dict, custom_instructions: str,
    batch_index: int, batch_total: int,
) -> dict:
    """Single LLM call producing up to ~batch_count questions."""
    batch_instr = custom_instructions
    if batch_total > 1:
        batch_instr = (
            (custom_instructions + "\n\n" if custom_instructions else "")
            + f"This is batch {batch_index + 1} of {batch_total} for the full "
            f"paper. Generate EXACTLY {batch_count} ORIGINAL questions in this "
            "batch, covering ONLY the topics listed for it -- the other topics "
            "belong to other batches and are being written in parallel. Vary "
            "question style so subsequent batches don't repeat your phrasing."
        )
    prompt = competitive_qgen_prompt(
        exam_name=exam.get("name", "Competitive Exam"),
        topics=batch_topics,
        difficulty=batch_difficulty,
        question_count=batch_count,
        duration=batch_duration,
        anchors=anchors,
        rag_dist=rag_dist,
        format_distribution=format_distribution,
        custom_instructions=batch_instr,
        exam_type=exam_type,
    )
    raw = await chat_complete(
        system_message=competitive_system_for_exam(exam_type),
        user_text=prompt,
        provider_chain=chain_for_exam(exam_type),
    )
    return parse_json_response(raw)


async def generate_missing_competitive_questions(paper_id: str, count: int) -> None:
    """Top up a competitive paper that came back short.

    Called by `workers.generate_missing_questions_background` when the paper is
    flagged `is_competitive`. Lives here so the RAG anchor retrieval stays with
    the rest of the competitive path.

    The batch plan is rebuilt for the shortfall only, and the existing questions
    are passed to the model as "already asked" so a top-up doesn't hand back
    what the paper already has. Existing questions are never modified.
    """
    try:
        p = await db.papers.find_one(
            {"id": paper_id, "is_deleted": False}, {"_id": 0}
        )
        if not p:
            return
        exam = await db.competitive_exams.find_one(
            {"id": p.get("competitive_exam_id")}, {"_id": 0}
        )
        if not exam:
            raise RuntimeError("The exam this paper belongs to no longer exists.")

        exam_type = (exam.get("exam_type") or "GENERIC").upper()
        spec = get_format(exam_type)
        topics = [
            t.get("name") if isinstance(t, dict) else str(t)
            for t in (p.get("topics") or [])
        ]
        topics = [t for t in topics if t]
        difficulty = p.get("difficulty", "medium")
        duration = int(p.get("duration_minutes") or 0)
        fmt_dist = p.get("format_distribution") or {}
        batch_size = spec["batch_size"] if spec else max(count, 30)

        anchors = await _retrieve_anchors(exam, topics)
        rag_dist = difficulty_distribution(anchors)

        existing_sections, section_plan, _ = reconcile_section_plan(
            p.get("sections") or [], p.get("section_plan"), None
        )
        already_asked = flatten_question_texts(existing_sections)
        # Built by prompts.avoid_list_block rather than here. This used to be a
        # second copy of that list with the same two silent caps -- the first 60
        # stems only, each cut at 200 characters -- so on a long paper the model
        # was never shown the questions it was most likely to repeat.
        avoid = "\n\n" + avoid_list_block(already_asked) if already_asked else ""

        num_batches = max(1, (count + batch_size - 1) // batch_size)
        sizes = [count // num_batches] * num_batches
        for i in range(count - sum(sizes)):
            sizes[i] += 1
        batch_topic_sets = _partition_topics(topics, sizes)

        # Same concurrency cap as first-pass generation.
        semaphore = _asyncio.Semaphore(3)

        async def _run(idx: int, n: int):
            async with semaphore:
                return await _generate_one_batch(
                    exam=exam,
                    exam_type=exam_type,
                    batch_topics=batch_topic_sets[idx],
                    batch_count=n,
                    batch_difficulty=difficulty,
                    batch_duration=duration,
                    anchors=anchors,
                    rag_dist=rag_dist,
                    format_distribution=fmt_dist,
                    custom_instructions=(p.get("custom_instructions") or "") + avoid,
                    batch_index=idx,
                    batch_total=num_batches,
                )

        results = await _asyncio.gather(
            *[_run(i, n) for i, n in enumerate(sizes)],
            return_exceptions=True,
        )

        new_questions: list = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Top-up batch failed: {r}")
                continue
            for sec in r.get("sections") or []:
                new_questions.extend(sec.get("questions") or [])

        if not new_questions:
            raise RuntimeError(
                "Every top-up batch failed - the upstream gateway is slow right "
                "now. Try again in a few minutes."
            )

        # Merge into the paper's existing single section so the top-up doesn't
        # create a second heading on an exam-format paper.
        title = (
            existing_sections[0].get("title")
            if existing_sections
            else (spec["label"] if spec else exam.get("name", "Practice Paper"))
        )
        new_sections = [{"title": title, "questions": new_questions}]

        strict_mcq = (
            isinstance(fmt_dist, dict)
            and len(fmt_dist) == 1
            and (fmt_dist.get("mcq") or 0) >= 100
        )
        rejects: list = []
        _normalise_questions(
            new_sections, difficulty, strict_mcq=strict_mcq, rejects=rejects
        )
        await _complete_and_gate(
            new_sections,
            ref=paper_id,
            strict_mcq=strict_mcq,
            exam_type=p.get("exam_type") or "",
            difficulty=difficulty,
            rejects=rejects,
        )
        if not count_questions(new_sections):
            raise RuntimeError("The model returned no usable questions.")

        # Compare against the whole paper, and let only the NEW questions lose.
        await dedupe_sections(new_sections, ref=paper_id, against=existing_sections)
        await repair_sections(new_sections, ref=paper_id)
        pending_count = apply_diagram_policy(new_sections, ref=paper_id)
        for s in new_sections:
            for q in s.get("questions", []):
                q["diagram_status"] = "pending" if q.get("needs_diagram") else "none"

        target = paper_target_count(p)
        before = count_questions(existing_sections)
        merged = merge_topup_sections(existing_sections, new_sections, limit=target)
        merged, section_plan, _ = reconcile_section_plan(merged, section_plan, None)
        added = count_questions(merged) - before

        await _finish_topup(paper_id, merged, added, pending_count, target, section_plan)
        logger.info(
            f"Top-up added {added} question(s) to competitive paper {paper_id}"
        )

        if pending_count > 0:
            await generate_diagrams_background(paper_id, p["owner_id"])
    except Exception as e:  # noqa: BLE001
        logger.exception("Competitive question top-up failed")
        await _fail_topup(paper_id, str(e))


async def _generate_competitive_paper(paper_id: str, req: CompetitivePaperRequest, user_id: str) -> None:
    """Background task that builds + writes a competitive paper using RAG.

    For preset exam_types (JEE_MAINS, NEET, …) the request's question_count,
    duration_minutes, total_marks and format_distribution are OVERRIDDEN
    with the official prescribed format from `exam_formats.EXAM_FORMATS`.

    Large papers (>batch_size questions) are generated in sequential
    batches and merged so we stay under the LLM output-token budget while
    still producing the full prescribed paper.
    """
    # Tagging here covers every concurrent batch below: asyncio.gather copies
    # this context into each sibling task, so _generate_one_batch needs nothing.
    tag("competitive_paper", paper_id)
    anchors: list = []
    rag_dist: dict = {"easy": 0, "medium": 0, "hard": 0}
    exam_type = "GENERIC"
    recovered = False
    instructions = ""
    sections: list = []
    try:
        exam = await db.competitive_exams.find_one({"id": req.exam_id}, {"_id": 0})
        if not exam:
            raise RuntimeError(f"exam {req.exam_id} missing")
        exam_type = (exam.get("exam_type") or "GENERIC").upper()
        # The section and syllabus parts of the box apply here too. Counts
        # deliberately do not: a competitive paper's question count is
        # locked by the exam format, and batches are sized from it.
        spec = await parse_instructions(
            req.custom_instructions or "",
            ref=paper_id,
            context={"exam_type": exam_type, "difficulty": req.difficulty},
        )
        spec["total_questions"] = 0
        spec["format_counts"] = {}
        anchors = await _retrieve_anchors(exam, req.topics)
        rag_dist = difficulty_distribution(anchors)

        spec = get_format(exam_type)
        if spec:
            # Locked official format — override any client-side numbers.
            total_q = spec["question_count"]
            duration = spec["duration_minutes"]
            fmt_dist = dict(spec["format_distribution"])
            batch_size = spec["batch_size"]
        else:
            total_q = req.question_count
            duration = req.duration_minutes
            fmt_dist = req.format_distribution
            batch_size = max(req.question_count, 30)  # GENERIC: single batch

        # Compute batch plan (each batch ≤ batch_size).
        num_batches = max(1, (total_q + batch_size - 1) // batch_size)
        sizes = [total_q // num_batches] * num_batches
        for i in range(total_q - sum(sizes)):
            sizes[i] += 1
        batch_topic_sets = _partition_topics(req.topics, sizes)

        # Run batches in parallel (capped concurrency=3 to avoid hammering
        # the LLM gateway and blowing per-tenant rate limits). With per-call
        # timeouts now at 150s a serial NEET 180-q run could take ~15min;
        # 3-way parallelism brings it back to ~5min worst case.
        semaphore = _asyncio.Semaphore(3)

        async def _run(idx: int, count: int):
            async with semaphore:
                return idx, await _generate_one_batch(
                    exam=exam,
                    exam_type=exam_type,
                    batch_topics=batch_topic_sets[idx],
                    batch_count=count,
                    batch_difficulty=req.difficulty,
                    batch_duration=duration,
                    anchors=anchors,
                    rag_dist=rag_dist,
                    format_distribution=fmt_dist,
                    custom_instructions=req.custom_instructions,
                    batch_index=idx,
                    batch_total=num_batches,
                )

        results = await _asyncio.gather(
            *[_run(i, c) for i, c in enumerate(sizes)],
            return_exceptions=True,
        )

        all_questions: list = []
        failed_batches = 0
        for r in results:
            if isinstance(r, Exception):
                failed_batches += 1
                logger.warning(f"Batch failed: {r}")
                continue
            _, data = r
            if not instructions and data.get("instructions"):
                instructions = data["instructions"]
            if data.get("_recovered_from_truncation"):
                recovered = True
            for sec in data.get("sections") or []:
                all_questions.extend(sec.get("questions") or [])

        if not all_questions:
            raise RuntimeError(
                "All LLM providers timed out — the upstream gateway is slow "
                "right now. Click 'Retry generation' below; usually clears "
                "within a few minutes."
            )

        # NOT truncated to total_q here, deliberately. Cutting first meant a
        # duplicate sitting inside the first total_q displaced a unique question
        # that was then thrown away unseen; dedupe dropped the duplicate a moment
        # later and the paper ended up short, which fired a top-up to regenerate
        # what had just been discarded. Over-production is now carried as far as
        # the dedupe pass and cut afterwards, so the questions that survive are
        # the ones that were actually worth keeping.

        # Group into 1 section labelled with the exam (we keep the section
        # blueprint simple; teachers can re-section in the editor if needed).
        sections = [{
            "title": (spec["label"] if spec else exam.get("name", "Practice Paper")),
            "questions": all_questions,
        }]
        # If the locked format is 100% MCQ, drop any rogue non-MCQ questions
        # so users never see broken (no-options) items.
        strict_mcq = (
            isinstance(fmt_dist, dict)
            and len(fmt_dist) == 1
            and (fmt_dist.get("mcq") or 0) >= 100
        )
        rejects: list = []
        _normalise_questions(
            sections, req.difficulty, strict_mcq=strict_mcq, rejects=rejects
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Competitive paper generation failed")
        await db.papers.update_one(
            {"id": paper_id},
            {"$set": {
                "generation_status": "failed",
                "generation_error": str(e)[:500],
            }},
        )
        return

    # Drop repeats before the completion pass, not after: dropping first widens
    # the `room` below, so a duplicate makes space for a strict-MCQ reject that
    # can actually be recovered. Concurrent batches are the main source here --
    # they cannot see each other's output, so only this can.
    topics_weighted = [{"name": t, "weight": 5} for t in (req.topics or [])]
    coverage = measure_coverage(sections, topics_weighted, total_q)
    dedupe_stats = await dedupe_sections(sections, ref=paper_id, coverage=coverage)

    # NOW cut to the prescribed total, with the duplicates already gone -- see
    # the note where the batches are collected. Over-production that survived
    # dedupe is genuine surplus and is what should be trimmed.
    _truncate_sections(sections, total_q)

    # Finish the questions that arrived incomplete, including the ones strict
    # MCQ mode had to reject. Both the dedupe pass and the cut above ran before
    # this, so the paper is short by exactly the number lost and there is room
    # for every one that can be recovered.
    completeness_stats = await _complete_and_gate(
        sections,
        ref=paper_id,
        strict_mcq=strict_mcq,
        exam_type=exam_type,
        difficulty=req.difficulty,
        rejects=rejects,
        room=total_q - count_questions(sections),
    )

    # Re-measure: both passes above changed the counts.
    coverage = measure_coverage(sections, topics_weighted, total_q)

    # Same LaTeX gate as the school path, in the same position: before the
    # diagram policy flattens anything and before generation_status goes ready.
    latex_stats = await repair_sections(sections, ref=paper_id)

    # Figures were never generated for competitive papers even though the model
    # was already flagging them — NEET biology and JEE circuit / organic items
    # genuinely need one. Same gate as the school path: the policy decides what
    # survives, and only what survives is marked pending.
    pending_count = apply_diagram_policy(sections, ref=paper_id)
    for s in sections:
        for q in s.get("questions", []):
            q["diagram_status"] = "pending" if q.get("needs_diagram") else "none"

    spec = get_format(exam_type)
    sections, section_plan, _ = reconcile_section_plan(sections, None, None)
    await db.papers.update_one(
        {"id": paper_id},
        {"$set": {
            "instructions": instructions,
            "sections": sections,
            "section_plan": section_plan,
            "question_count": count_questions(sections),
            "target_question_count": total_q,
            # Previously counted and dropped. A paper that lost batches is short
            # without being truncated, which is the other way a partial paper
            # happens - the top-up banner needs to see it.
            "failed_batches": failed_batches,
            "diagrams_pending": pending_count,
            "generation_status": "ready",
            "generation_error": None,
            "rag_anchors_used": len(anchors),
            "rag_difficulty_distribution": rag_dist,
            "exam_type": exam_type,
            "recovered_from_truncation": recovered,
            "latex_check": latex_stats,
            "completeness_check": completeness_stats,
            "dedupe_check": dedupe_stats,
            "topic_coverage": coverage,
            "instruction_check": verify_instructions(sections, spec),
            "duration_minutes": spec["duration_minutes"] if spec else req.duration_minutes,
            "total_marks": spec["total_marks"] if spec else req.question_count,
            "format_distribution": (
                spec["format_distribution"] if spec else req.format_distribution
            ),
        }},
    )

    # After the paper is marked ready, so the teacher sees questions immediately
    # and the figures fill in behind them — same ordering as the school path.
    if pending_count > 0:
        await generate_diagrams_background(paper_id, user_id)

    # A competitive paper goes short the other way: whole batches fail and
    # are only counted. Same remedy, same single attempt - the worker routes
    # itself back here for the actual generation.
    await _auto_topup_if_short(paper_id, user_id)


@api_router.post("/competitive-exams/{exam_id}/generate-paper")
async def generate_competitive_paper(
    exam_id: str,
    req: CompetitivePaperRequest,
    user: dict = Depends(get_current_user),
):
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Teacher or admin only")
    if req.exam_id != exam_id:
        raise HTTPException(status_code=400, detail="exam_id mismatch in body")
    exam = await _owned_exam(exam_id, user)

    paper_id = str(uuid.uuid4())
    # Lock to official format spec when exam_type is a known preset.
    spec = get_format((exam.get("exam_type") or "GENERIC").upper())
    if spec:
        locked_duration = spec["duration_minutes"]
        locked_total_marks = spec["total_marks"]
        locked_fmt = dict(spec["format_distribution"])
    else:
        locked_duration = req.duration_minutes
        locked_total_marks = req.question_count  # 1 mark per MCQ default
        locked_fmt = {
            k: int(v) for k, v in (req.format_distribution or {}).items() if int(v) > 0
        }
    paper_doc = {
        "id": paper_id,
        "owner_id": user["id"],
        "title": req.title,
        "subject": exam.get("name", "Competitive"),
        "class_name": "Competitive",
        "textbook_id": None,
        "textbook_ids": [],
        "topics": [{"name": t, "weight": 5} for t in req.topics],
        "difficulty": req.difficulty,
        "duration_minutes": locked_duration,
        "total_marks": locked_total_marks,
        "distribution": {"information": 30, "concept": 40, "application": 30},
        "format_distribution": locked_fmt,
        "custom_instructions": req.custom_instructions,
        "section_blueprint": "",
        # The prescribed count for a preset exam, else what was asked for. Same
        # value _generate_competitive_paper batches against, stored so a short
        # paper can tell how short it is.
        "target_question_count": (
            spec["question_count"] if spec else req.question_count
        ),
        "instructions": "",
        "sections": [],
        "diagrams_pending": 0,
        "generation_status": "queued",
        "generation_revision": 1,
        "generation_error": None,
        "is_competitive": True,
        "competitive_exam_id": exam_id,
        "created_at": utcnow_iso(),
        "is_deleted": False,
    }
    await db.papers.insert_one(paper_doc)

    from paper_review_queue import enqueue_generation
    await enqueue_generation(paper_id=paper_id, payload={"competitive_request": req.model_dump()})
    paper_doc.pop("_id", None)
    return paper_doc


@api_router.get("/competitive-exams/{exam_id}/rag-preview")
async def rag_preview(
    exam_id: str,
    topic: str,
    k: int = 5,
    user: dict = Depends(get_current_user),
):
    """Preview what anchors RAG would retrieve for a topic. Used by the UI
    to show teachers what calibration the LLM will see."""
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Teacher or admin only")
    # This route returns the literal text of ingested questions and used to
    # authorise nothing at all -- any teacher holding an exam id could read
    # another account's uploaded paper straight out of it.
    exam = await _owned_exam(exam_id, user)
    corpus = await db.past_questions.find(
        _docs_of(exam),
        {"_id": 0, "id": 1, "text": 1, "topic": 1, "difficulty": 1, "marks": 1},
    ).to_list(5000)
    hits = top_k_similar(topic, corpus, k=k)
    return {"anchors": hits, "distribution": difficulty_distribution(hits)}
