import asyncio

import firestore_compat
from firestore_compat import (
    FirestoreCollection,
    _decode_generation_requirements,
    _encode_generation_requirements,
    _get,
    _matches,
    _project,
    _set,
)


def test_matches_existing_route_query_operators_and_nested_fields():
    document = {
        "id": "paper-1",
        "owner_id": "teacher-1",
        "is_deleted": False,
        "solution": {"status": "pending", "completed": 1},
    }

    assert _matches(document, {"id": {"$in": ["paper-1", "paper-2"]}})
    assert _matches(document, {"is_deleted": {"$ne": True}})
    assert _matches(document, {"solution.status": {"$in": ["pending", "generating"]}})
    assert _matches(document, {"solution.completed": {"$gt": 0}})
    assert _matches(document, {"$or": [{"owner_id": "other"}, {"owner_id": "teacher-1"}]})
    assert not _matches(document, {"solution.status": "complete"})


def test_projection_and_dotted_update_preserve_response_shape():
    document = {"id": "paper-1", "chunks": ["private"], "solution": {"status": "pending"}}

    assert _project(document, {"_id": 0, "chunks": 0}) == {
        "id": "paper-1",
        "solution": {"status": "pending"},
    }
    _set(document, "solution.status", "complete")
    assert _get(document, "solution.status") == "complete"


def test_generation_requirements_are_firestore_safe_and_round_trip():
    document = {
        "id": "paper-1",
        "generation_requirements": {
            "blueprint": {
                "disjoint_section_pairs": [["B", "C"]],
                "sections": [{
                    "id": "A",
                    "groups": [{
                        "id": "q1",
                        "slots": [
                            {"id": "q1-i", "marks": 1},
                            {"id": "q1-ii", "marks": 1},
                        ],
                    }],
                }],
            },
        },
    }

    encoded = _encode_generation_requirements(document)
    blueprint = encoded["generation_requirements"]["blueprint"]
    assert blueprint["disjoint_section_pairs"] == [{"first": "B", "second": "C"}]
    assert blueprint["sections"][0]["groups"][0]["slots"] == {
        "q1-i": {"id": "q1-i", "marks": 1, "_firestore_slot_order": 0},
        "q1-ii": {"id": "q1-ii", "marks": 1, "_firestore_slot_order": 1},
    }
    assert _decode_generation_requirements(encoded) == document


def test_generation_requirements_restore_slot_order_after_map_reordering():
    slots = [{"id": f"q1-{index}", "marks": 1} for index in range(1, 16)]
    document = {
        "generation_requirements": {
            "blueprint": {"id": "custom-test", "version": 1, "sections": [{"groups": [{"slots": slots}]}]},
        },
    }
    encoded = _encode_generation_requirements(document)
    stored = encoded["generation_requirements"]["blueprint"]["sections"][0]["groups"][0]["slots"]
    # Simulate Firestore returning the map in a different field iteration order.
    encoded["generation_requirements"]["blueprint"]["sections"][0]["groups"][0]["slots"] = dict(reversed(list(stored.items())))

    decoded = _decode_generation_requirements(encoded)
    restored = decoded["generation_requirements"]["blueprint"]["sections"][0]["groups"][0]["slots"]
    assert [slot["id"] for slot in restored] == [slot["id"] for slot in slots]
    assert all("_firestore_slot_order" not in slot for slot in restored)


def test_legacy_official_slot_maps_recover_catalog_order():
    # Papers created by the old codec have no persisted ordinal.  The official
    # template remains the authoritative recovery source.
    slots = {f"q1-{index}": {"id": f"q1-{index}", "marks": 1} for index in range(15, 0, -1)}
    document = {
        "generation_requirements": {
            "blueprint": {
                "id": "icse10-physics", "version": 2,
                "sections": [{"groups": [{"slots": slots}]}],
            },
        },
    }

    decoded = _decode_generation_requirements(document)
    restored = decoded["generation_requirements"]["blueprint"]["sections"][0]["groups"][0]["slots"]
    assert [slot["id"] for slot in restored] == [f"q1-{index}" for index in range(1, 16)]


