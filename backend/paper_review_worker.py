"""Process durable paper-generation jobs outside the FastAPI request process."""
from __future__ import annotations

import asyncio
import os
import socket

from deps import db, logger, utcnow_iso
from paper_review_queue import claim_next, complete, heartbeat, retry_or_needs_review, enqueue_diagrams
from workers import generate_paper_background, generate_diagrams_background

POLL_SECONDS = float(os.environ.get("PAPER_REVIEW_POLL_SECONDS", "1.5"))
HEARTBEAT_SECONDS = float(os.environ.get("PAPER_REVIEW_HEARTBEAT_SECONDS", "20"))
MAX_CONCURRENCY = max(1, int(os.environ.get("PAPER_REVIEW_CONCURRENCY", "2")))
DIAGRAM_JOB_CONCURRENCY = 1


async def _keep_lease(job_id: str, worker_id: str, lease_token: str | None = None) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if not await heartbeat(job_id, worker_id, lease_token):
            return


async def process_job(job: dict, worker_id: str) -> None:
    from firestore_compat import worker_write_lease
    snapshots = await db.paper_review_jobs._matching_snapshots({"id": job["id"]})
    if not snapshots:
        return
    token = worker_write_lease.set({"job": job, "reference": snapshots[0][0].reference})
    try:
        await _process_job(job, worker_id)
    finally:
        worker_write_lease.reset(token)


