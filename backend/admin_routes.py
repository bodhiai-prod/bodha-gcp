"""Admin Dashboard endpoints. Imported once from server.py to register routes
on the shared `api_router`."""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query

from auth import get_current_user
from deps import api_router, db, require_admin


@api_router.get('/admin/usage')
async def admin_usage(user: dict = Depends(get_current_user), resource_id: str = '',
                      user_id: str = '', kind: str = '', status: str = '',
                      page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
    require_admin(user)
    query = {k: v for k, v in dict(resource_id=resource_id, user_id=user_id, kind=kind, status=status).items() if v}
    operations = await db.usage_operations.find(query, {'_id': 0}).to_list(None)
    operations.sort(key=lambda row: row.get('started_at', ''), reverse=True)
    selected = operations[(page - 1) * page_size:page * page_size]
    for operation in selected:
        calls = await db.usage_calls.find({'operation_id': operation['id']}, {'_id': 0}).to_list(None)
        operation['calls'] = sorted(calls, key=lambda row: row['timestamp'])
        operation['known_cost_inr'] = sum(call.get('cost_inr') or 0 for call in calls)
        operation['unknown_calls'] = sum(call.get('cost_inr') is None for call in calls)
        operation['tokens'] = sum(call.get('total_tokens', 0) for call in calls)
        collection = db.textbooks if operation['resource_type'] == 'book' else db.papers
        resource = await collection.find_one({'id': operation['resource_id']}, {'_id': 0}) or {}
        operation['resource_name'] = resource.get('title') or resource.get('original_filename') or operation['resource_id']
        owner_id = operation.get('user_id') or resource.get('owner_id')
        owner = await db.users.find_one({'id': owner_id}, {'_id': 0}) if owner_id else None
        operation['teacher'] = (owner or {}).get('full_name') or (owner or {}).get('email') or 'Unattributed'
    return {'items': selected, 'total': len(operations), 'page': page, 'page_size': page_size}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _profile_complete(record: dict) -> bool:
    board = (record.get("board") or "").strip()
    return bool(
        (record.get("full_name") or "").strip()
        and (record.get("school_name") or "").strip()
        and board
        and (board != "Other" or (record.get("board_other_name") or "").strip())
    )


def _provider(record: dict) -> str:
    # Provider data is added during Firebase migration; older records remain
    # useful in the directory without guessing how they originally signed in.
    providers = record.get("firebase_providers") or record.get("providers") or []
    if isinstance(providers, str):
        providers = [providers]
    if "google.com" in providers or "google" in providers:
        return "Google"
    if "password" in providers:
        return "Email/password"
    return "Firebase" if record.get("id") else "Unknown"


async def _user_counts(user_id: str) -> dict:
    active = {"is_deleted": False, "owner_id": user_id}
    tests = await db.mock_tests.find({"student_id": user_id}, {"_id": 0, "status": 1, "score": 1}).to_list(500)
    submitted = [test for test in tests if test.get("status") == "submitted"]
    average_score = (
        round(sum((test.get("score") or {}).get("percent", 0) for test in submitted) / len(submitted))
        if submitted else None
    )
    return {
        "textbook_count": await db.textbooks.count_documents(active),
        "paper_count": await db.papers.count_documents(active),
        "question_count": await db.qbank.count_documents(active),
        "test_count": len(tests),
        "submitted_test_count": len(submitted),
        "average_test_score": average_score,
    }


def _directory_row(record: dict, counts: dict) -> dict:
    return {
        "id": record["id"],
        "full_name": record.get("full_name", ""),
        "email": record.get("email", ""),
        "role": record.get("role", "teacher"),
        "school_name": record.get("school_name", ""),
        "board": record.get("board", ""),
        "board_other_name": record.get("board_other_name", ""),
        "created_at": record.get("created_at", ""),
        "last_active_at": record.get("last_active_at", ""),
        "profile_complete": _profile_complete(record),
        "auth_provider": _provider(record),
        **counts,
    }


@api_router.get("/admin/overview")
async def admin_overview(user: dict = Depends(get_current_user)):
    require_admin(user)
    now = datetime.now(timezone.utc)
    users = await db.users.find({}, {"_id": 0, "password_hash": 0}).to_list(1000)
    events = await db.activity_events.find({}, {"_id": 0}).to_list(5000)
    teachers = sum(u.get("role") == "teacher" for u in users)
    admins = sum(u.get("role") == "admin" for u in users)
    students = sum(u.get("role") == "student" for u in users)
    textbooks_total = await db.textbooks.count_documents({"is_deleted": False})
    textbooks_shared = await db.textbooks.count_documents(
        {"is_deleted": False, "is_shared": True}
    )
    papers_total = await db.papers.count_documents({"is_deleted": False})
    papers_pending = await db.papers.count_documents(
        {"is_deleted": False, "generation_status": "pending"}
    )
    papers_failed = await db.papers.count_documents(
        {"is_deleted": False, "generation_status": "failed"}
    )
    def recent(field: str, days: int) -> int:
        cutoff = now - timedelta(days=days)
        return sum((_parse_time(item.get(field)) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff for item in users)

    active_ids = {
        event.get("user_id") for event in events
        if (_parse_time(event.get("occurred_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= now - timedelta(days=30)
    }
    board_breakdown: dict[str, int] = {}
    schools = set()
    for item in users:
        board = item.get("board_other_name") if item.get("board") == "Other" else item.get("board")
        if board:
            board_breakdown[board] = board_breakdown.get(board, 0) + 1
        if item.get("school_name"):
            schools.add(item["school_name"].strip().casefold())
    stuck_cutoff = now - timedelta(minutes=20)
    pending_papers = await db.papers.find({"is_deleted": False, "generation_status": "pending"}, {"_id": 0, "id": 1, "title": 1, "created_at": 1, "owner_id": 1}).to_list(500)
    failed_books = await db.textbooks.find({"is_deleted": False, "status": "extraction_failed"}, {"_id": 0, "id": 1, "original_filename": 1, "owner_id": 1, "created_at": 1}).to_list(500)
    return {
        "teachers": teachers,
        "admins": admins,
        "students": students,
        "users_total": len(users),
        "new_users_7d": recent("created_at", 7),
        "new_users_30d": recent("created_at", 30),
        "active_users_30d": len(active_ids),
        "profiles_complete": sum(_profile_complete(item) for item in users),
        "profiles_incomplete": sum(not _profile_complete(item) for item in users),
        "schools_total": len(schools),
        "board_breakdown": sorted(({"label": label, "count": count} for label, count in board_breakdown.items()), key=lambda item: (-item["count"], item["label"]))[:8],
        "textbooks_total": textbooks_total,
        "textbooks_shared": textbooks_shared,
        "papers_total": papers_total,
        "papers_pending": papers_pending,
        "papers_failed": papers_failed,
        "tests_submitted": await db.mock_tests.count_documents({"status": "submitted"}),
        "attention": {
            "failed_textbooks": failed_books[:10],
            "failed_papers": await db.papers.find({"is_deleted": False, "generation_status": "failed"}, {"_id": 0, "id": 1, "title": 1, "owner_id": 1, "created_at": 1}).to_list(10),
            "stuck_papers": [paper for paper in pending_papers if (_parse_time(paper.get("created_at")) or now) < stuck_cutoff][:10],
        },
    }


@api_router.get("/admin/users")
async def admin_users(
    q: str = "",
    role: str = "",
    board: str = "",
    profile: str = "",
    activity: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    users = await db.users.find(
        {}, {"_id": 0, "password_hash": 0}
    ).sort("created_at", -1).to_list(1000)
    needle = q.strip().casefold()
    now = datetime.now(timezone.utc)
    active_cutoff = now - timedelta(days=30)
    filtered = []
    for record in users:
        row = _directory_row(record, await _user_counts(record["id"]))
        haystack = " ".join(str(row.get(key, "")) for key in ("full_name", "email", "school_name", "board", "board_other_name")).casefold()
        if needle and needle not in haystack:
            continue
        if role and row["role"] != role:
            continue
        if board and board.casefold() not in {
            (row["board"] or "").casefold(),
            (row["board_other_name"] or "").casefold(),
        }:
            continue
        if profile == "complete" and not row["profile_complete"]:
            continue
        if profile == "incomplete" and row["profile_complete"]:
            continue
        was_active = (_parse_time(row["last_active_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= active_cutoff
        if activity == "active" and not was_active:
            continue
        if activity == "inactive" and was_active:
            continue
        filtered.append(row)
    start = (page - 1) * page_size
    return {"items": filtered[start:start + page_size], "total": len(filtered), "page": page, "page_size": page_size}


@api_router.get("/admin/users/{user_id}")
async def admin_user_detail(user_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    record = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not record:
        raise HTTPException(status_code=404, detail="User not found")
    counts = await _user_counts(user_id)
    papers = await db.papers.find({"owner_id": user_id, "is_deleted": False}, {"_id": 0, "sections": 0, "chunks": 0, "topics": 0}).sort("created_at", -1).to_list(20)
    textbooks = await db.textbooks.find({"owner_id": user_id, "is_deleted": False}, {"_id": 0, "chunks": 0}).sort("created_at", -1).to_list(20)
    questions = await db.qbank.find({"owner_id": user_id, "is_deleted": False}, {"_id": 0, "question": 0}).sort("created_at", -1).to_list(20)
    tests = await db.mock_tests.find({"student_id": user_id}, {"_id": 0, "answers": 0}).sort("created_at", -1).to_list(20)
    activity = await db.activity_events.find({"user_id": user_id}, {"_id": 0}).sort("occurred_at", -1).to_list(50)
    return {"user": _directory_row(record, counts), "papers": papers, "textbooks": textbooks, "questions": questions, "tests": tests, "activity": activity}


@api_router.get("/admin/activity")
async def admin_activity(
    event_type: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    query = {"event_type": event_type} if event_type else {}
    events = await db.activity_events.find(query, {"_id": 0}).sort("occurred_at", -1).to_list(5000)
    ids = list({event.get("user_id") for event in events if event.get("user_id")})
    profiles = await db.users.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "full_name": 1, "email": 1}).to_list(len(ids))
    by_id = {profile["id"]: profile for profile in profiles}
    items = [{**event, "user": by_id.get(event.get("user_id"), {})} for event in events]
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size}


@api_router.get("/admin/textbooks")
async def admin_textbooks(user: dict = Depends(get_current_user)):
    require_admin(user)
    tbs = await db.textbooks.find(
        {"is_deleted": False}, {"_id": 0, "chunks": 0}
    ).sort("created_at", -1).to_list(500)
    owner_ids = list({t["owner_id"] for t in tbs})
    owners = await db.users.find(
        {"id": {"$in": owner_ids}}, {"_id": 0, "id": 1, "email": 1, "role": 1}
    ).to_list(len(owner_ids))
    by_id = {o["id"]: o for o in owners}
    out = []
    for t in tbs:
        owner = by_id.get(t["owner_id"], {})
        out.append({
            "id": t["id"],
            "original_filename": t["original_filename"],
            "subject": t["subject"],
            "class_name": t["class_name"],
            "status": t["status"],
            "topic_count": len(t.get("topics", [])),
            "chunk_count": t.get("chunk_count", 0),
            "is_shared": t.get("is_shared", False),
            "created_at": t["created_at"],
            "owner_id": t["owner_id"],
            "owner_email": owner.get("email", ""),
            "owner_role": owner.get("role", ""),
        })
    return out


@api_router.patch("/admin/textbooks/{textbook_id}/share")
async def admin_set_textbook_shared(
    textbook_id: str,
    is_shared: bool = Query(...),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    tb = await db.textbooks.find_one(
        {"id": textbook_id, "is_deleted": False}, {"_id": 0}
    )
    if not tb:
        raise HTTPException(status_code=404, detail="Textbook not found")
    await db.textbooks.update_one(
        {"id": textbook_id}, {"$set": {"is_shared": bool(is_shared)}}
    )
    return {"id": textbook_id, "is_shared": bool(is_shared)}


@api_router.get("/admin/papers")
async def admin_papers(user: dict = Depends(get_current_user)):
    require_admin(user)
    papers = await db.papers.find(
        {"is_deleted": False},
        {"_id": 0, "sections": 0, "topics": 0, "chunks": 0},
    ).sort("created_at", -1).to_list(500)
    owner_ids = list({p["owner_id"] for p in papers})
    owners = await db.users.find(
        {"id": {"$in": owner_ids}}, {"_id": 0, "id": 1, "email": 1}
    ).to_list(len(owner_ids))
    by_id = {o["id"]: o["email"] for o in owners}
    return [
        {
            "id": p["id"],
            "title": p.get("title", ""),
            "subject": p.get("subject", ""),
            "class_name": p.get("class_name", ""),
            "total_marks": p.get("total_marks", 0),
            "generation_status": p.get("generation_status", "ready"),
            "created_at": p.get("created_at", ""),
            "owner_id": p["owner_id"],
            "owner_email": by_id.get(p["owner_id"], ""),
        }
        for p in papers
    ]
