"""Source-aware topic indexing and bounded, topic-directed prompt context."""
import re
from section_blueprints import topic_catalog


def merge_topic_batches(batches):
    merged = {}
    for topics, valid_indices in batches:
        for raw in topics:
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            key = re.sub(r"\s+", " ", name.casefold())
            row = merged.setdefault(key, {"name": name, "subtopics": [], "source_chunks": []})
            for sub in raw.get("subtopics") or []:
                if isinstance(sub, str) and sub.strip() and sub.strip().casefold() not in {s.casefold() for s in row["subtopics"]}:
                    row["subtopics"].append(sub.strip())
            cited = [i for i in raw.get("source_chunks", []) if type(i) is int and i in valid_indices]
            for i in cited:
                if i not in row["source_chunks"]:
                    row["source_chunks"].append(i)
    return list(merged.values())


def topic_context(books, allocations, budget=30000):
    by_book = {b["id"]: b for b in books}
    rows = allocations or [r for b in books for r in topic_catalog(b)]
    rows = list({(r.get("textbook_id"), r.get("topic_id") or r.get("name")): r for r in rows}.values())
    excerpts, seen = [], set()
    # Every chosen topic gets a share of the context budget, including later chapters.
    share = max(40, (budget - 140 * len(rows)) // max(1, len(rows)))
    for row in rows:
        book = by_book.get(row.get("textbook_id"))
        if not book:
            continue
        chunks = book.get("chunks") or []
        words = set(re.findall(r"\w{3,}", (row.get("parent_topic", "") + " " + row.get("name", "")).casefold()))
        scores = {i: len(words & set(re.findall(r"\w{3,}", chunk.casefold()))) for i, chunk in enumerate(chunks)}
        ranked = sorted((i for i in scores if scores[i]), key=lambda i: scores[i], reverse=True)
        cited = [i for i in row.get("source_chunks", []) if type(i) is int and 0 <= i < len(chunks)]
        candidates = list(dict.fromkeys(cited + ranked))
        for i in candidates:
            key = (book["id"], i)
            if key in seen:
                continue
            seen.add(key)
            excerpts.append(f"[Textbook {book['id']}; topic {row.get('name', '')}; chunk {i}]\n{chunks[i][:share]}")
            break
    if not excerpts:
        for book in books:
            chunks = book.get("chunks") or []
            excerpts.extend(f"[Textbook {book['id']}]\n{c}" for c in chunks[::max(1, len(chunks)//4)][:4])
    return "\n\n".join(excerpts)[:budget]
