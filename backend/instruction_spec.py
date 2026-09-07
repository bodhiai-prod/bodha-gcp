"""Turning the teacher's Additional Instructions into numbers the prompt enforces.

The box used to be treated as prose that SUPPRESSED settings rather than as a
specification that OVERRODE numbers, and three failures followed from that:

* "10 mcq hard and 2 long question" was never parsed into a count, so the
  slider-derived target (16 questions for a 50-mark paper) stayed in force and
  the prompt carried a flat contradiction -- a hard, counted topic quota summing
  to 16 alongside a loose sentence asking for 12. The number won.
* Merely typing in the box set `has_override`, which deleted the teacher's own
  format mix along with the engine defaults.
* "make Q23 as hard as possible" had nothing to bind to, because questions are
  never numbered in the prompt.

This module parses the box once, cheaply, into a spec. `qgen_prompt` then renders
that spec as quotas it already knows how to enforce, and `verify` reports
afterwards whether the paper actually complied.

Nothing here raises. A parse that fails returns an empty spec, which renders
exactly the prompt the engine produced before this module existed -- the prose
still reaches the model, it just stops governing the arithmetic.

The one rule that matters more than any of it: whatever the teacher asked for,
questions stay inside the syllabus unless the teacher explicitly said otherwise.
That is `allow_out_of_syllabus`, and it is the only field that can loosen a
constraint rather than tighten one.
"""
from __future__ import annotations

import asyncio
import os
import re

from deps import logger, utcnow_iso
from llm_adapter import chat_complete, parse_json_response
from prompts import INSTRUCTION_SPEC_SYSTEM, instruction_spec_prompt
from generation_requirements import check_ui_requirements

