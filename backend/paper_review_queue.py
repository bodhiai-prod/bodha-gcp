"""Durable Firestore queue for paper generation and quality review.

The HTTP process only records work.  A separate worker leases these records so
an application restart cannot strand a paper in a spinner.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from deps import db, utcnow_iso

LEASE_SECONDS = int(os.environ.get("PAPER_REVIEW_LEASE_SECONDS", "90"))
MAX_ATTEMPTS = int(os.environ.get("PAPER_REVIEW_MAX_ATTEMPTS", "3"))


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def generation_job(*, paper_id: str, payload: dict[str, Any], revision: int = 1) -> dict[str, Any]:
    now = utcnow_iso()
    return {
        "id": str(uuid.uuid4()), "kind": "paper_generation", "paper_id": paper_id,
        "revision": revision, "payload": payload, "status": "queued", "stage": "queued",
        "attempt": 0, "max_attempts": MAX_ATTEMPTS, "lease_until": None,
        "heartbeat_at": None, "queued_at": now, "started_at": None, "finished_at": None,
        "next_run_at": now, "diagnostics": [],
    }


async def enqueue_generation(*, paper_id: str, payload: dict[str, Any], revision: int = 1) -> dict[str, Any]:
    job = generation_job(paper_id=paper_id, payload=payload, revision=revision)
    await db.paper_review_jobs.insert_one(job)
    return job


async def claim_next(worker_id: str) -> dict[str, Any] | None:
    """Lease one due job.

    ``claim_one`` is implemented by the Firestore adapter as a transaction;
    keeping the queue protocol there makes every worker use the same atomic
    status transition.
    """
    return await db.paper_review_jobs.claim_one(
        {
            "kind": "paper_generation",
            "$or": [
                {"status": "queued", "next_run_at": {"$lte": utcnow_iso()}},
                {"status": "leased", "lease_until": {"$lt": utcnow_iso()}},
            ],
        },
        {"status": "leased", "worker_id": worker_id, "lease_until": _after(LEASE_SECONDS),
         "heartbeat_at": utcnow_iso(), "stage": "generating", "started_at": utcnow_iso()},
    )


async def heartbeat(job_id: str, worker_id: str) -> bool:
    result = await db.paper_review_jobs.update_one(
        {"id": job_id, "status": "leased", "worker_id": worker_id},
        {"$set": {"heartbeat_at": utcnow_iso(), "lease_until": _after(LEASE_SECONDS)}},
    )
    return bool(result.modified_count)


async def complete(job: dict[str, Any], *, status: str, stage: str, diagnostics: list[str] | None = None) -> None:
    await db.paper_review_jobs.update_one(
        {"id": job["id"], "status": "leased"},
        {"$set": {"status": status, "stage": stage, "finished_at": utcnow_iso(),
                  "lease_until": None, "diagnostics": diagnostics or []}},
    )


async def retry_or_needs_review(job: dict[str, Any], message: str) -> str:
    attempt = int(job.get("attempt") or 0) + 1
    if attempt >= int(job.get("max_attempts") or MAX_ATTEMPTS):
        await complete(job, status="needs_review", stage="needs_review", diagnostics=[message])
        return "needs_review"
    await db.paper_review_jobs.update_one(
        {"id": job["id"], "status": "leased"},
        {"$set": {"status": "queued", "stage": "retrying", "attempt": attempt,
                  "lease_until": None, "next_run_at": _after(2 ** attempt),
                  "diagnostics": [message]}},
    )
    return "queued"
