import asyncio
from types import SimpleNamespace

import firestore_compat
from firestore_compat import FirestoreCollection


class NativeQuery:
    def __init__(self, rows, operations=()):
        self.rows, self.operations = rows, operations

    def where(self, *, filter):
        return NativeQuery(self.rows, (*self.operations, ("where", filter.field_path, filter.op_string, filter.value)))

    def order_by(self, field):
        return NativeQuery(self.rows, (*self.operations, ("order", field)))

    def limit(self, count):
        return NativeQuery(self.rows, (*self.operations, ("limit", count)))

    async def stream(self):
        assert any(op[0] == "limit" for op in self.operations), "Unbounded queue discovery"
        for row in self.rows(self.operations):
            yield row


def collection_with_native(rows):
    collection = object.__new__(FirestoreCollection)
    collection._name = "paper_review_jobs"
    collection._collection = NativeQuery(rows)
    return collection


def test_discovery_uses_indexed_kind_status_time_and_limit_without_scan():
    async def scenario():
        calls = []
        collection = collection_with_native(lambda ops: calls.append(ops) or [])
        async def claim(query, fields, *, candidates):
            assert candidates == []
        collection.claim_one = claim
        async def forbidden(*args):
            raise AssertionError("collection history scan")
        collection._matching_snapshots = forbidden
        assert await collection.claim_due_job(["paper_review", "paper_generation"], "2026-10-13", {}) is None
        assert len(calls) == 2
        assert ("where", "status", "==", "leased") in calls[0]
        assert ("where", "lease_until", "<", "2026-10-13") in calls[0]
        assert ("where", "next_run_at", "<=", "2026-10-13") in calls[1]
        for ops in calls:
            assert ("where", "kind", "in", ["paper_review", "paper_generation"]) in ops
            assert ("limit", 8) in ops
    asyncio.run(scenario())


def test_missing_index_is_reported_without_scan_fallback():
    async def scenario():
        def rows(ops):
            raise RuntimeError("missing index")
        collection = collection_with_native(rows)
        try:
            await collection.claim_due_job(["paper_review"], "2026", {})
        except RuntimeError as exc:
            assert str(exc) == "missing index"
        else:
            raise AssertionError("index error hidden")
    asyncio.run(scenario())


def test_lease_updates_push_id_filter_down_but_recheck_time():
    async def scenario():
        collection = object.__new__(FirestoreCollection)
        collection._name = "paper_review_jobs"
        queries = []
        snapshot = SimpleNamespace(to_dict=lambda: {"id": "j1", "status": "leased", "lease_until": "2000"})
        class Query:
            async def stream(self):
                yield snapshot
        def native(query):
            queries.append(query)
            return None if "lease_until" in query else Query()
        collection._native_query = native
        assert await collection._matching_snapshots({"id": "j1", "status": "leased", "lease_until": {"$gt": "2026"}}) == []
        assert queries[-1] == {"id": "j1", "status": "leased"}
    asyncio.run(scenario())


def test_concurrent_claims_and_stale_candidates_have_one_owner(monkeypatch):
    async def scenario():
        current = {"id": "j1", "status": "queued", "kind": "paper_review", "next_run_at": "2026"}
        lock = asyncio.Lock()
        class Reference:
            async def get(self, *, transaction):
                return SimpleNamespace(exists=True, to_dict=lambda: dict(current))
        class Transaction:
            def set(self, reference, value):
                current.update(value)
        def transactional(func):
            async def wrapped(txn):
                async with lock:
                    return await func(txn)
            return wrapped
        monkeypatch.setattr(firestore_compat.firestore, "async_transactional", transactional)
        collection = object.__new__(FirestoreCollection)
        collection._name = "paper_review_jobs"
        collection._client = SimpleNamespace(transaction=Transaction)
        candidates = [(SimpleNamespace(reference=Reference()), dict(current))]
        predicate = {"status": "queued", "next_run_at": {"$lte": "2026"}}
        results = await asyncio.gather(*(collection.claim_one(predicate, {"status": "leased", "worker_id": owner}, candidates=candidates)
                                         for owner in ("worker1", "worker2")))
        assert sum(result is not None for result in results) == 1
        assert current["status"] == "leased"
        # A renewed lease must not be reclaimed from an old query snapshot.
        current["lease_until"] = "2030"
        assert await collection.claim_one({"status": "leased", "lease_until": {"$lt": "2026"}},
                                          {"worker_id": "stale"}, candidates=candidates) is None
    asyncio.run(scenario())


def test_repeated_enqueue_preserves_existing_leased_job():
    async def scenario():
        class Reference:
            async def get(self):
                return SimpleNamespace(exists=True)
            async def create(self, value):
                raise AssertionError("Existing lease must not be overwritten")
        collection = object.__new__(FirestoreCollection)
        collection._collection = SimpleNamespace(document=lambda key: Reference())
        await collection.insert_once("existing", {"status": "queued"})
    asyncio.run(scenario())


def test_enqueue_retries_transient_abort_with_same_document_id():
    from google.api_core.exceptions import Aborted
    async def scenario():
        calls = []
        class Reference:
            async def get(self):
                return SimpleNamespace(exists=False)
            async def create(self, value):
                calls.append(value)
                if len(calls) == 1:
                    raise Aborted("transaction contention")
        collection = object.__new__(FirestoreCollection)
        collection._collection = SimpleNamespace(document=lambda key: Reference())
        await collection.insert_once("new", {"status": "queued"})
        assert calls == [{"status": "queued"}, {"status": "queued"}]
    asyncio.run(scenario())