def test_list_cursor_reads_only_matching_owner_and_deduplicates_shared_books():
    records = [
        {"id": "mine", "owner_id": "teacher-1", "is_shared": False, "is_deleted": False, "created_at": "2026-01-01"},
        {"id": "shared", "owner_id": "teacher-1", "is_shared": True, "is_deleted": False, "created_at": "2026-01-03"},
        {"id": "other-shared", "owner_id": "admin", "is_shared": True, "is_deleted": False, "created_at": "2026-01-02"},
        {"id": "other-private", "owner_id": "teacher-2", "is_shared": False, "is_deleted": False, "created_at": "2026-01-04"},
        {"id": "deleted", "owner_id": "teacher-1", "is_shared": False, "is_deleted": True, "created_at": "2026-01-05"},
    ]

    class Source:
        def __init__(self, rows):
            self.rows = rows

        async def stream(self):
            for row in self.rows:
                yield type("Snapshot", (), {"id": row["id"], "to_dict": lambda self, value=row: value})()

    class NativeCollection:
        def __init__(self):
            self.filters = []

        def where(self, key, operator, value):
            self.filters.append((key, operator, value))
            return Source([row for row in records if row.get(key) == value])

        def stream(self):
            raise AssertionError("List query must not scan the whole collection")

    collection = object.__new__(FirestoreCollection)
    collection._collection = NativeCollection()

    papers = asyncio.run(collection.find({"owner_id": "teacher-1", "is_deleted": False}).sort("created_at", -1).to_list(200))
    assert [row["id"] for row in papers] == ["shared", "mine"]

    books = asyncio.run(collection.find({
        "is_deleted": False,
        "$or": [{"owner_id": "teacher-1"}, {"is_shared": True}],
    }).sort("created_at", -1).to_list(500))
    assert [row["id"] for row in books] == ["shared", "other-shared", "mine"]
    assert collection._collection.filters == [
        ("owner_id", "==", "teacher-1"),
        ("owner_id", "==", "teacher-1"),
        ("is_shared", "==", True),
    ]


def test_claim_one_uses_a_begun_transaction_for_its_read(monkeypatch):
    """A queued paper must be leased through Firestore's transaction wrapper.

    Passing an unstarted ``AsyncTransaction`` directly to document.get makes
    Firestore reject the read at runtime, leaving the worker quietly idle.
    """
    document = {"id": "job-1", "kind": "paper_generation", "status": "queued"}

    class Reference:
        def __init__(self):
            self.read_transaction = None

        async def get(self, *, transaction):
            self.read_transaction = transaction
            assert transaction.begun
            return type("Snapshot", (), {"exists": True, "to_dict": lambda self: document})()

    class Transaction:
        def __init__(self):
            self.begun = False
            self.writes = []

        def set(self, reference, value):
            self.writes.append((reference, value))

    transaction = Transaction()
    reference = Reference()
    snapshot = type("Candidate", (), {"reference": reference})()
    collection = object.__new__(FirestoreCollection)
    collection._name = "paper_review_jobs"
    collection._client = type("Client", (), {"transaction": lambda self: transaction})()

    async def matching(_query):
        return [(snapshot, document)]

    collection._matching_snapshots = matching

    def transactional(func):
        async def invoke(txn):
            txn.begun = True
            return await func(txn)
        return invoke

    monkeypatch.setattr(firestore_compat.firestore, "async_transactional", transactional)

    claimed = asyncio.run(collection.claim_one(
        {"status": "queued"}, {"status": "leased", "worker_id": "worker-1"}
    ))

    assert claimed["status"] == "leased"
    assert claimed["worker_id"] == "worker-1"
    assert reference.read_transaction is transaction
    assert transaction.writes[0][1]["status"] == "leased"
