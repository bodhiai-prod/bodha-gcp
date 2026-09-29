"""Process durable paper-generation jobs outside the FastAPI request process."""
from __future__ import annotations

import asyncio
import os
import socket

from deps import db, logger, utcnow_iso
from paper_review_queue import claim_next, complete, heartbeat, retry_or_needs_review
from workers import generate_paper_background

POLL_SECONDS = float(os.environ.get("PAPER_REVIEW_POLL_SECONDS", "1.5"))
HEARTBEAT_SECONDS = float(os.environ.get("PAPER_REVIEW_HEARTBEAT_SECONDS", "20"))
MAX_CONCURRENCY = max(1, int(os.environ.get("PAPER_REVIEW_CONCURRENCY", "2")))


async def _keep_lease(job_id: str, worker_id: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if not await heartbeat(job_id, worker_id):
            return


async def process_job(job: dict, worker_id: str) -> None:
    paper_id = job["paper_id"]
    paper = await db.papers.find_one({"id": paper_id}, {"_id": 0})
    if not paper or paper.get("generation_status") == "cancelled":
        await complete(job, status="cancelled", stage="cancelled")
        return
    if int(paper.get("generation_revision") or 1) != int(job.get("revision") or 1):
        await complete(job, status="superseded", stage="superseded")
        return
    await db.papers.update_one({"id": paper_id, "generation_status": {"$in": ["queued", "pending", "generating", "reviewing", "needs_review"]}}, {"$set": {
        "generation_status": "generating", "generation_progress.status": "pending",
        "generation_progress.phase": "generating", "generation_progress.updated_at": utcnow_iso(),
        "quality_review.status": "running", "quality_review.stage": "generating",
        "quality_review.job_id": job["id"], "quality_review.started_at": utcnow_iso(),
    }})
    lease = asyncio.create_task(_keep_lease(job["id"], worker_id))
    try:
        payload = dict(job["payload"])
        # Blueprint requirements can contain nested arrays, which Firestore
        # rejects in an arbitrary job payload.  They already live safely on
        # the paper document through firestore_compat's dedicated encoder.
        payload.setdefault("saved_requirements", paper.get("generation_requirements"))
        await generate_paper_background(**payload)
        current = await db.papers.find_one({"id": paper_id}, {"_id": 0, "generation_status": 1, "generation_error": 1})
        if current and current.get("generation_status") == "ready":
            await db.papers.update_one({"id": paper_id, "generation_status": "ready"}, {"$set": {
                "quality_review.status": "passed", "quality_review.stage": "ready",
                "quality_review.finished_at": utcnow_iso(),
            }})
            await complete(job, status="completed", stage="ready")
            return
        message = (current or {}).get("generation_error") or "Quality review did not produce a publishable paper."
        result = await retry_or_needs_review(job, str(message)[:500])
        if result == "needs_review":
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
        current = await db.papers.find_one({"id": paper_id}, {"_id": 0, "generation_status": 1})
        if current and current.get("generation_status") == "ready":
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
    active: set[asyncio.Task] = set()
    while True:
        active = {task for task in active if not task.done()}
        if len(active) >= MAX_CONCURRENCY:
            await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            continue
        job = await claim_next(worker_id)
        if job is None:
            await asyncio.sleep(POLL_SECONDS)
            continue
        active.add(asyncio.create_task(process_job(job, worker_id)))


if __name__ == "__main__":
    asyncio.run(run())
