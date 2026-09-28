"""Move legacy inline textbook chunks into the compact content collection.

Run without ``--apply`` first. The migration is idempotent and clears inline
payloads only after their replacement content document is written.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deps import db  # noqa: E402
from textbook_content import save_textbook_chunks  # noqa: E402


async def migrate(apply: bool) -> dict:
    books = await db.textbooks.find({}, {"_id": 0}).to_list(None)
    report = {"total": len(books), "migrated": [], "skipped": []}
    for book in books:
        if book.get("chunk_storage") == "textbook_chunks":
            report["skipped"].append({"id": book.get("id"), "reason": "already migrated"})
            continue
        chunks = book.get("chunks") or []
        if not book.get("id") or not isinstance(chunks, list):
            report["skipped"].append({"id": book.get("id"), "reason": "missing or invalid chunks"})
            continue
        if apply:
            await save_textbook_chunks(book["id"], chunks)
            await db.textbooks.update_one({"id": book["id"]}, {"$set": {
                "chunk_storage": "textbook_chunks", "chunks": []
            }})
        report["migrated"].append({"id": book["id"], "chunks": len(chunks)})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy inline textbook chunks")
    parser.add_argument("--apply", action="store_true", help="perform writes; default is a dry run")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    report = asyncio.run(migrate(args.apply))
    output = json.dumps(report, indent=2)
    print(output)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
