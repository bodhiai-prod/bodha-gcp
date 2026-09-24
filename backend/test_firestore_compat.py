from firestore_compat import _get, _matches, _project, _set


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
