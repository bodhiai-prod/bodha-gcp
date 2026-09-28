"""Storage helpers for the extracted-text payload of a textbook.

Keep potentially large chunk arrays out of the ``textbooks`` document.  The
metadata document is read on every library page; chunk content is only read by
the processing and generation paths that actually need it.
"""
from __future__ import annotations

from typing import Any

from deps import db


async def save_textbook_chunks(textbook_id: str, chunks: list[str]) -> None:
    """Replace the compact, sampled chunk set used by downstream AI jobs."""
    await db.textbook_chunks.update_one(
        {"textbook_id": textbook_id},
        {"$set": {"textbook_id": textbook_id, "chunks": chunks}},
        upsert=True,
    )


async def load_textbook_chunks(textbook: dict[str, Any]) -> list[str]:
    """Read new external content, falling back to legacy inline chunks."""
    if textbook.get("chunk_storage") == "textbook_chunks":
        stored = await db.textbook_chunks.find_one(
            {"textbook_id": textbook["id"]}, {"_id": 0}
        )
        return (stored or {}).get("chunks") or []
    return textbook.get("chunks") or []
