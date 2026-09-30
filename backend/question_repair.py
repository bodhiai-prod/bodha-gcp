"""Completeness checking and repair for generated questions.

Three layers, cheapest first:

1. ``validate_question()`` -- a deterministic structural check that needs no
   model at all: empty or truncated stems, MCQs that did not arrive with four
   usable options, an answer key pointing at nothing. Always runs.
2. ``complete_questions()`` -- sends ONLY the items that fail to the LLM, all of
   them in one batched call, and asks it to finish what is missing.
3. ``_accept()`` -- re-checks every returned question and refuses anything that
   rewrote the parts which arrived intact.

The split exists for cost, exactly as in latex_repair: a paper whose questions
are all complete never reaches step 2, so checking is free for the large
majority of generations.

This module is deliberately the sibling of latex_repair.py and copies its
shape -- same env-var naming, same stats discipline, same guarantee that
nothing here raises. A question that cannot be completed safely is left exactly
as it was found and counted in the ``unrepaired`` stat, because a half-written
question a teacher can see and fix beats a plausible-looking one the model
invented around it.

The one place this module is allowed more licence than latex_repair: finishing
an options list means knowing the answer, so the repair model does get to reason
about the subject. Everything it returns is merged field-by-field rather than
swapped in wholesale -- see _REPAIRABLE_FIELDS.
"""
from __future__ import annotations

import asyncio
import difflib
import os
import re

from deps import logger, utcnow_iso
from llm_adapter import chat_complete, parse_json_response
from prompts import QUESTION_REPAIR_SYSTEM, question_repair_prompt

# latex_repair already owns "what counts as the same number" -- it expands
# Unicode digits before extracting, which a second implementation here would
# get wrong the first time a superscript slipped through.
from latex_repair import _numbers as _latex_numbers

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# false = validator only. The kill switch: generation and the completeness
# stats keep working, they just stop making repair calls.
REPAIR_ENABLED = os.environ.get("QUESTION_REPAIR_ENABLED", "true").strip().lower() not in (
    "false", "0", "no",
)
# Blank means "use whatever chain TEXT_PROVIDER already serves".
REPAIR_MODEL = os.environ.get("QUESTION_REPAIR_MODEL", "").strip()
# Items per completion call. Lower than latex_repair's 40 because a whole
# question object costs several times what a single text span does.
REPAIR_MAX_ITEMS = int(os.environ.get("QUESTION_REPAIR_MAX_ITEMS", "20"))
# Budget for one completion call. Enforced here rather than in chat_complete so
# the pass can never hold up a generation for longer than its own share.
REPAIR_TIMEOUT = float(os.environ.get("QUESTION_REPAIR_TIMEOUT", "90"))
# How closely a continued stem must still match the fragment it grew from before
# we assume the model restarted the question instead of finishing it.
SIMILARITY_FLOOR = float(os.environ.get("QUESTION_REPAIR_SIMILARITY_FLOOR", "0.75"))

# Every MCQ in this app is a 4-option MCQ: the prompt says so (prompts.py
# FORMAT_CONVENTIONS), the normalisers slice to 4, and the renderers show 4.
MCQ_OPTIONS = 4
# Shorter than this is not a question, it is a fragment.
MIN_STEM_CHARS = 10

# The only fields a completion may touch. Everything else on the question --
# id, topic, marks, type, difficulty, format, needs_diagram, diagram_spec,
# diagram_status -- is carried over from the original untouched, so a model that
# decides to relabel the topic or award itself 10 marks simply cannot.
_REPAIRABLE_FIELDS = ("question", "options", "correct_option")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
# Words and punctuation that cannot legitimately end a question. This is
# deliberately a list of STRONG signals only. A stem with no terminal
# punctuation at all ("Calculate the value of x") is perfectly normal model
# output, so absence of a full stop is NOT treated as truncation -- flagging it
# would send most of a healthy paper to the repair model for nothing.
_DANGLING_WORDS = (
    "a an and are as at be but by for from if in into is of on or so than that "
    "the then to was were when where which while who with without"
).split()
_DANGLING_RE = re.compile(
    r"(?:[,;(\[–—-]|\b(?:" + "|".join(_DANGLING_WORDS) + r")\b)\s*$",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip())


