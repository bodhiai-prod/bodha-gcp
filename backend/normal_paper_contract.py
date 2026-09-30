"""Server-owned structure for ordinary (non-blueprint) question papers.

The provider writes question content.  This module owns every field that
determines where the question appears or how it is counted.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from typing import Any

from export_utils import canonical_question_type


NORMAL_CONTRACT_VERSION = 1
TYPE_ORDER = ("information", "concept", "application")
SECTION_META = {
    "information": ("information", "Section A - Information Based"),
    "concept": ("concept", "Section B - Concept Based"),
    "application": ("application", "Section C - Application Based"),
}
FORMAT_WEIGHT = {
    "mcq": 1,
    "true_false": 1,
    "fill_blank": 1,
    "short_answer": 2,
    "long_answer": 6,
}

PROVIDER_CONTENT_FIELDS = (
    "question", "options", "correct_option", "diagram_spec",
)
from slot_identity import SLOT_IDENTITY_FIELDS
SLOT_STRUCTURE_FIELDS = set(SLOT_IDENTITY_FIELDS) - {'id'}


class NormalContractError(ValueError):
    pass


def _expanded_counts(counts: dict, total: int, *, order: tuple[str, ...] = ()) -> list[str]:
    counts = {str(key): max(0, int(value or 0)) for key, value in (counts or {}).items()}
    keys = [key for key in order if key in counts]
    keys.extend(sorted(key for key in counts if key not in keys))
    values = [key for key in keys for _ in range(counts[key])]
    if len(values) != total:
        raise NormalContractError(
            f"Resolved counts describe {len(values)} questions, expected {total}."
        )
    return values


def _slot_marks(formats: list[str], total_marks: int) -> list[int]:
    count = len(formats)
    if total_marks < count:
        raise NormalContractError(
            f"{count} questions require at least {count} total marks."
        )
    remaining = total_marks - count
    weights = [FORMAT_WEIGHT.get(fmt, 2) for fmt in formats]
    denominator = sum(weights) or count or 1
    exact = [remaining * weight / denominator for weight in weights]
    extras = [math.floor(value) for value in exact]
    left = remaining - sum(extras)
    order = sorted(range(count), key=lambda index: (-(exact[index] - extras[index]), index))
    for index in order[:left]:
        extras[index] += 1
    return [1 + value for value in extras]


def _topic_allocation(rows: list[dict], total: int) -> list[int]:
    """Weighted allocation with at least one slot for every selected identity."""
    weights = [max(1, int(row.get("weight", 5) or 5)) for row in rows]
    remaining = total - len(rows)
    if remaining < 0:
        raise NormalContractError(
            f"{len(rows)} topics are selected for {total} questions. "
            "Reduce the selected topics or increase the question count."
        )
    denominator = sum(weights) or len(rows) or 1
    exact = [remaining * weight / denominator for weight in weights]
    extras = [math.floor(value) for value in exact]
    left = remaining - sum(extras)
    order = sorted(range(len(rows)), key=lambda index: (-(exact[index] - extras[index]), -weights[index], index))
    for index in order[:left]:
        extras[index] += 1
    return [1 + value for value in extras]


def _section_difficulty(mapping: dict, qtype: str, title: str) -> str | None:
    aliases = {
        "information": {"information", "information based", "section a"},
        "concept": {"concept", "concept based", "section b"},
        "application": {"application", "application based", "section c"},
    }[qtype]
    aliases.add(title.lower())
    for raw, level in (mapping or {}).items():
        key = " ".join(str(raw).lower().replace("-", " ").split())
        if key in aliases or any(key.startswith(alias + " ") for alias in aliases):
            return level
    return None


def compile_normal_plan(spec: dict, topics: list[dict], total_marks: int) -> dict:
    """Compile one immutable slot for every requested normal-paper question."""
    total = int(spec.get("total_questions") or 0)
    if total <= 0:
        raise NormalContractError("A normal paper requires a positive question count.")

    topic_rows = [row for row in topics or [] if str(row.get("name") or "").strip()]
    if len(topic_rows) > total:
        raise NormalContractError(
            f"{len(topic_rows)} topics are selected for {total} questions. "
            "Reduce the selected topics or increase the question count."
        )
    per_topic = _topic_allocation(topic_rows, total)
    allocated_topics = [row for row, count in zip(topic_rows, per_topic) for _ in range(count)]
    type_names = _expanded_counts(spec.get("type_counts") or {"concept": total}, total, order=TYPE_ORDER)
    format_names = _expanded_counts(
        spec.get("format_counts") or {"short_answer": total}, total
    )
    marks = _slot_marks(format_names, int(total_marks))

    requested_diagrams = spec.get("diagram_count")
    diagram_slots: set[int] = set()
    if requested_diagrams is not None:
        candidates = sorted(
            range(total),
            key=lambda index: (
                type_names[index] != "application",
                -marks[index],
                index,
            ),
        )
        diagram_slots = set(candidates[:max(0, min(total, int(requested_diagrams)))])

    slots: list[dict] = []
    for index in range(total):
        qtype = canonical_question_type(type_names[index])
        if qtype not in SECTION_META:
            raise NormalContractError(f"Unsupported cognitive type: {type_names[index]}")
        topic = allocated_topics[index]
        topic_name = str(topic["name"]).strip()
        section_id, section_title = SECTION_META[qtype]
        override = next(
            (row for row in spec.get("question_overrides") or [] if int(row.get("n") or 0) == index + 1),
            None,
        )
        difficulty = (
            (override or {}).get("difficulty")
            or _section_difficulty(
                spec.get("section_difficulty") or {}, qtype, section_title
            )
            or (spec.get("format_difficulty") or {}).get(format_names[index])
            or (spec.get("topic_difficulty") or {}).get(topic_name)
            or topic.get("difficulty")
            or spec.get("difficulty")
            or "medium"
        )
        slots.append({
            "slot_id": f"normal-{index + 1:03d}",
            "order": index + 1,
            "section_id": section_id,
            "section_title": section_title,
            "topic": topic_name,
            **({"topic_id": topic["topic_id"]} if topic.get("topic_id") else {}),
            **({"textbook_id": topic["textbook_id"]} if topic.get("textbook_id") else {}),
            **({"parent_topic": topic["parent_topic"]} if topic.get("parent_topic") else {}),
            "type": qtype,
            "format": format_names[index],
            "difficulty": difficulty,
            "marks": marks[index],
            "diagram_mode": (
                "required" if index in diagram_slots else "forbidden"
            ) if requested_diagrams is not None else "allowed",
        })

    sections = [
        {"id": section_id, "title": title, "order": order}
        for order, qtype in enumerate(TYPE_ORDER, start=1)
        for section_id, title in [SECTION_META[qtype]]
        if any(slot["type"] == qtype for slot in slots)
    ]
    return {
        "contract_version": NORMAL_CONTRACT_VERSION,
        "kind": "normal",
        "sections": sections,
        "slots": slots,
        "topic_counts": dict(Counter(slot["topic"] for slot in slots)),
    }


def normal_slot_batches(
    slots: list[dict], *, single_call_limit: int = 25, batch_size: int = 20
) -> list[list[dict]]:
    """The provider-call plan for an ordinary paper.

    Up to ``single_call_limit`` questions is deliberately one call. Larger
    papers are partitioned without changing any slot allocation.
    """
    if not slots:
        return []
    size = len(slots) if len(slots) <= max(1, single_call_limit) else max(1, batch_size)
    return [slots[index:index + size] for index in range(0, len(slots), size)]


def questions_from_payload(payload: dict) -> list[dict]:
    if isinstance(payload.get("questions"), list):
        return [row for row in payload["questions"] if isinstance(row, dict)]
    return [
        question
        for section in payload.get("sections") or []
        if isinstance(section, dict)
        for question in section.get("questions") or []
        if isinstance(question, dict)
    ]


def stamp_slot_content(candidate: dict, slot: dict, *, preserve_runtime: bool = False) -> dict:
    """Copy content fields and restore every structural value from the slot."""
    question = (
        deepcopy(candidate)
        if preserve_runtime
        else {key: deepcopy(candidate[key]) for key in PROVIDER_CONTENT_FIELDS if key in candidate}
    )
    for key in SLOT_STRUCTURE_FIELDS:
        question.pop(key, None)
    question.update({
        key: deepcopy(value)
        for key, value in slot.items()
        if key not in {"order", "section_title", "diagram_mode"}
    })
    mode = slot.get("diagram_mode")
    question["needs_diagram"] = (
        True if mode == "required" else False if mode == "forbidden"
        else bool(candidate.get("needs_diagram"))
    )
    if not question["needs_diagram"]:
        question.pop("diagram_spec", None)
        question.pop("diagram_path", None)
        if preserve_runtime:
            question["diagram_status"] = "none"
    return question


def materialize_normal_sections(payload: dict, plan: dict, *, only_slots: list[dict] | None = None) -> tuple[list, list[str], list[str]]:
    """Return canonical sections plus missing and unknown slot identifiers."""
    slots = list(only_slots if only_slots is not None else plan.get("slots") or [])
    allowed = {slot["slot_id"]: slot for slot in slots}
    received: dict[str, dict] = {}
    unknown: list[str] = []
    for candidate in questions_from_payload(payload):
        slot_id = str(candidate.get("slot_id") or "").strip()
        if slot_id not in allowed:
            unknown.append(slot_id or "missing")
            continue
        if slot_id not in received and str(candidate.get("question") or "").strip():
            received[slot_id] = stamp_slot_content(candidate, allowed[slot_id])

    by_section = {
        row["id"]: {"section_id": row["id"], "title": row["title"], "questions": []}
        for row in plan.get("sections") or []
    }
    for slot in slots:
        question = received.get(slot["slot_id"])
        if question:
            by_section[slot["section_id"]]["questions"].append(question)
    sections = [
        by_section[row["id"]]
        for row in sorted(plan.get("sections") or [], key=lambda value: value.get("order", 0))
        if by_section[row["id"]]["questions"]
    ]
    missing = [slot["slot_id"] for slot in slots if slot["slot_id"] not in received]
    return sections, missing, unknown


def merge_normal_sections(plan: dict, *section_groups: list) -> list:
    """Merge already accepted questions without losing IDs or edit state."""
    questions = [
        question
        for sections in section_groups
        for section in sections or []
        for question in section.get("questions") or []
    ]
    expected = {slot["slot_id"]: slot for slot in plan.get("slots") or []}
    received: dict[str, dict] = {}
    for candidate in questions:
        slot_id = candidate.get("slot_id")
        if slot_id in expected and slot_id not in received:
            received[slot_id] = stamp_slot_content(
                candidate, expected[slot_id], preserve_runtime=True
            )

    by_section = {
        row["id"]: {"section_id": row["id"], "title": row["title"], "questions": []}
        for row in plan.get("sections") or []
    }
    for slot in plan.get("slots") or []:
        question = received.get(slot["slot_id"])
        if question:
            by_section[slot["section_id"]]["questions"].append(question)
    return [
        by_section[row["id"]]
        for row in sorted(plan.get("sections") or [], key=lambda value: value.get("order", 0))
        if by_section[row["id"]]["questions"]
    ]


def missing_plan_slots(plan: dict, sections: list) -> list[dict]:
    present = {
        question.get("slot_id")
        for section in sections or []
        for question in section.get("questions") or []
    }
    return [slot for slot in plan.get("slots") or [] if slot.get("slot_id") not in present]


def normalize_normal_sections(sections: list, plan: dict | None = None) -> tuple[list, bool]:
    """Canonicalize safe generated normal papers and conservative legacy ones."""
    if plan and plan.get("contract_version") == NORMAL_CONTRACT_VERSION:
        expected = {slot.get("slot_id") for slot in plan.get("slots") or []}
        actual = [
            question.get("slot_id")
            for section in sections or []
            for question in section.get("questions") or []
        ]
        # A question added manually has no provable slot. Preserve the teacher's
        # document verbatim and let validation flag it instead of deleting it.
        if any(slot_id not in expected for slot_id in actual):
            return sections or [], False
        normalized = merge_normal_sections(plan, sections)
        return normalized, normalized != (sections or [])

    questions = [
        question
        for section in sections or []
        for question in section.get("questions") or []
        if isinstance(question, dict)
    ]
    if not questions or any(canonical_question_type(question.get("type")) is None for question in questions):
        return sections or [], False
    # Do not rewrite genuinely custom sections.  The legacy defect always used
    # repeated conventional Section A/B/C headings with provider-added suffixes.
    conventional = all(
        str(section.get("title") or "").strip().lower().startswith("section ")
        for section in sections or []
    )
    families = [str(section.get("title") or "").strip().lower().split("-")[0].strip() for section in sections or []]
    if not conventional or len(families) == len(set(families)):
        return sections or [], False

    grouped = {qtype: [] for qtype in TYPE_ORDER}
    for question in questions:
        grouped[canonical_question_type(question.get("type"))].append(question)
    normalized = [
        {"section_id": SECTION_META[qtype][0], "title": SECTION_META[qtype][1], "questions": grouped[qtype]}
        for qtype in TYPE_ORDER if grouped[qtype]
    ]
    return normalized, normalized != (sections or [])


def validate_normal_plan(sections: list, plan: dict) -> list[dict[str, Any]]:
    questions = [q for section in sections or [] for q in section.get("questions") or []]
    slots = plan.get("slots") or []
    expected = {slot["slot_id"]: slot for slot in slots}
    actual = Counter(q.get("slot_id") for q in questions)
    issues: list[dict[str, Any]] = []
    missing = [slot_id for slot_id in expected if actual[slot_id] == 0]
    duplicates = [slot_id for slot_id, count in actual.items() if slot_id and count > 1]
    if missing:
        issues.append({"code": "missing_slots", "category": "count", "count": len(missing), "slot_ids": missing})
    if duplicates:
        issues.append({"code": "duplicate_slots", "category": "count", "count": len(duplicates), "slot_ids": duplicates})
    expected_marks = sum(int(slot.get("marks") or 0) for slot in slots)
    actual_marks = sum(
        int(question.get("marks") or 0)
        for question in questions
        if isinstance(question.get("marks"), (int, float))
    )
    if actual_marks != expected_marks:
        issues.append({
            "code": "marks_total",
            "category": "marks",
            "expected": expected_marks,
            "actual": actual_marks,
        })
    for question in questions:
        slot = expected.get(question.get("slot_id"))
        if not slot:
            issues.append({"code": "unknown_slot", "category": "structure", "slot_id": question.get("slot_id")})
            continue
        for field in ("section_id", "topic", "type", "format", "difficulty", "marks"):
            expected_value = slot.get(field)
            actual_value = question.get(field)
            if actual_value != expected_value:
                category = {
                    "topic": "topic",
                    "format": "formatting",
                    "marks": "marks",
                }.get(field, "structure")
                issues.append({"code": "slot_metadata", "category": category, "slot_id": slot["slot_id"], "field": field})
        for field in ("topic_id", "textbook_id", "parent_topic"):
            if field in slot and question.get(field) != slot[field]:
                issues.append({
                    "code": "slot_identity",
                    "category": "topic",
                    "slot_id": slot["slot_id"],
                    "field": field,
                })
        diagram_mode = slot.get("diagram_mode")
        if (
            (diagram_mode == "required" and not question.get("needs_diagram"))
            or (diagram_mode == "forbidden" and question.get("needs_diagram"))
        ):
            issues.append({
                "code": "slot_diagram",
                "category": "diagram",
                "slot_id": slot["slot_id"],
            })
    return issues


def review_status(issues: list[dict[str, Any]]) -> dict:
    if not issues:
        return {"status": "ready", "summary": "Paper matches the requested structure.", "issues": []}
    grouped: dict[tuple, dict] = {}
    for issue in issues:
        key = (
            issue.get("category") or "other",
            issue.get("code") or "validation_issue",
            issue.get("field"),
        )
        count = max(1, int(issue.get("count") or 1))
        if key not in grouped:
            grouped[key] = {
                "category": key[0],
                "code": key[1],
                **({"field": key[2]} if key[2] else {}),
                "count": 0,
                "slot_ids": [],
                "messages": [],
            }
        row = grouped[key]
        row["count"] += count
        for slot_id in [issue.get("slot_id"), *(issue.get("slot_ids") or [])]:
            if slot_id and slot_id not in row["slot_ids"] and len(row["slot_ids"]) < 20:
                row["slot_ids"].append(slot_id)
        if issue.get("message") and issue["message"] not in row["messages"] and len(row["messages"]) < 5:
            row["messages"].append(issue["message"])
    compact = []
    for row in grouped.values():
        messages = row.pop("messages")
        if messages:
            row["message"] = messages[0] + (
                f" (+{row['count'] - 1} related)" if row["count"] > 1 else ""
            )
        if not row["slot_ids"]:
            row.pop("slot_ids")
        compact.append(row)
    categories = Counter()
    for issue in compact:
        categories[issue["category"]] += issue["count"]
    pieces = [f"{count} {category} issue{'s' if count != 1 else ''}" for category, count in sorted(categories.items())]
    return {
        "status": "needs_review",
        "summary": "Paper recovery needs review: " + ", ".join(pieces) + ".",
        "issues": compact,
    }


def review_status_from_misses(misses: list[str]) -> dict:
    """Group legacy prose validation misses into a compact public status."""
    issues = []
    for miss in dict.fromkeys(str(value) for value in misses or [] if value):
        lower = miss.lower()
        category = (
            "topic" if " questions on " in lower
            else "marks" if "mark" in lower
            else "format" if "format" in lower or "mcq" in lower or "short_answer" in lower or "long_answer" in lower
            else "diagram" if "diagram" in lower
            else "count" if "question" in lower
            else "other"
        )
        issues.append({"code": "legacy_validation", "category": category, "message": miss})
    return review_status(issues)
