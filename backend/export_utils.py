"""Shared normalization for printable paper exports."""

from __future__ import annotations

import math
from typing import Any


DEFAULT_INSTRUCTIONS = "Answer all questions."
QUESTION_TYPE_LABELS = {
    "information": "Information Based",
    "concept": "Concept Based",
    "application": "Application Based",
}
_QUESTION_TYPE_ALIASES = {
    "information": "information", "info": "information", "knowledge": "information",
    "direct": "information", "factual": "information", "recall": "information",
    "concept": "concept", "conceptual": "concept", "reasoning": "concept",
    "understanding": "concept", "problem": "concept", "problem_based": "concept",
    "application": "application", "applied": "application", "numerical": "application",
    "application_based": "application",
}


def format_mark(value: Any) -> str:
    """Render numeric marks without incidental trailing zeroes."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "0").strip() or "0"
    if not math.isfinite(number):
        return "0"
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def canonical_question_type(value: Any, default: str | None = None) -> str | None:
    """Return one of the three persisted cognitive-domain values, if known."""
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _QUESTION_TYPE_ALIASES.get(key, default)


def hydrate_question_types(paper: dict) -> None:
    """Normalize safe legacy display metadata without changing question text."""
    for section in paper.get("sections") or []:
        for question in section.get("questions") or []:
            question["type"] = canonical_question_type(question.get("type"), "concept")


def printable_instructions(raw: Any, *, template_instructions: Any = "", custom_instructions: str = "") -> str:
    """Return saved student-facing instructions with a deterministic fallback.

    A teacher's saved edit is authoritative.  ``custom_instructions`` remains
    generation-only, but it must not cause a separately saved paper
    instruction to disappear when the paper is loaded or exported.
    """
    saved = str(raw or "").strip()
    if saved:
        return saved
    template = str(template_instructions or "").strip()
    if template:
        return template
    return DEFAULT_INSTRUCTIONS
