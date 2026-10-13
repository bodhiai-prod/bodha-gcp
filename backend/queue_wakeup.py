"""Firestore changes wake the worker; timers cover retries and dead owners.

The synchronous SDK owns its watch thread. Only schedule event-loop work from
that thread; the listener never claims or runs jobs. The durable queue remains
the authority, including when the listener is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger("bodhiai.queue_wakeup")


class QueueWakeup:
    def __init__(self, project: str, database: str, *, recovery_seconds=60, fallback_seconds=20, enabled=True):
        self.project, self.database = project, database
        self.recovery_seconds = max(1, float(recovery_seconds))
        self.fallback_seconds = max(1, float(fallback_seconds))
        self.event = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.client = None
        self.watch = None
        self.due_times: list[float] = []
        self.closed = False
        self.enabled = enabled

    def _snapshot(self, documents, changes, read_time):
        times = []
        for document in documents:
            value = (document.to_dict() or {}).get("next_run_at")
            if value:
                try:
                    times.append(datetime.fromisoformat(value).timestamp())
                except (ValueError, TypeError):
                    logger.warning("Invalid next_run_at on queue document %s", document.id)
        try:
            self.loop.call_soon_threadsafe(self._notify, times)
        except RuntimeError:
            # Shutdown can race with the SDK's final callback.
            pass

    def _notify(self, times):
        if not self.closed:
            self.due_times = times
            self.event.set()

    async def connect(self):
        if self.closed or not self.enabled or (self.watch is not None and self.watch.is_active):
            return
        try:
            if self.watch is not None:
                await asyncio.to_thread(self.watch.unsubscribe)
                self.watch = None
            if self.client is None:
                self.client = firestore.Client(project=self.project, database=self.database)
            query = self.client.collection("paper_review_jobs").where(
                filter=FieldFilter("status", "==", "queued")
            )
            self.watch = query.on_snapshot(self._snapshot)
            logger.info("Queue listener connected; recovery sweep every %ss", self.recovery_seconds)
        except Exception:
            logger.exception("Queue listener unavailable; indexed fallback every %ss", self.fallback_seconds)

    async def wait(self):
        healthy = self.watch is not None and self.watch.is_active
        timeout = self.recovery_seconds if healthy else self.fallback_seconds
        now = datetime.now(timezone.utc).timestamp()
        # A queued retry does not change when its time arrives. Schedule a
        # local wake then; already-due records must not create a busy loop.
        future = [due - now for due in self.due_times if due > now]
        if future:
            timeout = min(timeout, min(future))
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def close(self):
        self.closed = True
        if self.watch is not None:
            await asyncio.to_thread(self.watch.unsubscribe)
        if self.client is not None:
            self.client.close()
