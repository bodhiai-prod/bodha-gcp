"""Small, server-side audit trail for the operational admin dashboard."""
from __future__ import annotations

import uuid
from typing import Any

from deps import db, logger, utcnow_iso


async def record_activity(user_id: str, event_type: str, metadata: dict[str, Any] | None = None) -> None:
    """Persist safe operational activity and update the user's last-seen time.

    This intentionally accepts only caller-created, small metadata dictionaries.
    Authentication tokens, passwords, document text and uploaded file contents are
    never placed in the activity collection.
    """
    occurred_at = utcnow_iso()
    safe_metadata = {
        str(key)[:60]: value
        for key, value in (metadata or {}).items()
        if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 240
    }
    try:
        await db.activity_events.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "event_type": event_type,
            "metadata": safe_metadata,
            "occurred_at": occurred_at,
        })
        await db.users.update_one({"id": user_id}, {"$set": {"last_active_at": occurred_at}})
    except Exception as exc:  # Tracking must never make the main action fail.
        logger.warning("activity record failed (%s): %s", event_type, exc)
