from firestore_compat import (
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
        "q1-i": {"id": "q1-i", "marks": 1},
        "q1-ii": {"id": "q1-ii", "marks": 1},
    }
    assert _decode_generation_requirements(encoded) == document
