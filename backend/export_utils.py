"""Shared normalization for printable paper exports."""

from __future__ import annotations

import math
import re
from typing import Any


DEFAULT_INSTRUCTIONS = "Answer all questions."
_INTERNAL_INSTRUCTION = re.compile(
    r"\b(latex|json|markdown|format(?:ting)?|render(?:ing)?|model|prompt|"
    r"mathematical expressions? and symbols?)\b",
    re.IGNORECASE,
)


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


def printable_instructions(raw: Any, *, custom_instructions: str = "") -> str:
    """Return student-facing instructions with a deterministic safe default.

    With no teacher instruction, model-generated prose is intentionally ignored.
    This prevents implementation directions from leaking onto the paper.
    """
    if not (custom_instructions or "").strip():
        return DEFAULT_INSTRUCTIONS
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_INSTRUCTIONS
    sentences = re.split(r"(?<=[.!?])\s+|\r?\n+", text)
    kept = [sentence.strip() for sentence in sentences
            if sentence.strip() and not _INTERNAL_INSTRUCTION.search(sentence)]
    return " ".join(kept).strip() or DEFAULT_INSTRUCTIONS