def _stem(q: dict) -> str:
    text = q.get("question") if isinstance(q, dict) else None
    return text.strip() if isinstance(text, str) else ""


def _options(q: dict) -> list:
    opts = q.get("options") if isinstance(q, dict) else None
    return opts if isinstance(opts, list) else []


def is_mcq(q: dict, strict_mcq: bool = False) -> bool:
    """Whether this question must satisfy the MCQ rules.

    `strict_mcq` mirrors competitive_routes: the preset exams are locked to
    100% MCQ, so every question on those papers is one whether or not the model
    remembered to label it.
    """
    if strict_mcq:
        return True
    if not isinstance(q, dict):
        return False
    return (q.get("format") or "").lower() == "mcq"


def validate_question(q: dict, *, strict_mcq: bool = False) -> list:
    """Report everything structurally wrong with one question, as issue codes.

    Free, deterministic, no I/O. LaTeX and markup are NOT checked here --
    latex_repair.validate() owns those and runs immediately afterwards.
    """
    if not isinstance(q, dict):
        return ["empty_stem"]

    issues: set = set()

    stem = _stem(q)
    if len(stem) < MIN_STEM_CHARS:
        issues.add("empty_stem")
    elif _DANGLING_RE.search(stem) and not (
        is_mcq(q, strict_mcq) and re.search(r"\b(?:is|are|was|were|be)\s*$", stem, re.IGNORECASE)
        and len(_options(q)) == MCQ_OPTIONS
        and all(str(option).strip() for option in _options(q))
    ):
        issues.add("truncated_stem")

    if is_mcq(q, strict_mcq):
        opts = _options(q)
        if len(opts) != MCQ_OPTIONS:
            issues.add("mcq_missing_options")
        if any(not str(o).strip() for o in opts):
            issues.add("blank_option")
        filled = [str(o).strip().lower() for o in opts if str(o).strip()]
        if len(set(filled)) != len(filled):
            issues.add("duplicate_options")
        try:
            co = int(q.get("correct_option"))
        except (TypeError, ValueError):
            issues.add("bad_correct_option")
        else:
            # An index that is valid for a short list stays valid once the
            # missing options are appended, so a 2-option MCQ pointing at 1 is
            # only guilty of mcq_missing_options.
            if not 0 <= co < max(len(opts), 1):
                issues.add("bad_correct_option")

    return sorted(issues)


# ---------------------------------------------------------------------------
# Guardrails on model output
# ---------------------------------------------------------------------------
def _merge(original: dict, proposed: dict) -> dict:
    """Original question with only the repairable fields taken from the model."""
    out = dict(original)
    for field in _REPAIRABLE_FIELDS:
        if field in proposed:
            out[field] = proposed[field]
    return out


def _numbers_preserved(original: str, fixed: str) -> bool:
    """Every number in `original` still present in `fixed` (multiset subset).

    Subset rather than equality: continuing a truncated stem may legitimately
    introduce new quantities, but may never lose or alter the ones already
    stated.
    """
    remaining = list(_latex_numbers(fixed))
    for n in _latex_numbers(original):
        if n not in remaining:
            return False
        remaining.remove(n)
    return True


def _options_preserved(original: list, fixed: list) -> tuple:
    """Options that arrived usable must survive at the same index.

    Index stability is what keeps the answer key meaning the same thing: if the
    model reorders the choices while appending the missing ones, correct_option
    silently starts pointing at a different answer. Blank and duplicate entries
    are exempt, because replacing those is the whole point of the repair.
    """
    seen: set = set()
    for idx, opt in enumerate(original):
        text = str(opt).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        if idx >= len(fixed) or _norm_ws(str(fixed[idx])) != _norm_ws(text):
            return False, f"option_{idx}_changed"
    return True, ""


