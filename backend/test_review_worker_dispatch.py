"""Worker scheduling tests: no real Firestore or provider calls."""
import asyncio
from collections import deque

import paper_review_worker as worker


class Wakeup:
    instance = None

    def __init__(self, *args, **kwargs):
        self.event = asyncio.Event()
        self.waiting = asyncio.Event()
        self.closed = False
        Wakeup.instance = self

    async def connect(self):
        pass

    async def wait(self):
        self.waiting.set()
        await self.event.wait()

    async def close(self):
        self.closed = True


def test_idle_arrival_and_capacity_release_dispatch_without_timer(monkeypatch):
    async def scenario():
        jobs = deque()
        started = []
        releases = {}
        seen = asyncio.Event()
        calls = []
        async def claim(owner, kinds):
            calls.append(kinds)
            for job in list(jobs):
                if job["kind"] in kinds:
                    jobs.remove(job)
                    return job
            return None
        async def process(job, owner):
            started.append(job["id"])
            seen.set()
            await releases[job["id"]].wait()
        monkeypatch.setattr(worker, "QueueWakeup", Wakeup)
        monkeypatch.setattr(worker, "claim_next", claim)
        monkeypatch.setattr(worker, "process_job", process)
        monkeypatch.setattr(worker, "MAX_CONCURRENCY", 2)
        runner = asyncio.create_task(worker.run())
        try:
            await asyncio.sleep(0)
            wake = Wakeup.instance
            await asyncio.wait_for(wake.waiting.wait(), timeout=1)
            idle_calls = len(calls)
            await asyncio.sleep(.02)
            assert len(calls) == idle_calls  # no idle query loop
            for number in range(3):
                job_id = str(number)
                releases[job_id] = asyncio.Event()
                jobs.append({"id": job_id, "kind": "paper_review"})
            wake.event.set()  # listener signal
            while len(started) < 2:
                seen.clear()
                await asyncio.wait_for(seen.wait(), timeout=1)
            assert started == ["0", "1"]
            assert len(jobs) == 1  # concurrency preserved
            seen.clear()
            releases["0"].set()  # completion signal must drain pending work
            await asyncio.wait_for(seen.wait(), timeout=1)
            assert started == ["0", "1", "2"]
        finally:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        assert Wakeup.instance.closed
    asyncio.run(scenario())


def test_failed_claim_retries_instead_of_terminating_worker(monkeypatch):
    async def scenario():
        called = asyncio.Event()
        count = 0
        async def claim(owner, kinds):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("index temporarily unavailable")
            called.set()
            return None
        monkeypatch.setattr(worker, "QueueWakeup", Wakeup)
        monkeypatch.setattr(worker, "claim_next", claim)
        monkeypatch.setattr(worker, "POLL_SECONDS", .01)
        runner = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(called.wait(), timeout=1)
            assert not runner.done()
        finally:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
    asyncio.run(scenario())