async def _process_job(job: dict, worker_id: str) -> None:
    paper_id = job["paper_id"]
    paper = await db.papers.find_one({"id": paper_id}, {"_id": 0})
    if not paper or paper.get("generation_status") == "cancelled":
        await complete(job, status="cancelled", stage="cancelled")
        return
    if int(paper.get("generation_revision") or 1) != int(job.get("revision") or 1):
        await complete(job, status="superseded", stage="superseded")
        return
    if job.get("engine_version") == "v2":
        from paper_completion import run_completion
        from completion_store import OwnershipLost
        lease = asyncio.create_task(_keep_lease(job["id"], worker_id, job.get("lease_token")))
        try:
            await run_completion(job)
            current = await db.papers.find_one({"id": paper_id}, {"_id": 0})
            ready = (current or {}).get("completion", {}).get("ready", False)
            await complete(job, status="completed" if ready else "needs_review", stage="ready" if ready else "incomplete")
        except OwnershipLost:
            logger.info("Completion ownership lost for %s", paper_id)
        except Exception as exc:
            logger.exception("Completion failed for %s", paper_id)
            await retry_or_needs_review(job, str(exc)[:500])
        finally:
            lease.cancel()
            try:
                await lease
            except asyncio.CancelledError:
                pass
        return
    if job.get("kind") == "paper_diagram":
        if paper.get("generation_status") != "ready":
            await complete(job, status="cancelled", stage="not_ready")
            return
        lease = asyncio.create_task(_keep_lease(job["id"], worker_id, job.get("lease_token")))
        try:
            await generate_diagrams_background(paper_id, paper.get("owner_id", ""))
            await complete(job, status="completed", stage="ready")
        except Exception as exc:
            logger.exception("diagram job %s failed", job["id"])
            await retry_or_needs_review(job, str(exc)[:500])
        finally:
            lease.cancel()
            try:
                await lease
            except asyncio.CancelledError:
                pass
        return
    await db.papers.update_one({"id": paper_id, "generation_status": {"$in": ["queued", "pending", "generating", "reviewing", "needs_review"]}}, {"$set": {
        "generation_status": "generating", "generation_progress.status": "pending",
        "generation_progress.phase": "generating", "generation_progress.updated_at": utcnow_iso(),
        "quality_review.status": "running", "quality_review.stage": "generating",
        "quality_review.job_id": job["id"], "quality_review.started_at": utcnow_iso(),
    }})
    lease = asyncio.create_task(_keep_lease(job["id"], worker_id, job.get("lease_token")))
    try:
        payload = dict(job["payload"])
        # Blueprint requirements can contain nested arrays, which Firestore
        # rejects in an arbitrary job payload.  They already live safely on
        # the paper document through firestore_compat's dedicated encoder.
        payload.setdefault("saved_requirements", paper.get("generation_requirements"))
        if payload.get("competitive_request"):
            from competitive_routes import _generate_competitive_paper, CompetitivePaperRequest
            await _generate_competitive_paper(paper_id, CompetitivePaperRequest(**payload["competitive_request"]), paper["owner_id"])
        else:
            await generate_paper_background(**payload)
        current = await db.papers.find_one({"id": paper_id}, {"_id": 0, "generation_status": 1, "generation_error": 1, "diagrams_pending": 1, "quality_review": 1})
        review_meta = (current or {}).get("quality_review") or {}
        budget = review_meta.get("budget") or {}
        await db.paper_review_jobs.update_one({"id": job["id"], "status": "leased", "worker_id": worker_id, "lease_token": job.get("lease_token")}, {"$set": {
            "provider_calls_used": int(budget.get("provider_calls_used") or 0),
            "provider_calls_limit": int(budget.get("provider_calls_limit") or 12),
            "failed_gates": list(review_meta.get("failed_gates") or []),
        }})
        if current and current.get("generation_status") == "ready":
            # The generator owns the actual quality verdict. A ready draft can
            # be viewable while targeted repairs continue; never turn that
            # in-progress or failed review into a pass just because its worker
            # returned normally.
            if review_meta.get("status") == "running":
                await db.papers.update_one({"id": paper_id, "generation_status": "ready"}, {"$set": {
                    "quality_review.status": "needs_review",
                    "quality_review.stage": "needs_review",
                    "quality_review.message": "The draft is available, but its final quality checks did not finish.",
                    "quality_review.finished_at": utcnow_iso(),
                }})
            await complete(job, status="completed", stage="ready")
            if int(current.get("diagrams_pending") or 0) > 0:
                await enqueue_diagrams(
                    paper_id=paper_id, owner_id=paper.get("owner_id", ""),
                    revision=int(job.get("revision") or 1),
                )
            return
        message = (current or {}).get("generation_error") or "Quality review did not produce a publishable paper."
        # Validation failures are terminal and user-actionable. Regenerating
        # automatically was the main source of repeated provider spend.
        await complete(job, status="needs_review", stage="needs_review", diagnostics=[str(message)[:500]])
        await db.papers.update_one({"id": paper_id}, {"$set": {
            "generation_status": "needs_review", "quality_review.status": "needs_review",
            "quality_review.stage": "needs_review", "quality_review.message": str(message)[:500],
            "quality_review.finished_at": utcnow_iso(),
        }})
    except Exception as exc:  # never leave an unowned pending paper
        logger.exception("paper review job %s failed", job["id"])
        # A post-publish side effect (for example activity logging) can fail
        # after the paper was atomically made ready.  Do not rerun and replace
        # a valid published paper in that case.
        current = await db.papers.find_one({"id": paper_id}, {"_id": 0, "generation_status": 1, "quality_review": 1})
        if current and current.get("generation_status") == "ready":
            await db.papers.update_one({"id": paper_id, "generation_status": "ready"}, {"$set": {
                "quality_review.status": "needs_review",
                "quality_review.stage": "needs_review",
                "quality_review.message": str(exc)[:500],
                "quality_review.finished_at": utcnow_iso(),
            }})
            await complete(job, status="completed", stage="ready")
            return
        result = await retry_or_needs_review(job, str(exc)[:500])
        if result == "needs_review":
            await db.papers.update_one({"id": paper_id}, {"$set": {
                "generation_status": "needs_review", "generation_error": None,
                "quality_review.status": "needs_review", "quality_review.stage": "needs_review",
                "quality_review.message": str(exc)[:500], "quality_review.finished_at": utcnow_iso(),
            }})
    finally:
        lease.cancel()
        try:
            await lease
        except asyncio.CancelledError:
            pass


async def run() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info("paper review worker started as %s", worker_id)
    active: dict[asyncio.Task, str] = {}
    while True:
        active = {task: kind for task, kind in active.items() if not task.done()}
        if len(active) >= MAX_CONCURRENCY:
            await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            continue
        # A diagram can never consume the last review-worker slot. Quality
        # jobs always win, then one non-blocking diagram may use spare room.
        job = await claim_next(worker_id, ["paper_review", "paper_generation"])
        if job is None and sum(1 for kind in active.values() if kind == "paper_diagram") < DIAGRAM_JOB_CONCURRENCY:
            job = await claim_next(worker_id, ["paper_diagram"])
        if job is None:
            await asyncio.sleep(POLL_SECONDS)
            continue
        task = asyncio.create_task(process_job(job, worker_id))
        active[task] = job.get("kind", "paper_review")


if __name__ == "__main__":
    asyncio.run(run())