def _accept(original: dict, fixed: dict, issues: list, *, strict_mcq: bool = False) -> tuple:
    """Whether a model-returned completion may replace the original.

    A model asked to add two options that instead rewrites the question does
    more damage than the gap it was asked to fill, so a completion has to prove
    it finished the item rather than replaced it.
    """
    remaining = validate_question(fixed, strict_mcq=strict_mcq)
    if remaining:
        return False, "still_incomplete (%s)" % ",".join(remaining)

    orig_stem, new_stem = _stem(original), _stem(fixed)
    stem_was_broken = "empty_stem" in issues or "truncated_stem" in issues

    if not stem_was_broken:
        # The stem arrived intact. It is not the model's to touch.
        if _norm_ws(new_stem) != _norm_ws(orig_stem):
            return False, "stem_rewritten"
    elif orig_stem:
        # A continued stem must still open with what it grew from.
        head = _norm_ws(new_stem)[: len(_norm_ws(orig_stem))]
        ratio = difflib.SequenceMatcher(None, _norm_ws(orig_stem), head).ratio()
        if ratio < SIMILARITY_FLOOR:
            return False, "stem_not_continued (%.2f)" % ratio
        if not _numbers_preserved(orig_stem, new_stem):
            return False, "numbers_changed"

    if is_mcq(fixed, strict_mcq):
        ok, why = _options_preserved(_options(original), _options(fixed))
        if not ok:
            return False, why
        # An answer that was already valid must still name the same option --
        # but only if it was chosen against the full list of choices. An index
        # picked when two of the four options had not been written yet carries
        # no authority: the real answer may well be one of the ones being added,
        # so a completion is allowed to move it. That re-derivation is logged as
        # unverified by the caller, exactly like a bad_correct_option repair.
        if not {"bad_correct_option", "mcq_missing_options"} & set(issues):
            try:
                before = str(_options(original)[int(original["correct_option"])]).strip()
                after = str(_options(fixed)[int(fixed["correct_option"])]).strip()
            except (KeyError, IndexError, TypeError, ValueError):
                before = after = None
            if before is not None and _norm_ws(before) != _norm_ws(after):
                return False, "answer_key_moved"

    return True, ""


# ---------------------------------------------------------------------------
# LLM completion pass
# ---------------------------------------------------------------------------
async def _llm_complete(items: list, context: dict | None) -> dict:
    """One batched completion call. Returns id -> question dict; {} on failure.

    Reuses chat_complete so retries, model fallback and token accounting are
    inherited rather than reimplemented.
    """
    chain = [REPAIR_MODEL] if REPAIR_MODEL else None
    try:
        raw = await asyncio.wait_for(
            chat_complete(
                system_message=QUESTION_REPAIR_SYSTEM,
                user_text=question_repair_prompt(items, context=context),
                provider_chain=chain,
            ),
            timeout=REPAIR_TIMEOUT,
        )
        data = parse_json_response(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qcomplete] completion call failed, keeping originals: {e}")
        return {}
    out: dict = {}
    for fix in (data.get("fixes") if isinstance(data, dict) else None) or []:
        if not isinstance(fix, dict):
            continue
        fid, question = fix.get("id"), fix.get("question")
        if fid is not None and isinstance(question, dict):
            out[str(fid)] = question
    return out


def empty_stats(checked: int = 0) -> dict:
    """The `completeness_check` shape, for papers that never ran the pass."""
    return {
        "checked": checked,
        "incomplete": 0,
        "llm_fixed": 0,
        "unrepaired": 0,
        "dropped": 0,
        "issues": [],
        "at": utcnow_iso(),
    }