# Rendering lives in prompts.py with every other prompt builder, and is
# re-exported here so callers have one import for the whole feature.
from prompts import (  # noqa: F401
    apportion_marks,
    is_empty,
    render_precedence,
    render_spec,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# false = the box is passed through as prose only, exactly as before this
# module. The kill switch.
SPEC_ENABLED = os.environ.get("INSTRUCTION_SPEC_ENABLED", "true").strip().lower() not in (
    "false", "0", "no",
)
# Blank means "reuse whatever chain TEXT_PROVIDER already serves".
SPEC_MODEL = os.environ.get("INSTRUCTION_SPEC_MODEL", "").strip()
# Smaller budget than a repair pass: the input is a few sentences and the output
# is a small JSON object.
SPEC_TIMEOUT = float(os.environ.get("INSTRUCTION_SPEC_TIMEOUT", "60"))
# A paper cannot sensibly be driven past this from one instruction line. Matches
# the competitive request ceiling so the two cannot disagree.
MAX_TOTAL_QUESTIONS = 200

_DIFFICULTIES = ("easy", "medium", "hard")
# The formats FORMAT_CONVENTIONS documents, plus the empty string for free-form.
_KNOWN_FORMATS = (
    "mcq", "short_answer", "long_answer", "fill_blank", "true_false",
)


def empty_spec() -> dict:
    """A spec that changes nothing. What a disabled or failed parse returns."""
    return {
        "total_questions": 0,
        "format_counts": {},
        "section_difficulty": {},
        "question_overrides": [],
        "allow_out_of_syllabus": False,
        "unparsed": [],
        "at": utcnow_iso(),
    }


# ---------------------------------------------------------------------------
# Normalisation of whatever the model returns
# ---------------------------------------------------------------------------
def _clean_format_counts(raw) -> dict:
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for name, count in raw.items():
        key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if n > 0 and (key in _KNOWN_FORMATS or key):
            out[key] = n
    return out


def _clean_section_difficulty(raw) -> dict:
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for name, level in raw.items():
        label = str(name).strip()
        value = str(level).strip().lower()
        if label and value in _DIFFICULTIES:
            out[label] = value
    return out


def _clean_overrides(raw) -> list:
    out: list = []
    if not isinstance(raw, list):
        return out
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        if n < 1 or n in seen:
            continue
        seen.add(n)
        difficulty = str(item.get("difficulty") or "").strip().lower()
        note = " ".join(str(item.get("note") or "").split())[:200]
        if difficulty not in _DIFFICULTIES:
            difficulty = ""
        if not difficulty and not note:
            continue
        out.append({"n": n, "difficulty": difficulty, "note": note})
    return sorted(out, key=lambda o: o["n"])


def _clean_unparsed(raw) -> list:
    """Directives the parser could not structure.

    These are the reason a teacher can trust the box: anything not recognised is
    still handed to the model verbatim rather than dropped on the floor because
    a schema had no field for it.
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = " ".join(str(item).split())[:300]
        if text:
            out.append(text)
    return out[:20]


def normalise(raw: dict | None) -> dict:
    """Coerce a model-returned object into the spec shape, dropping junk."""
    spec = empty_spec()
    if not isinstance(raw, dict):
        return spec

    try:
        total = int(raw.get("total_questions") or 0)
    except (TypeError, ValueError):
        total = 0
    spec["total_questions"] = total if 0 < total <= MAX_TOTAL_QUESTIONS else 0

    spec["format_counts"] = _clean_format_counts(raw.get("format_counts"))
    spec["section_difficulty"] = _clean_section_difficulty(raw.get("section_difficulty"))
    spec["topic_difficulty"] = _clean_section_difficulty(raw.get("topic_difficulty"))
    spec["format_difficulty"] = _clean_section_difficulty(raw.get("format_difficulty"))
    level = str(raw.get("difficulty") or "").lower()
    if level in _DIFFICULTIES:
        spec["difficulty"] = level
    try:
        diagram_count = int(raw["diagram_count"])
        if 0 <= diagram_count <= MAX_TOTAL_QUESTIONS:
            spec["diagram_count"] = diagram_count
    except (KeyError, TypeError, ValueError):
        pass
    spec["question_overrides"] = _clean_overrides(raw.get("question_overrides"))
    spec["allow_out_of_syllabus"] = bool(raw.get("allow_out_of_syllabus"))
    spec["unparsed"] = _clean_unparsed(raw.get("unparsed"))

    # A stated per-format breakdown implies the total, and the two must agree or
    # the prompt carries the same contradiction this module exists to remove.
    summed = sum(spec["format_counts"].values())
    if summed:
        if not spec["total_questions"]:
            spec["total_questions"] = min(summed, MAX_TOTAL_QUESTIONS)
        elif spec["total_questions"] != summed:
            logger.info(
                f"[spec] total_questions {spec['total_questions']} disagrees with "
                f"format counts summing to {summed}; trusting the breakdown"
            )
            spec["total_questions"] = min(summed, MAX_TOTAL_QUESTIONS)
    return spec


# ---------------------------------------------------------------------------
# The parse call
# ---------------------------------------------------------------------------
async def parse_instructions(
    text: str, *, ref: str = "", context: dict | None = None
) -> dict:
    """Parse the Additional Instructions box into a spec. Never raises.

    A failure here is not an error worth failing a paper over -- the prose is
    still sent to the model in the prompt, so the worst case is the behaviour
    that existed before this module.
    """
    text = (text or "").strip()
    if not text or not SPEC_ENABLED:
        return empty_spec()
    chain = [SPEC_MODEL] if SPEC_MODEL else None
    try:
        raw = await asyncio.wait_for(
            chat_complete(
                system_message=INSTRUCTION_SPEC_SYSTEM,
                user_text=instruction_spec_prompt(text, context=context),
                provider_chain=chain,
            ),
            timeout=SPEC_TIMEOUT,
        )
        data = parse_json_response(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[spec] {ref or '-'} parse failed, instructions stay prose-only: {e}"
        )
        return empty_spec()

    spec = normalise(data if isinstance(data, dict) else None)
    if not is_empty(spec):
        logger.info(
            f"[spec] {ref or '-'} total={spec['total_questions']} "
            f"formats={spec['format_counts']} "
            f"sections={spec['section_difficulty']} "
            f"overrides={len(spec['question_overrides'])} "
            f"out_of_syllabus={spec['allow_out_of_syllabus']}"
        )
    return spec


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(sections: list, spec: dict | None) -> dict:
    """Did the paper actually do what the teacher asked?

    Written onto the paper document as `instruction_check`. The point is to turn
    "it is not taking my instructions" from a judgement into a number the
    teacher and the logs can both see.

    What it can check is deliberately narrow: counts and difficulty labels.
    Nobody can check "as hard as possible", and this does not pretend to.
    """
    stats = {
        "requested": {},
        "actual": {},
        "honoured": True,
        "misses": [],
        "at": utcnow_iso(),
    }
    if is_empty(spec):
        return stats

    questions = [
        q
        for s in sections or []
        for q in (s.get("questions") or [])
        if isinstance(q, dict)
    ]

    total = spec.get("total_questions") or 0
    if total:
        stats["requested"]["total_questions"] = total
        stats["actual"]["total_questions"] = len(questions)
        if len(questions) != total:
            stats["misses"].append(
                f"asked for {total} questions, got {len(questions)}"
            )

    wanted_formats = spec.get("format_counts") or {}
    if wanted_formats:
        actual: dict = {}
        for q in questions:
            key = (q.get("format") or "").strip().lower()
            actual[key] = actual.get(key, 0) + 1
        stats["requested"]["format_counts"] = dict(wanted_formats)
        stats["actual"]["format_counts"] = actual
        for fmt, n in wanted_formats.items():
            got = actual.get(fmt, 0)
            if got != n:
                stats["misses"].append(f"asked for {n} {fmt}, got {got}")

    wanted_sections = spec.get("section_difficulty") or {}
    if wanted_sections:
        stats["requested"]["section_difficulty"] = dict(wanted_sections)
        seen: dict = {}
        for s in sections or []:
            title = (s.get("title") or "").strip()
            match = _match_section(title, wanted_sections)
            if match is None:
                continue
            levels = {
                (q.get("difficulty") or "").lower()
                for q in (s.get("questions") or [])
                if isinstance(q, dict)
            }
            seen[match] = sorted(levels)
        stats["actual"]["section_difficulty"] = seen
        for name in wanted_sections.keys() - seen.keys():
            stats["misses"].append(f"requested section '{name}' is missing")

    overrides = spec.get("question_overrides") or []
    if overrides:
        stats["requested"]["question_overrides"] = [
            {"n": o["n"], "difficulty": o["difficulty"]} for o in overrides
        ]
        got: dict = {}
        for o in overrides:
            idx = o["n"] - 1
            if idx >= len(questions):
                stats["misses"].append(
                    f"question {o['n']} was requested but the paper has only "
                    f"{len(questions)} questions"
                )
                continue
            actual_level = (questions[idx].get("difficulty") or "").lower()
            got[str(o["n"])] = actual_level
            # A note with no difficulty ("use SI units") is unverifiable; only
            # an explicit level is checked.
            if o["difficulty"] and actual_level != o["difficulty"]:
                stats["misses"].append(
                    f"question {o['n']} should be '{o['difficulty']}', "
                    f"is '{actual_level or 'unset'}'"
                )
        stats["actual"]["question_overrides"] = got

    stats["misses"].extend(check_ui_requirements(sections, spec))
    # Explicit section and question rules override topic defaults.
    index = 0
    overrides_by_n = {o["n"]: o.get("difficulty") for o in overrides}
    for section in sections or []:
        match = _match_section(section.get("title", ""), wanted_sections)
        for question in section.get("questions", []):
            index += 1
            expected = overrides_by_n.get(index) or (
                wanted_sections.get(match) if match else None
            ) or (spec.get("format_difficulty") or {}).get(question.get("format")) or (
                spec.get("topic_difficulty") or {}).get(question.get("topic"))
            if expected and question.get("difficulty") != expected:
                stats["misses"].append(f"question {index} should be '{expected}', is '{question.get('difficulty', 'unset')}'")
    stats["honoured"] = not stats["misses"]
    return stats


_SECTION_LABEL_RE = re.compile(r"\bsection\s+([A-Za-z0-9]+)", re.IGNORECASE)


def _match_section(title: str, wanted: dict) -> str | None:
    """Map a generated section title onto a key the teacher used.

    A teacher writes "Section A"; the model titles it "Section A (20 Marks)" or
    just "A". Matching on the label alone is what makes those the same section.
    """
    if not title:
        return None
    for key in wanted:
        if key.strip().lower() == title.strip().lower():
            return key
    got = _SECTION_LABEL_RE.search(title)
    got_label = got.group(1).lower() if got else title.strip().lower()
    for key in wanted:
        want = _SECTION_LABEL_RE.search(key)
        want_label = want.group(1).lower() if want else key.strip().lower()
        if got_label == want_label:
            return key
    return None
