import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from queue_wakeup import QueueWakeup


def test_arrival_wakes_wait_without_fallback_poll():
    async def scenario():
        wakeup = QueueWakeup("test", "(default)", fallback_seconds=60)
        waiter = asyncio.create_task(wakeup.wait())
        await asyncio.sleep(0)
        # Exercise the real SDK-thread callback handoff.
        snapshot = SimpleNamespace(id="job", to_dict=lambda: {"next_run_at": datetime.now(timezone.utc).isoformat()})
        await asyncio.to_thread(wakeup._snapshot, [snapshot], [], None)
        await asyncio.wait_for(waiter, timeout=1)
    asyncio.run(scenario())


def test_delayed_retry_wakes_at_due_time_without_document_change():
    async def scenario():
        wakeup = QueueWakeup("test", "(default)", recovery_seconds=60)
        wakeup.watch = SimpleNamespace(is_active=True)
        wakeup.due_times = [(datetime.now(timezone.utc) + timedelta(seconds=.03)).timestamp()]
        await asyncio.wait_for(wakeup.wait(), timeout=1)
    asyncio.run(scenario())


def test_overdue_records_do_not_cause_busy_loop(monkeypatch):
    async def scenario():
        wakeup = QueueWakeup("test", "(default)", recovery_seconds=60)
        wakeup.watch = SimpleNamespace(is_active=True)
        wakeup.due_times = [0]
        observed = []
        async def wait_for(awaitable, *, timeout):
            observed.append(timeout)
            awaitable.close()
            raise asyncio.TimeoutError
        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        await wakeup.wait()
        assert observed == [60]
    asyncio.run(scenario())


def test_listener_failure_uses_bounded_fallback_and_reconnects(monkeypatch):
    async def scenario():
        wakeup = QueueWakeup("test", "(default)", fallback_seconds=20)
        query = SimpleNamespace(on_snapshot=lambda callback: SimpleNamespace(is_active=True))
        collection = SimpleNamespace(where=lambda **kwargs: query)
        client = SimpleNamespace(collection=lambda name: collection)
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("disconnected")
            return client
        monkeypatch.setattr("queue_wakeup.firestore.Client", create)
        await wakeup.connect()
        assert wakeup.watch is None
        observed = []
        async def wait_for(awaitable, *, timeout):
            observed.append(timeout)
            awaitable.close()
            raise asyncio.TimeoutError
        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        await wakeup.wait()
        assert observed == [20]
        await wakeup.connect()
        assert wakeup.watch.is_active
        assert len(calls) == 2
    asyncio.run(scenario())


def test_shutdown_ignores_late_listener_callback():
    async def scenario():
        wakeup = QueueWakeup("test", "(default)")
        await wakeup.close()
        wakeup._notify([123])
        assert not wakeup.event.is_set()
    asyncio.run(scenario())