async def complete_questions(
    questions: list,
    *,
    ref: str = "",
    strict_mcq: bool = False,
    context: dict | None = None,
    flags: dict | None = None,
) -> dict:
    """Check and complete a flat list of question dicts, in place.

    `flags` is the map the normalisers fill in when they silently coerce a value
    -- without it a correct_option that was rewritten to 0 looks valid here, and
    the wrong answer key sails straight through.

    Returns a stats dict written straight onto the Mongo document as
    `completeness_check`; the collection is schemaless and everything is written
    with $set, so no migration is needed.
    """
    questions = [q for q in (questions or []) if isinstance(q, dict)]
    stats = empty_stats(len(questions))
    if not questions:
        return stats

    broken: list = []
    seen_issues: set = set()

    for idx, q in enumerate(questions):
        issues = validate_question(q, strict_mcq=strict_mcq)
        if "coerced_correct_option" in ((flags or {}).get(q.get("id")) or []):
            if "bad_correct_option" not in issues:
                issues.append("bad_correct_option")
                issues.sort()
        if not issues:
            continue
        seen_issues.update(issues)
        broken.append({
            "id": str(q.get("id") or f"idx{idx}"),
            "issues": issues,
            "question": q,
        })

    stats["incomplete"] = len(broken)

    if broken and REPAIR_ENABLED:
        for start in range(0, len(broken), REPAIR_MAX_ITEMS):
            chunk = broken[start:start + REPAIR_MAX_ITEMS]
            proposals = await _llm_complete(chunk, context)
            for item in chunk:
                proposed = proposals.get(item["id"])
                if not isinstance(proposed, dict):
                    continue
                merged = _merge(item["question"], proposed)
                key_moved = merged.get("correct_option") != item["question"].get(
                    "correct_option"
                )
                ok, why = _accept(
                    item["question"], merged, item["issues"], strict_mcq=strict_mcq
                )
                if not ok:
                    logger.info(
                        f"[qcomplete] {ref or '-'} rejected completion for "
                        f"{item['id']}: {why}"
                    )
                    continue
                for field in _REPAIRABLE_FIELDS:
                    if field in merged:
                        item["question"][field] = merged[field]
                item["repaired"] = True
                stats["llm_fixed"] += 1
                if key_moved or "bad_correct_option" in item["issues"]:
                    # The model had to re-derive this answer and there is no
                    # cheap way to check it. Better than the silent coerce-to-0
                    # it replaces, but it is worth being able to find later.
                    logger.info(
                        f"[qcomplete] {ref or '-'} answer key re-derived for "
                        f"{item['id']} (unverified)"
                    )

    stats["unrepaired"] = sum(1 for item in broken if not item.get("repaired"))
    stats["issues"] = sorted(seen_issues)
    if broken:
        logger.info(
            f"[qcomplete] {ref or '-'} checked={stats['checked']} "
            f"incomplete={stats['incomplete']} llm_fixed={stats['llm_fixed']} "
            f"unrepaired={stats['unrepaired']} issues={','.join(stats['issues'])}"
        )
    return stats


async def complete_sections(
    sections: list,
    *,
    ref: str = "",
    strict_mcq: bool = False,
    context: dict | None = None,
    flags: dict | None = None,
) -> dict:
    """Check and complete every question across all sections, in place.

    Call this AFTER the normaliser (so ids exist and formats are settled) and
    BEFORE latex_repair.repair_sections, so options written by the completion
    pass get the same LaTeX gate as the ones the first call produced.
    """
    questions = [
        q
        for s in sections or []
        for q in (s.get("questions") or [])
        if isinstance(q, dict)
    ]
    return await complete_questions(
        questions, ref=ref, strict_mcq=strict_mcq, context=context, flags=flags
    )


def alternative_issues(question):
    """Validate every independently printed branch, including empty last branches."""
    from section_blueprints import _OR_SEPARATOR_RE, _BREAK_TAG_RE
    text = _BREAK_TAG_RE.sub('\n', str(question.get('question') or ''))
    parts = [part.strip() for part in _OR_SEPARATOR_RE.split(text)]
    if len(parts) < 2:
        return []
    issues = [issue for part in parts for issue in validate_question({**question, 'question': part})]
    if len({part.casefold() for part in parts}) != len(parts):
        issues.append('duplicate_alternatives')
    return sorted(set(issues))
