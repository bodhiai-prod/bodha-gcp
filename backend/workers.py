"""Background workers — long-running LLM jobs that run off the request loop
to bypass the 60s ingress proxy timeout.

These functions are imported and kicked off via `asyncio.create_task(...)` from
the API endpoints in `server.py`.
"""
from __future__ import annotations

import asyncio as _asyncio
import copy
import json
import os
import re
import uuid
from typing import Optional

from fastapi import HTTPException

from deps import db, logger, utcnow_iso
from activity import record_activity
from diagram_policy import apply_diagram_policy, normalise_spec
from latex_repair import repair_answers, repair_sections, repair_text
from llm_adapter import (
    MalformedProviderResponseError,
    chat_complete,
    generate_diagram,
    parse_json_response,
)
from prompts import (
    derive_target_question_count,
    normal_slots_prompt,
    regen_question_prompt,
    qgen_prompt,
    solution_prompt,
    REGEN_SYSTEM,
    SOLUTION_SYSTEM,
    STEM_CHARS,
)
from instruction_spec import parse_instructions, verify as verify_instructions
from export_utils import canonical_question_type, printable_instructions
from section_blueprints import (
    allocation_misses,
    attemptable_marks,
    compact_validation_misses,
    derived_internal_choice_count,
    internal_choice_misses,
    normalize_internal_choice_counts,
    hydrate_section_instructions,
    normalize_template_sections,
    printable_template_instructions,
)
from generation_requirements import compile_requirements, remaining_requirements
from section_blueprints import allocation_coverage
from question_dedupe import dedupe_sections, is_duplicate
from question_repair import complete_sections, validate_question
from storage import APP_NAME, put_object
from textbook_content import load_textbook_chunks
from topic_balance import measure_coverage, topic_shortfall
from token_usage import tag
from usage_ledger import tracked
from review_budget import begin as begin_review_budget
from normal_paper_contract import (
    NORMAL_CONTRACT_VERSION,
    compile_normal_plan,
    materialize_normal_sections,
    merge_normal_sections,
    missing_plan_slots,
    normal_slot_batches,
    normalize_normal_sections,
    review_status as normal_review_status,
    validate_normal_plan,
)
from case_study_contract import case_groups, case_issues, review_cases, review_misses

ACTIVE_GENERATION_STATUSES = ["queued", "pending", "generating", "reviewing"]


def _screen_viewable_sections(
    sections: list,
    *,
    spec: dict,
    normal_plan: dict | None = None,
    case_review_misses: list[dict] | None = None,
) -> tuple[list, list[str]]:
    """Return only question blocks that are safe and coherent enough to preview.

    The draft view is deliberately more permissive than the final contract
    gate, but it never renders truncated stems, invalid MCQs, broken math, or a
    question stamped into the wrong immutable slot. Those questions remain in
    the recovery candidate and can be repaired after the teacher opens the
    usable parts of the paper.
    """
    screened = copy.deepcopy(sections or [])
    issues: list[str] = []
    expected_slots = {
        slot.get("slot_id"): slot
        for slot in (normal_plan or {}).get("slots") or []
    }
    allocation_rows = {
        row.get("allocation_id"): row
        for row in spec.get("section_allocations") or []
    }
    required_choice_groups = {
        (section.get("id"), group.get("id")): int(group.get("internal_choices") or 0)
        for section in ((spec.get("blueprint") or {}).get("sections") or [])
        for group in (section.get("groups") or [])
        if group.get("internal_choices") is not None
    }
    pending_choice_allocations = set()
    invalid_case_parents = set()
    case_questions = {}
    for section in screened:
        for question in section.get("questions") or []:
            row = allocation_rows.get((question or {}).get("allocation_id"))
            if row and row.get("case_study") and row.get("parent_id"):
                case_questions.setdefault((row.get("section_id"), row.get("parent_id")), []).append(question)
    for parent, questions in case_questions.items():
        valid_stimuli = [question for question in questions if str(question.get("shared_stimulus") or "").strip()]
        if len(valid_stimuli) != 1:
            invalid_case_parents.add(parent)
    invalid_case_parents.update((issue.get("section_id"), issue.get("parent_id")) for issue in (case_review_misses or []))
    if required_choice_groups:
        choice_counts = {key: 0 for key in required_choice_groups}
        choice_ids = {key: set() for key in required_choice_groups}
        for section in screened:
            for question in section.get("questions") or []:
                row = allocation_rows.get((question or {}).get("allocation_id"))
                if not row:
                    continue
                key = (row.get("section_id"), row.get("group_id"))
                if key not in required_choice_groups:
                    continue
                choice_counts[key] += derived_internal_choice_count(question)
                choice_ids[key].add(row.get("allocation_id"))
        for key, expected in required_choice_groups.items():
            if choice_counts[key] != expected:
                pending_choice_allocations.update(choice_ids[key])
    for section in screened:
        safe_questions = []
        for question in section.get("questions") or []:
            if not isinstance(question, dict):
                issues.append("A generated question was not a valid question object.")
                continue
            qid = question.get("id") or question.get("slot_id") or "unknown"
            question["question"], stem_issues = repair_text(str(question.get("question") or ""))
            options = question.get("options")
            if isinstance(options, list):
                fixed_options = []
                option_issues = []
                for option in options:
                    fixed, found = repair_text(str(option or ""))
                    fixed_options.append(fixed)
                    option_issues.extend(found)
                question["options"] = fixed_options
            else:
                option_issues = []
            structural_issues = validate_question(question)
            if stem_issues or option_issues or structural_issues:
                codes = sorted(set([*stem_issues, *option_issues, *structural_issues]))
                issues.append(f"Question {qid} is withheld until repaired ({', '.join(codes)}).")
                continue

            slot = expected_slots.get(question.get("slot_id"))
            if normal_plan and (not slot or any(
                question.get(field) != slot.get(field)
                for field in ("section_id", "topic", "type", "format", "difficulty", "marks")
            )):
                issues.append(f"Question {qid} is withheld until its paper slot is repaired.")
                continue

            row = allocation_rows.get(question.get("allocation_id"))
            if row and (row.get("section_id"), row.get("parent_id")) in invalid_case_parents:
                issues.append(f"Question {qid} is withheld until its shared case stimulus is repaired.")
                continue
            if question.get("allocation_id") in pending_choice_allocations:
                issues.append(f"Question {qid} has pending required internal choices.")
                question["completion_pending"] = "Internal OR alternative pending"
            if spec.get("section_allocations") and (
                not row
                or any(question.get(field) != row.get(field) for field in (
                    "textbook_id", "topic_id", "group_id", "slot_id", "marks", "format", "difficulty"
                ))
                or question.get("topic") != row.get("name")
                or question.get("section_id") != row.get("section_id")
                or section.get("section_id") != row.get("section_id")
            ):
                issues.append(f"Question {qid} is withheld until its template allocation is repaired.")
                continue
            safe_questions.append(question)
        section["questions"] = safe_questions
    return [section for section in screened if section.get("questions")], issues


async def _review_template_case_studies(sections: list, spec: dict, *, ref: str) -> list[dict]:
    """Review assembled template cases; fail closed only for their parent."""
    plan = {"slots": list(spec.get("section_allocations") or [])}
    deterministic = case_issues(sections, plan)
    groups = case_groups(sections, plan)
    if not groups:
        return deterministic
    try:
        cache = await review_cases(sections, plan, _request_paper_json, ref=f"{ref}:case-review")
        return [*deterministic, *review_misses(sections, plan, cache)]
    except Exception as exc:
        return [*deterministic, *[
            {"code": "case_semantic", "section_id": group["section_id"], "parent_id": group["parent_id"],
             "slot_ids": [slot.get("slot_id") for slot in group["slots"]],
             "message": f"Case-study review could not complete: {str(exc)[:160]}"}
            for group in groups
        ]]


def _preview_allowed(sections, spec, target):
    total = int(target or spec.get("total_questions") or 0)
    if not total:
        return False
    missing = max(0, total - count_questions(sections))
    try:
        pending = len(internal_choice_repair_targets(sections, spec))
    except RegenerationFailed:
        return False
    unresolved = missing + pending
    return unresolved <= 6 and (total - unresolved) / total >= .85


async def _complete_and_repair_sections_parallel(
    sections: list,
    *,
    ref: str,
    context: dict,
    flags: dict,
) -> tuple[dict, dict]:
    """Run independent completion and LaTeX passes concurrently.

    Both passes start from an immutable snapshot.  A question completed by the
    first pass wins over the preliminary LaTeX patch, then receives a small
    second LaTeX pass.  That retains the required dependency without making a
    whole clean-paper LaTeX pass wait behind a slow completion model call.
    """
    original = copy.deepcopy(sections)
    completion_copy = copy.deepcopy(sections)
    latex_copy = copy.deepcopy(sections)
    completeness, preliminary_latex = await _asyncio.gather(
        complete_sections(completion_copy, ref=ref, context=context, flags=flags),
        repair_sections(latex_copy, ref=ref),
    )
    original_by_id = {
        q.get("id"): q for section in original for q in section.get("questions") or []
        if isinstance(q, dict) and q.get("id")
    }
    completed_by_id = {
        q.get("id"): q for section in completion_copy for q in section.get("questions") or []
        if isinstance(q, dict) and q.get("id")
    }
    latex_by_id = {
        q.get("id"): q for section in latex_copy for q in section.get("questions") or []
        if isinstance(q, dict) and q.get("id")
    }
    changed = {
        qid for qid, question in completed_by_id.items()
        if question != original_by_id.get(qid)
    }
    for qid, question in completed_by_id.items():
        repaired = latex_by_id.get(qid)
        if qid not in changed and repaired:
            question["question"] = repaired.get("question", question.get("question"))
            if isinstance(question.get("options"), list) and isinstance(repaired.get("options"), list):
                question["options"] = repaired["options"]
    sections[:] = completion_copy
    if changed:
        changed_sections = [{
            "questions": [q for q in section.get("questions") or [] if q.get("id") in changed]
        } for section in sections]
        follow_up = await repair_sections(changed_sections, ref=f"{ref}:completed")
        preliminary_latex["checked"] = int(preliminary_latex.get("checked") or 0) + int(follow_up.get("checked") or 0)
        preliminary_latex["auto_fixed"] = int(preliminary_latex.get("auto_fixed") or 0) + int(follow_up.get("auto_fixed") or 0)
        preliminary_latex["llm_fixed"] = int(preliminary_latex.get("llm_fixed") or 0) + int(follow_up.get("llm_fixed") or 0)
        preliminary_latex["unrepaired"] = int(follow_up.get("unrepaired") or 0)
        preliminary_latex["issues"] = sorted(set(preliminary_latex.get("issues") or []) | set(follow_up.get("issues") or []))
    return completeness, preliminary_latex


# ---------------------------------------------------------------------------
# Feedback hints — short summaries of past teacher edits, fed back into prompts
# ---------------------------------------------------------------------------
async def load_paper_feedback_hints(user_id: str, subject: str, class_name: str) -> str:
    """Build a short feedback-hints block from recent paper edits."""
    try:
        edits = (
            await db.paper_edits.find(
                {"owner_id": user_id, "subject": subject, "class_name": class_name},
                {"_id": 0},
            )
            .sort("created_at", -1)
            .to_list(8)
        )
    except Exception:
        return ""
    if not edits:
        return ""
    lines = []
    for e in edits:
        kind = e.get("edit_type")
        if kind == "modify_question":
            orig = (e.get("original") or "")[:150]
            new = (e.get("revised") or "")[:150]
            if orig and new:
                lines.append(f"- TEACHER REPHRASED: '{orig}'  →  '{new}'")
        elif kind == "delete_question":
            q = (e.get("original") or "")[:150]
            if q:
                lines.append(f"- TEACHER REMOVED style: '{q}' (avoid similar)")
        elif kind == "add_question":
            q = (e.get("revised") or "")[:150]
            if q:
                lines.append(f"- TEACHER ADDED style: '{q}' (prefer similar)")
        elif kind == "regenerate_question":
            # A regenerate is a deliberate rejection of one question with its
            # slot kept -- not a deletion, so it gets its own wording. Without
            # this branch the row would still occupy one of the eight slots the
            # query returns and contribute nothing, displacing one that would.
            orig = (e.get("original") or "")[:150]
            new_q = (e.get("revised") or "")[:150]
            if orig:
                lines.append(
                    f"- TEACHER REGENERATED away from: '{orig}'"
                    + (f"  →  accepted instead: '{new_q}'" if new_q
                       else " (avoid similar)")
                )
    return "\n".join(lines[:10])


async def load_solution_feedback(user_id: str, subject: str, class_name: str) -> str:
    try:
        edits = (
            await db.solution_edits.find(
                {"owner_id": user_id, "subject": subject, "class_name": class_name},
                {"_id": 0},
            )
            .sort("created_at", -1)
            .to_list(8)
        )
    except Exception:
        return ""
    if not edits:
        return ""
    lines = []
    for e in edits:
        orig = (e.get("original") or "")[:180]
        new = (e.get("revised") or "")[:180]
        if orig and new:
            lines.append(f"- TEACHER REWORDED ANSWER: '{orig}'  →  '{new}'")
    return "\n".join(lines[:10])


# ---------------------------------------------------------------------------
# Diagram generation
# ---------------------------------------------------------------------------
async def generate_diagrams_for_paper(paper_id: str, owner_id: str, sections: list) -> list:
    """Generate diagrams (in parallel) for questions with needs_diagram=true.
    Saves PNGs to object storage and attaches `diagram_path` to each question.
    Best-effort: a failed diagram leaves the question without an image.

    The policy gate runs again here rather than relying on the caller's pass:
    this is the last point before an image is actually paid for, and the
    paper may have been edited between generation and this call.
    """
    apply_diagram_policy(sections, ref=paper_id, max_diagrams=sum(
        bool(q.get("needs_diagram")) for section in sections for q in section.get("questions") or []
    ))

    jobs = []
    targets = []
    for s in sections:
        for q in s.get("questions", []):
            if not q.get("needs_diagram"):
                continue
            # Already has an image. Re-running this job (a top-up appends new
            # questions and re-enters here) must not re-bill every existing
            # figure.
            if q.get("diagram_path"):
                continue
            spec = normalise_spec(q)
            if spec is None:
                continue
            targets.append(q)
            jobs.append(generate_diagram(spec))
    if not jobs:
        return sections
    results = await _asyncio.gather(*jobs, return_exceptions=True)
    for q, img_bytes in zip(targets, results):
        if isinstance(img_bytes, Exception) or not img_bytes:
            continue
        try:
            import hashlib
            version = hashlib.sha256(img_bytes).hexdigest()
            path = f"{APP_NAME}/diagrams/{owner_id}/{paper_id}/{q['id']}/{version}.png"
            saved = put_object(path, img_bytes, "image/png")
            q["diagram_path"] = saved["path"]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to save diagram: {e}")
    return sections


async def generate_diagrams_background(paper_id: str, owner_id: str) -> None:
    """Run diagram generation for a paper and update its sections in-place."""
    try:
        p = await db.papers.find_one(
            {"id": paper_id, "is_deleted": False}, {"_id": 0}
        )
        if not p:
            return
        sections = p.get("sections", [])
        sections = await generate_diagrams_for_paper(paper_id, owner_id, sections)
        # Mark statuses
        for s in sections:
            for q in s.get("questions", []):
                if q.get("diagram_path"):
                    q["diagram_status"] = "ready"
                elif q.get("diagram_status") == "pending":
                    q["diagram_status"] = "failed"
        await db.papers.update_one(
            {"id": paper_id},
            {"$set": {"sections": sections, "diagrams_pending": 0}},
        )
        logger.info(f"Diagram background job finished for {paper_id}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Diagram background job failed: {e}")


def _flag(flags: Optional[dict], q: dict, reason: str) -> None:
    """Record a value the normaliser had to invent, if anyone is collecting."""
    if flags is None:
        return
    flags.setdefault(q.get("id"), []).append(reason)


def _normalise_paper_questions(
    sections: list, difficulty: str, flags: Optional[dict] = None,
    assign_ids: bool = True,
) -> None:
    """Stamp ids and fill in the defaults every question is expected to have.

    Shared by first-pass generation and the top-up worker so the two cannot
    drift apart on MCQ handling or default marks. Mutates in place.

    `assign_ids` off keeps whatever id a question already carries. The single-
    question regeneration path needs that: the replacement occupies the slot the
    old question held, and its id is what ties it to the answer key, the diagram
    it may already own and the star a teacher put on it.

    When `flags` is passed it collects question_id -> [reason] for every
    value this function had to invent. The coercion below is lossy on
    purpose (a paper still renders with a wrong answer key), so without
    somewhere to record it, question_repair has no way to tell a
    correct_option the model chose from one this function made up.
    """
    for s in sections:
        for q in s.get("questions", []):
            if assign_ids or not q.get("id"):
                q["id"] = str(uuid.uuid4())
            q.setdefault("important", False)
            q.setdefault("marks", 2)
            canonical_type = canonical_question_type(q.get("type"))
            if canonical_type is None:
                _flag(flags, q, "invalid_question_type")
                canonical_type = "concept"
            q["type"] = canonical_type
            q.setdefault("difficulty", difficulty)
            q.setdefault("needs_diagram", False)
            q.setdefault("format", "")
            # MCQ structured fields — keep options as a 4-string list and
            # correct_option as a 0-3 int. Drop them for non-MCQ items.
            if (q.get("format") or "").lower() == "mcq":
                opts = q.get("options") or []
                if isinstance(opts, list):
                    opts = [str(o).strip() for o in opts][:4]
                else:
                    opts = []
                q["options"] = opts
                try:
                    co = int(q.get("correct_option", 0))
                except (TypeError, ValueError):
                    co = 0
                    _flag(flags, q, "coerced_correct_option")
                if not 0 <= co < len(opts):
                    co = 0
                    _flag(flags, q, "coerced_correct_option")
                q["correct_option"] = co
            else:
                q.pop("options", None)
                q.pop("correct_option", None)
    # Do not trust provider bookkeeping for choice blocks.  This is repeated
    # after repair below because repair may alter the final printable text.
    normalize_internal_choice_counts(sections)


# ---------------------------------------------------------------------------
# Question paper generation
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Slot generation with live phase progress
# ---------------------------------------------------------------------------
# Ordinary papers up to 25 questions use one provider request. Larger papers
# use immutable slot batches of at most 20 with bounded parallelism; batching
# never recompiles topics, marks, formats, types, or section structure.
NORMAL_SINGLE_CALL_LIMIT = int(os.environ.get("NORMAL_SINGLE_CALL_LIMIT", "25"))
NORMAL_BATCH_SIZE = int(os.environ.get("NORMAL_BATCH_SIZE", "20"))
NORMAL_BATCH_CONCURRENCY = int(os.environ.get("NORMAL_BATCH_CONCURRENCY", "3"))
# One response-format retry absorbs a malformed/truncated provider reply
# without changing paper state. Validation failures are deliberately not
# retried here; their dedicated recovery paths retain the existing candidate.
PROVIDER_JSON_MAX_ATTEMPTS = int(os.environ.get("PROVIDER_JSON_MAX_ATTEMPTS", "2"))


async def _request_paper_json(*, system_message: str, user_text: str, ref: str, attempts: int | None = None) -> dict:
    """Request a JSON paper with bounded recovery for malformed provider text."""
    max_attempts = max(1, attempts or PROVIDER_JSON_MAX_ATTEMPTS)
    for attempt in range(1, max_attempts + 1):
        retry_suffix = (
            "\n\nRETRY FORMAT REQUIREMENT: Return one complete JSON object only. "
            'Escape every backslash inside JSON strings as "\\\\".'
            if attempt > 1 else ""
        )
        raw = await chat_complete(
            system_message=system_message,
            user_text=user_text + retry_suffix,
        )
        try:
            data = parse_json_response(raw)
        except MalformedProviderResponseError as exc:
            logger.warning(
                "Provider JSON parse failed for %s (attempt %s/%s; chars=%s): %s",
                ref or "-", attempt, max_attempts, len(raw or ""), exc,
            )
            if attempt == max_attempts:
                raise RuntimeError(
                    "The AI provider returned incomplete output after automatic recovery. "
                    "Please retry generation."
                ) from exc
            continue
        if not isinstance(data, dict):
            raise RuntimeError("The AI provider returned an unsupported paper response.")
        return data
    raise RuntimeError("The AI provider did not return a paper response.")


async def _generate_normal_slot_batch(
    *,
    slots: list[dict],
    plan: dict,
    subject: str,
    klass: str,
    context_excerpt: str,
    custom_instructions: str,
    feedback_hints: str,
    existing_questions: list,
    ref: str,
) -> list:
    """Generate one slot batch and retry only identities that were omitted."""
    prompt = normal_slots_prompt(
        subject=subject,
        klass=klass,
        slots=slots,
        context_excerpt=context_excerpt,
        custom_instructions=custom_instructions,
        feedback_hints=feedback_hints,
        existing_questions=existing_questions,
    )
    data = await _request_paper_json(
        system_message=(
            "Write original exam-question content for immutable server slots. "
            "Return strict JSON only."
        ),
        user_text=prompt,
        ref=ref,
    )
    sections, missing, _unknown = materialize_normal_sections(data, plan, only_slots=slots)
    if not missing:
        return sections

    missing_set = set(missing)
    missing_slots = [slot for slot in slots if slot["slot_id"] in missing_set]
    retry = await _request_paper_json(
        system_message=(
            "Fill only the missing immutable exam-question slots. Return strict JSON only."
        ),
        user_text=normal_slots_prompt(
            subject=subject,
            klass=klass,
            slots=missing_slots,
            context_excerpt=context_excerpt,
            custom_instructions=custom_instructions,
            feedback_hints=feedback_hints,
            existing_questions=[
                *existing_questions,
                *(q.get("question", "") for section in sections for q in section.get("questions") or []),
            ],
        ),
        ref=f"{ref}:missing-slots",
    )
    recovered, _still_missing, _unknown = materialize_normal_sections(
        retry, plan, only_slots=missing_slots
    )
    return merge_normal_sections(plan, sections, recovered)


async def _generate_normal_candidate(
    *,
    plan: dict,
    subject: str,
    klass: str,
    context_excerpt: str,
    custom_instructions: str,
    feedback_hints: str,
    ref: str,
    on_batch_complete=None,
    only_slots: list[dict] | None = None,
    existing_questions: list | None = None,
) -> dict:
    """Generate a normal paper in one call or bounded parallel slot batches."""
    slots = list(only_slots if only_slots is not None else plan.get("slots") or [])
    batches = normal_slot_batches(
        slots,
        single_call_limit=NORMAL_SINGLE_CALL_LIMIT,
        batch_size=NORMAL_BATCH_SIZE,
    )
    semaphore = _asyncio.Semaphore(max(1, NORMAL_BATCH_CONCURRENCY))
    completed = 0
    completed_batches = 0
    completed_sections: list = []
    completed_lock = _asyncio.Lock()

    async def run(index: int, batch: list[dict]) -> list:
        nonlocal completed, completed_batches
        async with semaphore:
            sections = await _generate_normal_slot_batch(
                slots=batch,
                plan=plan,
                subject=subject,
                klass=klass,
                context_excerpt=context_excerpt,
                custom_instructions=custom_instructions,
                feedback_hints=feedback_hints,
                existing_questions=list(existing_questions or []),
                ref=f"{ref}:slot-batch-{index + 1}",
            )
        async with completed_lock:
            completed += sum(len(section.get("questions") or []) for section in sections)
            completed_batches += 1
            completed_sections[:] = merge_normal_sections(
                plan, completed_sections, sections
            )
            if on_batch_complete:
                await on_batch_complete(
                    min(len(slots), completed), completed_batches, len(batches),
                    draft_sections=copy.deepcopy(completed_sections),
                )
        return sections

    results = await _asyncio.gather(
        *(run(index, batch) for index, batch in enumerate(batches)),
        return_exceptions=True,
    )
    valid = []
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Normal slot batch %s failed for %s: %s", index + 1, ref, result)
            continue
        valid.append(result)
    sections = merge_normal_sections(plan, *valid)
    if not count_questions(sections):
        raise RuntimeError(
            "The AI provider could not produce any usable question content "
            "after automatic recovery. Please retry generation."
        )
    return {"instructions": "", "sections": sections}


@tracked('question_generation')
async def generate_paper_background(
    paper_id: str,
    owner_id: str,
    subject: str,
    klass: str,
    topics_weighted: list,
    difficulty: str,
    distribution: dict,
    total_marks: int,
    duration_minutes: int,
    context_excerpt: str,
    feedback_hints: str,
    format_distribution: Optional[dict] = None,
    custom_instructions: str = "",
    section_blueprint: str = "",
    total_questions: Optional[int] = None,
    format_counts: Optional[dict] = None,
    diagram_count: Optional[int] = None,
    saved_requirements: Optional[dict] = None,
) -> None:
    """Run question generation off the request loop so the LLM call doesn't
    burst the 60s ingress timeout. Writes the result back to the paper doc
    and updates `generation_status`."""
    tag("paper", paper_id)
    # Parse the Additional Instructions box into counts BEFORE building the
    # prompt. Without this the box is prose competing with a slider-derived
    # quota that contradicts it, and the quota wins because it is the one
    # stated as a number.
    instruction_spec = {} if saved_requirements else await parse_instructions(
        custom_instructions,
        ref=paper_id,
        context={
            "subject": subject,
            "klass": klass,
            "total_marks": total_marks,
            "difficulty": difficulty,
        },
    )
    blueprint_spec = {} if saved_requirements else await parse_instructions(section_blueprint, ref=f"{paper_id}:blueprint")
    spec = saved_requirements or compile_requirements(
        total_marks=total_marks, distribution=distribution,
        format_distribution=format_distribution, topics=topics_weighted,
        difficulty=difficulty, total_questions=total_questions,
        format_counts=format_counts, diagram_count=diagram_count,
        instruction_spec=instruction_spec, blueprint_spec=blueprint_spec,
        has_blueprint=bool(section_blueprint.strip()),
    )
    normal_plan = None
    if (
        spec.get("total_questions")
        and not spec.get("section_allocations")
        and not (section_blueprint or "").strip()
    ):
        # Zero allocations are an apportionment implementation detail, never a
        # request the provider or teacher should be asked to satisfy.
        try:
            normal_plan = compile_normal_plan(spec, topics_weighted, total_marks)
            spec["topic_counts"] = dict(normal_plan.get("topic_counts") or {})
        except Exception as exc:  # configuration can also be changed by parsed instructions
            await db.papers.update_one(
                {"id": paper_id, "generation_status": {"$in": ACTIVE_GENERATION_STATUSES}},
                {"$set": {
                    "generation_status": "needs_review",
                    "generation_error": str(exc),
                    "generation_progress.status": "needs_review",
                    "generation_progress.phase": "needs_review",
                    "generation_progress.updated_at": utcnow_iso(),
                }},
            )
            return
    await db.papers.update_one({"id": paper_id}, {"$set": {
        "generation_requirements": spec,
        **({"normal_generation_plan": normal_plan} if normal_plan else {}),
    }})
    # Hoisted above the generation call because the batching decision needs it.
    # Same number the prompt is built from, or the coverage check below would
    # measure the paper against a target the model was never given.
    target_q = int(spec.get("total_questions") or 0) or (
        derive_target_question_count(total_marks)
    )
    prompt_kwargs = dict(
        subject=subject,
        klass=klass,
        topics_weighted=topics_weighted,
        difficulty=difficulty,
        distribution=distribution,
        total_marks=total_marks,
        duration=duration_minutes,
        context_excerpt=context_excerpt,
        feedback_hints=feedback_hints,
        format_distribution=format_distribution or {},
        custom_instructions=custom_instructions or "",
        section_blueprint=section_blueprint or "",
        spec=spec,
    )
    system_message = (
        "You create original, high-quality exam questions in strict JSON. "
        "Never copy textbook content."
    )
    # Templates retain their compiled blueprint contract. Ordinary papers use
    # the server-owned slot plan and never ask the provider to invent sections.
    batched = bool(normal_plan and len(normal_plan.get("slots") or []) > NORMAL_SINGLE_CALL_LIMIT)
    prompt = None if normal_plan else qgen_prompt(**prompt_kwargs)
    draft_published = False

    async def update_progress(
        completed: int, batch: int = 0, batch_total: int = 0,
        phase: str = "generating", draft_sections: list | None = None,
    ) -> None:
        nonlocal draft_published
        updates = {
            "generation_progress.completed": min(target_q, max(0, completed)),
            "generation_progress.total": target_q,
            "generation_progress.status": "pending",
            "generation_progress.phase": phase,
            "generation_progress.batch": batch,
            "generation_progress.batch_total": batch_total,
            "generation_progress.updated_at": utcnow_iso(),
            "quality_review.status": "running",
            "quality_review.stage": phase,
        }
        if draft_sections is not None:
            visible, withheld = _screen_viewable_sections(
                draft_sections, spec=spec, normal_plan=normal_plan
            )
            if _preview_allowed(visible, spec, target_q):
                draft_published = True
                updates.update({
                    "sections": visible,
                    "question_count": count_questions(visible),
                    "generation_status": "ready",
                    "generation_progress.status": "ready",
                    "generation_progress.phase": "repairing",
                    "quality_review.visible_question_count": count_questions(visible),
                    "quality_review.withheld_count": max(0, count_questions(draft_sections) - count_questions(visible)),
                    "quality_review.withheld_reasons": withheld[:20],
                    "quality_review.draft_available": True,
                })
        elif not draft_published:
            updates["generation_status"] = "reviewing" if phase in {"validating", "finalizing"} else "generating"
        await db.papers.update_one(
            {"id": paper_id, "generation_status": {"$in": [*ACTIVE_GENERATION_STATUSES, "ready"]}},
            {"$set": updates},
        )

    await update_progress(0, phase="generating")
    saved_draft = await db.papers.find_one(
        {"id": paper_id, "generation_status": "ready", "quality_review.status": "running"},
        {"_id": 0, "sections": 1, "instructions": 1},
    )
    resume_sections = copy.deepcopy((saved_draft or {}).get("sections") or [])
    resuming_visible_draft = bool(resume_sections)
    try:
        if resuming_visible_draft and normal_plan:
            missing_slots = missing_plan_slots(normal_plan, resume_sections)
            if missing_slots:
                async def publish_resumed_batch(completed, batch, batch_total, *, draft_sections):
                    cumulative = merge_normal_sections(
                        normal_plan, resume_sections, draft_sections
                    )
                    await update_progress(
                        completed, batch, batch_total, draft_sections=cumulative
                    )

                recovered_data = await _generate_normal_candidate(
                    ref=f"{paper_id}:resume",
                    plan=normal_plan,
                    subject=subject,
                    klass=klass,
                    context_excerpt=context_excerpt,
                    custom_instructions=custom_instructions,
                    feedback_hints=feedback_hints,
                    existing_questions=flatten_question_texts(resume_sections),
                    only_slots=missing_slots,
                    on_batch_complete=publish_resumed_batch,
                )
                data = {
                    **recovered_data,
                    "sections": merge_normal_sections(
                        normal_plan, resume_sections, recovered_data.get("sections") or []
                    ),
                    "instructions": (saved_draft or {}).get("instructions") or "",
                }
            else:
                data = {
                    "sections": merge_normal_sections(normal_plan, resume_sections),
                    "instructions": (saved_draft or {}).get("instructions") or "",
                }
        elif resuming_visible_draft:
            # Non-slot papers resume from the durable screened copy; any count
            # gap is handled by the targeted top-up after final checks.
            data = {
                "sections": resume_sections,
                "instructions": (saved_draft or {}).get("instructions") or "",
            }
        elif normal_plan:
            data = await _generate_normal_candidate(
                ref=paper_id,
                plan=normal_plan,
                subject=subject,
                klass=klass,
                context_excerpt=context_excerpt,
                custom_instructions=custom_instructions,
                feedback_hints=feedback_hints,
                on_batch_complete=update_progress,
            )
        else:
            data = await _request_paper_json(
                system_message=system_message,
                user_text=prompt,
                ref=paper_id,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("Question generation failed")
        await db.papers.update_one(
            {"id": paper_id, "generation_status": {"$in": ACTIVE_GENERATION_STATUSES}},
            {"$set": {
                "generation_status": "needs_review",
                "generation_error": str(e)[:500],
                "generation_progress.status": "needs_review",
                "generation_progress.phase": "needs_review",
                "generation_progress.updated_at": utcnow_iso(),
            }},
        )
        await record_activity(owner_id, "paper.failed", {"paper_id": paper_id})
        return

    sections = data.get("sections") or []
    # The draft provider call is now complete. Publish structurally safe
    # content before any whole-paper correction, LaTeX pass, or targeted repair
    # can add latency. Unsafe questions remain withheld from the visible copy.
    review_budget = begin_review_budget(
        seconds=float(os.environ.get("PAPER_REVIEW_BUDGET_SECONDS", "180")),
        call_limit=int(os.environ.get("PAPER_REVIEW_PROVIDER_CALL_LIMIT", "12")),
    )
    # Publish the only truthful question count available once the model output
    # has been parsed. A single provider response moves from 0 straight to this
    # value; the UI continues to poll it while the finishing checks run.
    coercions: dict = {}
    _normalise_paper_questions(
        sections, difficulty, coercions, assign_ids=normal_plan is None
    )
    initial_case_review = await _review_template_case_studies(sections, spec, ref=paper_id)
    first_view, withheld = _screen_viewable_sections(
        sections, spec=spec, normal_plan=normal_plan, case_review_misses=initial_case_review
    )
    if _preview_allowed(first_view, spec, target_q):
        draft_published = True
        await db.papers.update_one(
            {"id": paper_id, "generation_status": {"$in": [*ACTIVE_GENERATION_STATUSES, "ready"]}},
            {"$set": {
                "sections": first_view,
                "question_count": count_questions(first_view),
                "generation_status": "ready",
                "generation_error": None,
                "generation_progress.completed": min(target_q, count_questions(sections)),
                "generation_progress.total": target_q,
                "generation_progress.status": "ready",
                "generation_progress.phase": "repairing",
                "generation_progress.updated_at": utcnow_iso(),
                "quality_review.status": "running",
                "quality_review.stage": "repairing",
                "quality_review.visible_question_count": count_questions(first_view),
                "quality_review.withheld_count": max(0, count_questions(sections) - count_questions(first_view)),
                "quality_review.withheld_reasons": withheld[:20],
                "quality_review.draft_available": True,
            }},
        )
    else:
        await update_progress(count_questions(sections), phase="validating")
    (allocation_coverage(sections, spec) if spec.get("section_allocations") else measure_coverage(sections, topics_weighted, spec.get("total_questions") or derive_target_question_count(total_marks)))
    # One bounded correction attempt for a complete but noncompliant paper.
    # Truncated papers use the existing top-up path instead.
    initial_check = verify_instructions(sections, spec)
    initial_template_misses = (
        template_validation_misses(sections, spec)
        if spec.get("section_allocations") else []
    )
    # A missing shared case stimulus is content failure, not a count shortfall.
    # Surface it before the existing *single* correction attempt so the paper
    # gets one chance to become a real case study.  Keep every other template
    # diagnostic in the final/recovery gate; broadening this trigger would make
    # harmless metadata differences regenerate an otherwise usable paper.
    initial_case_misses = [
        miss for miss in initial_template_misses
        if "case study requires exactly one substantive shared stimulus" in miss.lower()
    ]
    initial_check["misses"].extend(initial_case_misses)
    invalid_type_misses = [
        f"Question {index}: generated an invalid cognitive type."
        for index, q in enumerate((q for s in sections for q in s.get("questions", [])), start=1)
        if "invalid_question_type" in coercions.get(q.get("id"), [])
    ]
    initial_check["misses"].extend(invalid_type_misses)
    initial_check["honoured"] = not initial_check["misses"]
    # `prompt is not None` excludes batched papers. The correction regenerates
    # the WHOLE paper in a single call, which is the exact thing that truncates
    # at that size -- retrying it would reintroduce the salvage-and-top-up chain
    # batching exists to remove. A batched paper that misses its counts goes to
    # the top-up path instead, which asks only for the gap.
    #
    # `spec["total_questions"] > 0` excludes plain blueprint papers, where the
    # compiled spec sets it to 0 on purpose: `count_questions(...) >= 0` is
    # always true, so every such paper with any unmet check was making a second
    # full-paper call. A blueprint WITH allocations has a real total and is
    # still corrected, which is what its own tests expect.
    if (
        normal_plan is None
        and prompt is not None
        and spec.get("total_questions")
        and not initial_check["honoured"]
        and count_questions(sections) >= spec["total_questions"]
    ):
        try:
            corrected_data = await _request_paper_json(
                system_message="Create original exam questions in strict JSON. Satisfy the resolved requirements exactly.",
                user_text=prompt + "\nCORRECTION REQUIRED: A previous attempt failed these checks:\n" +
                    "\n".join(initial_check["misses"]) +
                    "\nGenerate the complete paper again, correcting these mismatches. Return only JSON.",
                ref=f"{paper_id}:validation-correction",
            )
            corrected = corrected_data.get("sections") or []
            corrected_coercions: dict = {}
            _normalise_paper_questions(corrected, difficulty, corrected_coercions)
            (allocation_coverage(corrected, spec) if spec.get("section_allocations") else measure_coverage(corrected, topics_weighted, spec.get("total_questions") or derive_target_question_count(total_marks)))
            corrected_check = verify_instructions(corrected, spec)
            corrected_template_misses = (
                template_validation_misses(corrected, spec)
                if spec.get("section_allocations") else []
            )
            corrected_check["misses"].extend(
                miss for miss in corrected_template_misses
                if "case study requires exactly one substantive shared stimulus" in miss.lower()
            )
            corrected_check["misses"].extend(
                "generated an invalid cognitive type"
                for flags in corrected_coercions.values() if "invalid_question_type" in flags
            )
            template_improved = (
                not spec.get("section_allocations")
                or len(corrected_template_misses) < len(initial_template_misses)
            )
            if (corrected and template_improved
                    and len(corrected_check["misses"]) < len(initial_check["misses"])):
                data, sections = corrected_data, corrected
                coercions = corrected_coercions
        except Exception as exc:
            logger.warning(f"Requirement correction skipped for {paper_id}: {exc}")

    await update_progress(count_questions(sections), phase="finalizing")

    # Capture the first valid set of headings as this paper's durable section
    # plan. From here on recovered and top-up questions are routed by ID, not
    # by whatever display title the provider happens to emit.
    if normal_plan:
        sections, _ = normalize_normal_sections(sections, normal_plan)
        section_plan = list(normal_plan.get("sections") or [])
    else:
        sections, section_plan, _ = reconcile_section_plan(sections, None, spec)
    hydrate_section_instructions({"generation_requirements": spec, "sections": sections})

    # Finish the questions that arrived structurally incomplete: an MCQ with
    # two options, a stem cut mid-sentence, an answer key pointing at
    # nothing. Runs ahead of repair_sections so options written here get the
    # same LaTeX gate as the ones the first call produced.
    completeness_stats, latex_stats = await _complete_and_repair_sections_parallel(
        sections, ref=paper_id,
        context={"subject": subject, "klass": klass, "difficulty": difficulty},
        flags=coercions,
    )
    latex_baseline = {
        q.get("id"): (q.get("question"), copy.deepcopy(q.get("options")))
        for section in sections for q in section.get("questions") or []
        if isinstance(q, dict) and q.get("id")
    }

    # A transport-salvaged slot can exist while its content is still unusable
    # (for example a truncated stem or a three-option MCQ). Retry only those
    # immutable identities once and never disturb the accepted slots.
    if normal_plan:
        invalid_slot_ids = {
            question.get("slot_id")
            for section in sections
            for question in section.get("questions") or []
            if validate_question(question)
        }
        invalid_slots = [
            slot for slot in normal_plan.get("slots") or []
            if slot.get("slot_id") in invalid_slot_ids
        ]
        if invalid_slots:
            try:
                replacement_data = await _generate_normal_candidate(
                    only_slots=invalid_slots,
                    plan=normal_plan,
                    subject=subject,
                    klass=klass,
                    context_excerpt=context_excerpt,
                    custom_instructions=custom_instructions,
                    feedback_hints=feedback_hints,
                    existing_questions=flatten_question_texts(sections),
                    ref=f"{paper_id}:invalid-slot-recovery",
                )
                replacements = replacement_data.get("sections") or []
                replacement_flags: dict = {}
                _normalise_paper_questions(
                    replacements, difficulty, replacement_flags, assign_ids=False
                )
                await complete_sections(
                    replacements,
                    ref=f"{paper_id}:invalid-slot-recovery",
                    context={"subject": subject, "klass": klass, "difficulty": difficulty},
                    flags=replacement_flags,
                )
                valid_replacements = [
                    {
                        **section,
                        "questions": [
                            question for question in section.get("questions") or []
                            if not validate_question(question)
                        ],
                    }
                    for section in replacements
                ]
                valid_ids = {
                    question.get("slot_id")
                    for section in valid_replacements
                    for question in section.get("questions") or []
                }
                retained = [
                    {
                        **section,
                        "questions": [
                            question for question in section.get("questions") or []
                            if question.get("slot_id") not in valid_ids
                        ],
                    }
                    for section in sections
                ]
                sections = merge_normal_sections(normal_plan, retained, valid_replacements)
            except Exception as exc:
                logger.warning("Normal invalid-slot recovery failed for %s: %s", paper_id, exc)

    # Measure coverage first so the dedupe pass knows which topics are already
    # over-quota: when two questions are the same question, the one belonging
    # to the topic with too many is the one to lose. This first pass is also
    # what canonicalises each question's topic label, which everything below
    # counts on. Then measure again, because the answer changed.
    #
    # `target_q` was resolved before generation, from the same spec the prompt
    # was built from -- measuring against anything else scores the paper on a
    # target the model was never given.
    #
    # Except under a plain blueprint, where there is no such number to reuse:
    # the compiled spec sets total_questions to 0 there on purpose, so target_q
    # falls through to the marks-derived guess, and the prompt states per-topic
    # PERCENTAGES rather than counts. Scoring a 38-question blueprint paper
    # against a guess of 16 marked nearly every topic `over`. Counting what came
    # back is the honest denominator. A blueprint carrying section allocations
    # does not need any of this -- allocation_coverage scores against the
    # allocation rows themselves, which is a better answer than either.
    coverage_target = target_q
    if (section_blueprint or "").strip():
        coverage_target = count_questions(sections) or target_q
    coverage = (
        allocation_coverage(sections, spec)
        if spec.get("section_allocations")
        else measure_coverage(sections, topics_weighted, coverage_target)
    )
    dedupe_stats = await dedupe_sections(sections, ref=paper_id, coverage=coverage)
    # Duplicate removal can vacate slots. Recover only those identities; never
    # regenerate or replace the questions that already passed.
    if normal_plan:
        missing_slots = missing_plan_slots(normal_plan, sections)
        if missing_slots:
            try:
                recovered_data = await _generate_normal_candidate(
                    only_slots=missing_slots,
                    plan=normal_plan,
                    subject=subject,
                    klass=klass,
                    context_excerpt=context_excerpt,
                    custom_instructions=custom_instructions,
                    feedback_hints=feedback_hints,
                    existing_questions=flatten_question_texts(sections),
                    ref=f"{paper_id}:missing-slot-recovery",
                )
                recovered = recovered_data.get("sections") or []
                recovered_flags: dict = {}
                _normalise_paper_questions(
                    recovered, difficulty, recovered_flags, assign_ids=False
                )
                await complete_sections(
                    recovered,
                    ref=f"{paper_id}:missing-slot-recovery",
                    context={"subject": subject, "klass": klass, "difficulty": difficulty},
                    flags=recovered_flags,
                )
                await dedupe_sections(
                    recovered,
                    ref=f"{paper_id}:missing-slot-recovery",
                    against=sections,
                )
                sections = merge_normal_sections(normal_plan, sections, recovered)
            except Exception as exc:  # retain every valid initial slot
                logger.warning("Normal missing-slot recovery failed for %s: %s", paper_id, exc)
    coverage = (
        allocation_coverage(sections, spec)
        if spec.get("section_allocations")
        else measure_coverage(sections, topics_weighted, coverage_target)
    )

    # Check and repair the LaTeX before anything downstream reads it. Runs
    # ahead of apply_diagram_policy so diagram_policy.plain_text() flattens
    # already-clean markup, and ahead of the $set below so generation_status
    # only reaches "ready" once the questions actually render.
    # The parallel pass above has already checked every stable question.
    # Re-check only slots introduced or rewritten by later recovery/dedupe.
    late_sections = [{
        "questions": [
            q for q in section.get("questions") or []
            if latex_baseline.get(q.get("id")) != (q.get("question"), q.get("options"))
        ]
    } for section in sections]
    late_stats = await repair_sections(late_sections, ref=f"{paper_id}:recovery")
    latex_stats["checked"] = int(latex_stats.get("checked") or 0) + int(late_stats.get("checked") or 0)
    latex_stats["auto_fixed"] = int(latex_stats.get("auto_fixed") or 0) + int(late_stats.get("auto_fixed") or 0)
    latex_stats["llm_fixed"] = int(latex_stats.get("llm_fixed") or 0) + int(late_stats.get("llm_fixed") or 0)
    latex_stats["unrepaired"] = int(latex_stats.get("unrepaired") or 0) + int(late_stats.get("unrepaired") or 0)
    latex_stats["issues"] = sorted(set(latex_stats.get("issues") or []) | set(late_stats.get("issues") or []))
    normalize_internal_choice_counts(sections)
    internal_choice_recovery = None
    choice_diagnostics = internal_choice_misses(sections, spec)
    if choice_diagnostics:
        # A complete paper can still be structurally wrong for a student when
        # its required internal alternatives are absent.  Repair the minimum
        # slots before publishing; unlike count top-up this never appends a
        # question or shifts the board-template allocation.
        try:
            repaired_choices = await repair_internal_choices({
                "id": paper_id, "owner_id": owner_id, "subject": subject,
                "class_name": klass, "difficulty": difficulty,
                "custom_instructions": custom_instructions,
                "generation_requirements": spec, "sections": sections,
            }, context_excerpt=context_excerpt, feedback_hints=feedback_hints)
            sections = repaired_choices["sections"]
        except Exception as exc:  # keep the original complete paper available
            logger.warning("Internal OR auto-repair failed for %s: %s", paper_id, exc)
            internal_choice_recovery = {
                "status": "needs_review", "reason": "internal_choices", "attempt": 1,
                "max_attempts": 1, "finished_at": utcnow_iso(),
                "validation_summary": [str(exc)[:500]], "diagnostics": choice_diagnostics,
            }

    # Drop figures the model asked for but the question must not have — the
    # student's own drawing task, a decorative illustration, or a spec carrying
    # the answer — then mark what survives pending so the UI can show
    # "generating" placeholders.
    pending_count = apply_diagram_policy(sections, ref=paper_id,
        max_diagrams=spec.get("diagram_count", spec.get("total_questions", 200) if spec.get("section_allocations") else 5))
    for s in sections:
        for q in s.get("questions", []):
            q["diagram_status"] = "pending" if q.get("needs_diagram") else "none"

    # Cancellation is cooperative: do not publish a response that finished
    # after its owner cancelled the job.
    still_pending = await db.papers.find_one({"id": paper_id, "generation_status": {"$in": [*ACTIVE_GENERATION_STATUSES, "ready"]}}, {"_id": 0, "id": 1})
    if not still_pending:
        logger.info("Paper generation cancelled before publish: %s", paper_id)
        return
    final_check = verify_instructions(sections, spec)
    final_choice_diagnostics = internal_choice_misses(sections, spec)
    normal_issues = validate_normal_plan(sections, normal_plan) if normal_plan else []
    if normal_plan:
        incomplete = [
            question.get("slot_id")
            for section in sections
            for question in section.get("questions") or []
            if validate_question(question)
        ]
        completeness_stats["unrepaired"] = len(incomplete)
        if incomplete:
            normal_issues.append({
                "code": "incomplete_content",
                "category": "provider_response",
                "count": len(incomplete),
                "slot_ids": incomplete,
            })
        if int(latex_stats.get("unrepaired") or 0):
            normal_issues.append({
                "code": "unrepaired_math",
                "category": "formatting",
                "count": int(latex_stats["unrepaired"]),
            })
    allocation_issues = template_validation_misses(sections, spec) if spec.get("section_allocations") else []
    final_case_review = await _review_template_case_studies(sections, spec, ref=paper_id)
    final_check["misses"].extend(issue["message"] for issue in final_case_review)
    final_check["misses"].extend(allocation_issues)
    if not spec.get("section_allocations"):
        actual_marks = sum(float(q.get("marks") or 0) for s in sections for q in (s.get("questions") or []))
        if actual_marks != float(total_marks):
            final_check["misses"].append(f"asked for {total_marks} total marks, got {actual_marks:g}")
    final_check["honoured"] = not final_check["misses"]
    if normal_plan and normal_issues:
        status = normal_review_status(normal_issues)
        final_check["misses"] = [status["summary"]]
        final_check["honoured"] = False
    final_type_misses = [flags for flags in coercions.values() if "invalid_question_type" in flags]
    if final_type_misses:
        final_check["misses"].append("One or more questions had an invalid cognitive type.")
        final_check["honoured"] = False
    # Validation mismatches are recoverable. Publish the usable paper, retain
    # the details for the review/recovery pipeline, and let the existing
    # top-up/recovery work continue in the background. A mismatch must never
    # turn a generated paper into a terminal "failed" screen.
    mark_misses = [
        miss for miss in final_check["misses"]
        if "total marks" in miss.lower()
        or ("expected " in miss.lower() and " marks" in miss.lower())
    ]
    structural_recovery = bool(spec.get("section_allocations") and any(
        "allocation identity" in miss.lower() for miss in allocation_issues
    ))
    quality_misses = list(dict.fromkeys(
        list(final_check["misses"])
        + (["One or more questions remain structurally incomplete."] if int(completeness_stats.get("unrepaired") or 0) else [])
        + (["Some mathematical notation could not be repaired safely."] if int(latex_stats.get("unrepaired") or 0) else [])
        + (["Required internal choices could not be repaired."] if internal_choice_recovery else [])
        + (["Required internal choices remain incomplete."] if final_choice_diagnostics else [])
        + (["Template allocation needs review."] if structural_recovery else [])
        + ([issue["message"] for issue in final_case_review])
        + ([review_budget.exhausted_reason] if review_budget.exhausted_reason else [])
    ))
    validation_error = "; ".join(quality_misses)[:500] or None
    final_sections, final_withheld = _screen_viewable_sections(
        sections, spec=spec, normal_plan=normal_plan, case_review_misses=final_case_review
    )
    final_quality_misses = list(dict.fromkeys([
        *quality_misses,
        *(final_withheld[:20]),
        *( [normal_review_status(normal_issues)["summary"]] if normal_issues else []),
    ]))
    final_validation_error = "; ".join(final_quality_misses)[:500] or None
    auto_topup_pending = bool(
        (spec.get("total_questions_stated") or spec.get("section_allocations"))
        and target_q > count_questions(final_sections)
        and AUTO_TOPUP_ENABLED
    )
    await db.papers.update_one(
        {"id": paper_id, "generation_status": {"$in": [*ACTIVE_GENERATION_STATUSES, "ready"]}},
        {"$set": {
            "instructions": printable_instructions(
                data.get("instructions", ""),
                template_instructions=printable_template_instructions({"generation_requirements": spec}),
                custom_instructions=custom_instructions,
            ),
            "sections": final_sections,
            "section_plan": section_plan,
            "question_count": count_questions(final_sections),
            "diagrams_pending": pending_count,
            "generation_status": "ready" if _preview_allowed(final_sections, spec, target_q) else ("generating" if auto_topup_pending else "needs_review"),
            "generation_error": None,
            "generation_recovery": (
                {"status": "queued", "reason": "allocation_metadata", "attempt": 0,
                 "max_attempts": TEMPLATE_RECOVERY_MAX_ATTEMPTS, "started_at": None,
                 "finished_at": None, "validation_summary": compact_validation_misses(allocation_issues)}
                if structural_recovery else (
                    internal_choice_recovery if internal_choice_recovery else (
                    {"status": "queued", "reason": "validation", "attempt": 0,
                     "max_attempts": 0, "validation_summary": [validation_error]}
                    if validation_error and not normal_plan else None
                    )
                )
            ),
            "review_status": normal_review_status(normal_issues) if normal_plan else None,
            "generation_progress.completed": min(target_q, count_questions(final_sections)),
            "generation_progress.total": target_q,
            "generation_progress.status": "ready",
            "generation_progress.phase": "ready",
            "generation_progress.batch": len(normal_slot_batches(
                normal_plan.get("slots") or [],
                single_call_limit=NORMAL_SINGLE_CALL_LIMIT,
                batch_size=NORMAL_BATCH_SIZE,
            )) if normal_plan else 1,
            "generation_progress.batch_total": len(normal_slot_batches(
                normal_plan.get("slots") or [],
                single_call_limit=NORMAL_SINGLE_CALL_LIMIT,
                batch_size=NORMAL_BATCH_SIZE,
            )) if normal_plan else 1,
            "generation_progress.updated_at": utcnow_iso(),
            "recovered_from_truncation": bool(data.get("_recovered_from_truncation")),
            "latex_check": latex_stats,
            "completeness_check": completeness_stats,
            "dedupe_check": dedupe_stats,
            "topic_coverage": coverage,
            "instruction_check": final_check,
            "quality_review.budget": review_budget.snapshot(),
            "quality_review.status": "running" if auto_topup_pending else ("needs_review" if final_quality_misses else "passed"),
            "quality_review.stage": "topup" if auto_topup_pending else ("needs_review" if final_quality_misses else "ready"),
            "quality_review.message": final_validation_error,
            "quality_review.finished_at": utcnow_iso(),
            "quality_review.visible_question_count": count_questions(final_sections),
            "quality_review.withheld_count": max(0, count_questions(sections) - count_questions(final_sections)),
            "quality_review.withheld_reasons": final_withheld[:20],
            "quality_review.draft_available": True,
            "quality_review.internal_choice_diagnostics": final_choice_diagnostics,
            "quality_review.failed_gates": final_quality_misses,
            # A stated count makes the target knowable, which is what
            # re-enables auto-top-up and the missing-questions banner for
            # instruction-driven papers -- both were dead while this was 0.
            **(
                # STATED, not merely resolved. compile_requirements always
                # produces a total, falling back to a guess derived from
                # total_marks, and writing that guess here overwrote the real
                # target the paper was created with -- so a complete paper could
                # be declared short of a number nobody asked for and top itself
                # up to reach it. An allocation spec never passes through
                # compile_requirements, and its total comes straight from the
                # blueprint's group counts, so it is stated by construction.
                {"target_question_count": target_q}
                if spec.get("total_questions")
                and (
                    spec.get("total_questions_stated")
                    or spec.get("section_allocations")
                )
                else {}
            ),
        }},
    )
    await record_activity(owner_id, "paper.ready", {"paper_id": paper_id, "questions": count_questions(final_sections)})

    # Count shortfalls are repaired only after the safe draft is durable and
    # visible. The existing top-up path targets the gap and preserves the
    # accepted questions; do not make the first view wait for this call.
    if auto_topup_pending:
        topup_budget = begin_review_budget(
            seconds=float(os.environ.get("PAPER_REVIEW_BUDGET_SECONDS", "180")),
            call_limit=int(os.environ.get("PAPER_REVIEW_PROVIDER_CALL_LIMIT", "12")),
        )
        await _auto_topup_if_short(paper_id, owner_id, context_excerpt, feedback_hints)
        latest = await db.papers.find_one({"id": paper_id, "is_deleted": False}, {"_id": 0})
        if latest:
            # Top-up may restore slots that were unavailable during the first
            # OR pass. Repair their alternatives before the final validation.
            if internal_choice_misses(latest.get("sections") or [], spec):
                choice_result = await repair_internal_choices(
                    {**latest, "generation_requirements": spec},
                    context_excerpt=context_excerpt, feedback_hints=feedback_hints,
                )
                latest["sections"] = choice_result["sections"]
            repaired_sections, withheld_after_topup = _screen_viewable_sections(
                latest.get("sections") or [], spec=spec, normal_plan=normal_plan
            )
            refreshed_check = verify_instructions(repaired_sections, spec)
            refreshed_normal_issues = validate_normal_plan(repaired_sections, normal_plan) if normal_plan else []
            refreshed_choice_diagnostics = internal_choice_misses(repaired_sections, spec)
            short_after_topup = count_questions(repaired_sections) < target_q
            remaining_gates = list(dict.fromkeys([
                *(refreshed_check.get("misses") or []),
                *( [normal_review_status(refreshed_normal_issues)["summary"]] if refreshed_normal_issues else []),
                *( ["Required internal choices remain incomplete."] if refreshed_choice_diagnostics else []),
                *withheld_after_topup[:20],
                *( ["The paper is still short of its requested question count."] if short_after_topup else []),
                *([topup_budget.exhausted_reason] if topup_budget.exhausted_reason else []),
                *(quality_misses if review_budget.exhausted_reason else []),
            ]))
            final_status = "needs_review" if remaining_gates else "passed"
            await db.papers.update_one({"id": paper_id, "generation_status": {"$in": ["ready", "generating"]}}, {"$set": {
                "generation_status": "ready" if not remaining_gates else "needs_review",
                "sections": repaired_sections,
                "question_count": count_questions(repaired_sections),
                "quality_review.status": final_status,
                "quality_review.stage": final_status,
                "quality_review.message": "; ".join(remaining_gates)[:500] or None,
                "quality_review.failed_gates": remaining_gates,
                "quality_review.withheld_count": max(0, count_questions(latest.get("sections") or []) - count_questions(repaired_sections)),
                "quality_review.withheld_reasons": withheld_after_topup[:20],
                "quality_review.internal_choice_diagnostics": refreshed_choice_diagnostics,
                "quality_review.topup_budget": topup_budget.snapshot(),
                "quality_review.finished_at": utcnow_iso(),
                "instruction_check": refreshed_check,
            }})

    # The durable worker records quality completion and queues diagrams after
    # this function returns. Do not block the paper on diagrams or auto-topup.


# ---------------------------------------------------------------------------
# Top-up generation — fill in the questions a partial generation never produced
# ---------------------------------------------------------------------------
# A paper can end up short in two ways: the single school-path LLM call gets
# truncated and `parse_json_response` salvages whatever questions were complete,
# or a competitive paper loses whole batches. Both still save as "ready". The
# full `regenerate` endpoint throws the survivors away to try again; this path
# keeps them and asks only for the shortfall.
#
# The hard rule for everything below: an existing question object is never
# mutated and never dropped. A failed top-up must leave the paper exactly as it
# was found.
# Ceiling on a teacher-supplied top-up count. Matches the largest paper the
# app will generate (CompetitivePaperRequest caps question_count at 200), so
# it guards against absurd input without ever clamping a real shortfall.
TOPUP_MAX = 200
TEMPLATE_RECOVERY_MAX_ATTEMPTS = 2
_RECOVERY_TASKS: set = set()


def _spawn_recovery(coro) -> None:
    """Keep an in-process recovery task alive until it has persisted a result."""
    task = _asyncio.create_task(coro)
    _RECOVERY_TASKS.add(task)
    task.add_done_callback(_RECOVERY_TASKS.discard)


def count_questions(sections: list) -> int:
    """Total questions across all sections."""
    return sum(len(s.get("questions") or []) for s in sections or [])


def flatten_question_texts(sections: list) -> list:
    """Question stems, in order — the 'do not repeat these' list for a top-up."""
    return [
        q.get("question", "")
        for s in sections or []
        for q in (s.get("questions") or [])
        if q.get("question")
    ]


def paper_target_count(paper: dict) -> int:
    """How many questions this paper was supposed to have.

    Prefers the value stored at creation time. Papers created before that field
    existed fall back to the same formula the prompt builder uses, so an older
    paper still gets a usable target instead of no top-up button at all.
    """
    stored = (paper.get("generation_requirements") or {}).get("total_questions") or paper.get("target_question_count")
    try:
        stored = int(stored)
    except (TypeError, ValueError):
        stored = 0
    if stored > 0:
        return stored
    # A blueprint states its real question count in prose nobody parses.
    # Deriving one from total_marks would be a guess, and a wrong guess here
    # shows a "12 questions missing" banner on a paper that is perfectly
    # complete. Better to admit we do not know: 0 means the banner falls back to
    # the truncation flag and the teacher supplies the count.
    #
    # Additional instructions used to land here too. They no longer do:
    # instruction_spec parses them into a count and generate_paper_background
    # stores it, so those papers have a real target and get the top-up and the
    # banner back.
    if paper.get("section_blueprint"):
        return 0
    return derive_target_question_count(int(paper.get("total_marks") or 0))


def template_validation_misses(sections: list, spec: dict) -> list[str]:
    """Return the compact, deterministic gate for a template candidate."""
    misses = allocation_misses(sections, spec)
    check = verify_instructions(sections, spec)
    misses.extend(check.get("misses") or [])
    expected_marks = spec.get("total_marks")
    if expected_marks is not None:
        actual_marks = sum(float(q.get("marks") or 0) for section in sections for q in section.get("questions") or [])
        if actual_marks != expected_marks:
            misses.append(f"asked for {expected_marks} offered marks, got {actual_marks:g}")
    return list(dict.fromkeys(misses))


def template_requires_full_recovery(sections: list, spec: dict) -> bool:
    """Only a complete candidate can repair unprovable allocation placement."""
    return bool(spec.get("section_allocations") and any(
        "allocation identity" in miss.lower()
        for miss in template_validation_misses(sections, spec)
    ))


async def recover_template_generation_background(
    paper_id: str,
    owner_id: str,
    context_excerpt: str = "",
    feedback_hints: str = "",
) -> None:
    """Build replacement candidates without disturbing the visible paper."""
    tag("paper_template_recovery", paper_id)
    paper = await db.papers.find_one({"id": paper_id, "is_deleted": False}, {"_id": 0})
    if not paper:
        return
    spec = paper.get("generation_requirements") or {}
    if not spec.get("section_allocations"):
        return
    recovery = paper.get("generation_recovery") or {}
    attempt = int(recovery.get("attempt") or 0)
    max_attempts = int(recovery.get("max_attempts") or TEMPLATE_RECOVERY_MAX_ATTEMPTS)
    if recovery.get("status") not in {"queued", "recovering"} or attempt >= max_attempts:
        return

    prompt = qgen_prompt(
        subject=paper.get("subject", ""), klass=paper.get("class_name", ""),
        topics_weighted=paper.get("topics") or [], difficulty=paper.get("difficulty", "medium"),
        distribution=paper.get("distribution") or {}, total_marks=int(paper.get("total_marks") or 0),
        duration=int(paper.get("duration_minutes") or 0), context_excerpt=context_excerpt,
        feedback_hints=feedback_hints, format_distribution=paper.get("format_distribution") or {},
        custom_instructions=paper.get("custom_instructions") or "",
        section_blueprint=paper.get("section_blueprint") or "", spec=spec,
    )
    latest_misses: list[str] = []
    while attempt < max_attempts:
        attempt += 1
        await db.papers.update_one({"id": paper_id}, {"$set": {"generation_recovery": {
            "status": "recovering", "reason": "allocation_metadata", "attempt": attempt,
            "max_attempts": max_attempts, "started_at": utcnow_iso(), "validation_summary": [],
        }}})
        try:
            candidate = await _request_paper_json(
                system_message="Create original exam questions in strict JSON. Return every required blueprint identity exactly.",
                user_text=prompt + "\nRECOVERY: This is a complete replacement candidate. Every section and question identity is mandatory. Return only JSON.",
                ref=f"{paper_id}:template-recovery-{attempt}",
                # The enclosing recovery loop already owns its two candidates.
                attempts=1,
            )
            sections = candidate.get("sections") or []
            coercions: dict = {}
            _normalise_paper_questions(sections, paper.get("difficulty", "medium"), coercions)
            sections, section_plan, _ = reconcile_section_plan(sections, None, spec)
            await complete_sections(sections, ref=f"{paper_id}:recovery", context={
                "subject": paper.get("subject", ""), "klass": paper.get("class_name", ""),
                "difficulty": paper.get("difficulty", "medium"),
            }, flags=coercions)
            await repair_sections(sections, ref=f"{paper_id}:recovery")
            normalize_internal_choice_counts(sections)
            latest_misses = template_validation_misses(sections, spec)
            if any("invalid_question_type" in flags for flags in coercions.values()):
                latest_misses.append("One or more questions had an invalid cognitive type.")
            if not latest_misses:
                hydrate_section_instructions({"generation_requirements": spec, "sections": sections})
                pending = apply_diagram_policy(sections, ref=f"{paper_id}:recovery", max_diagrams=spec.get("diagram_count", 200))
                for section in sections:
                    for question in section.get("questions") or []:
                        question["diagram_status"] = "pending" if question.get("needs_diagram") else "none"
                # Atomic candidate swap: all fields describe the same validated paper.
                await db.papers.update_one({"id": paper_id}, {"$set": {
                    "sections": sections, "section_plan": section_plan,
                    "question_count": count_questions(sections), "diagrams_pending": pending,
                    "instructions": printable_instructions("", template_instructions=printable_template_instructions({"generation_requirements": spec})),
                    "instruction_check": verify_instructions(sections, spec),
                    "generation_recovery": {"status": "ready", "reason": "allocation_metadata", "attempt": attempt,
                                            "max_attempts": max_attempts, "finished_at": utcnow_iso(), "validation_summary": []},
                }})
                if pending:
                    await generate_diagrams_background(paper_id, owner_id)
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Template recovery attempt %s failed for %s: %s", attempt, paper_id, exc)
            latest_misses = [str(exc)[:240]]

    await db.papers.update_one({"id": paper_id}, {"$set": {"generation_recovery": {
        "status": "needs_review", "reason": "allocation_metadata", "attempt": attempt,
        "max_attempts": max_attempts, "finished_at": utcnow_iso(),
        "validation_summary": compact_validation_misses(latest_misses),
    }}})


def merge_topup_sections(existing: list, new: list, limit: int = 0) -> list:
    """Fold freshly generated sections into the paper's existing ones.

    Questions land in the existing section whose title matches (case- and
    whitespace-insensitively); anything else is appended as its own section.
    That keeps a blueprint paper's structure when the model reproduces the
    section titles, and degrades to a clean extra section when it doesn't —
    which is the common case, since the truncation salvage path collapses
    everything into a single "Recovered Questions" section anyway.

    `limit` caps the merged total, mirroring the competitive path's truncation
    of over-produced batches. Existing questions are never dropped to make room.
    """
    merged = [dict(s, questions=list(s.get("questions") or [])) for s in existing or []]
    by_title = {}
    for s in merged:
        by_title.setdefault((s.get("title") or "").strip().lower(), s)

    room = max(0, limit - count_questions(merged)) if limit else None
    for sec in new or []:
        incoming = list(sec.get("questions") or [])
        if room is not None:
            if room <= 0:
                break
            incoming = incoming[:room]
            room -= len(incoming)
        if not incoming:
            continue
        key = (sec.get("title") or "").strip().lower()
        target = next((s for s in merged if sec.get("section_id") and s.get("section_id") == sec["section_id"]), None) or by_title.get(key)
        if target is not None:
            target["questions"].extend(incoming)
        else:
            fresh = {
                **{k: v for k, v in sec.items() if k != "questions"},
                "title": sec.get("title") or "Additional Questions",
                "questions": incoming,
            }
            merged.append(fresh)
            by_title.setdefault(key, fresh)
    return merged


def _section_key(title: object) -> str:
    """A comparison key for display headings; never shown to teachers."""
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").casefold()).strip()


def _section_family(title: object) -> str:
    """The stable part of conventional headings such as ``Section A``."""
    key = _section_key(title)
    match = re.match(r"section\s+([a-z0-9]+)\b", key)
    return f"section {match.group(1)}" if match else key


def _is_synthetic_section(section: dict) -> bool:
    title = _section_key(section.get("title"))
    return title == "recovered questions" or title.endswith("top up questions")


def _synthetic_base_key(title: object) -> str:
    return re.sub(r"\s+top up questions$", "", _section_key(title)).strip()


def reconcile_section_plan(
    sections: list, section_plan: Optional[list] = None, spec: Optional[dict] = None
) -> tuple[list, list, bool]:
    """Apply the server-owned section plan and return canonical section order.

    Titles remain teacher-facing presentation text.  ``section_id`` is the
    placement identity: recovery and top-up labels are matched to a saved plan
    before questions are rendered, exported, or counted.  This also makes the
    operation idempotent for existing papers.
    """
    spec = spec or {}
    # A compiled blueprint owns all printable section metadata.  Valid
    # allocation identities route questions into that skeleton; unprovable
    # questions remain visible for recovery/review and never rename a section.
    if spec.get("section_allocations") and (spec.get("blueprint") or {}).get("sections"):
        normalized, _misses = normalize_template_sections(sections, spec)
        if _misses:
            # Ambiguous persisted papers are review-only: do not silently
            # re-home their questions merely because a page was opened.
            return sections or [], list(section_plan or []), False
        plan = [
            {"id": section["section_id"], "title": section["title"], "order": index}
            for index, section in enumerate(normalized, start=1)
            if section.get("section_id") != "unplaced-generated-questions"
        ]
        return normalized, plan, normalized != (sections or [])
    source = [dict(section, questions=list(section.get("questions") or [])) for section in sections or []]
    original = [(s.get("section_id"), s.get("title"), len(s.get("questions") or [])) for s in source]
    had_question_ids = all(
        question.get("section_id") == section.get("section_id")
        for section in source
        for question in section.get("questions") or []
        if section.get("section_id")
    )

    # Blueprint allocation IDs are the strongest placement signal. Keep that
    # compatibility path first, then use the universal plan below.
    source, _ = align_blueprint_sections(source, spec)
    plan = [dict(row) for row in (section_plan or []) if isinstance(row, dict) and row.get("id")]
    by_id = {row["id"]: row for row in plan}
    by_key = {_section_key(row.get("title")): row for row in plan if _section_key(row.get("title"))}
    by_family: dict[str, list] = {}
    for row in plan:
        by_family.setdefault(_section_family(row.get("title")), []).append(row)

    # First establish a plan from ordinary sections. Synthetic sections never
    # get their own permanent ID when a real matching section exists.
    # Existing questions always precede refills even when an old malformed
    # document stored the synthetic block before its base section.
    for section in [*filter(lambda item: not _is_synthetic_section(item), source),
                    *filter(_is_synthetic_section, source)]:
        if _is_synthetic_section(section):
            continue
        row = by_id.get(section.get("section_id")) or by_key.get(_section_key(section.get("title")))
        if row is None:
            section_id = section.get("section_id") or f"section-{len(plan) + 1}"
            while section_id in by_id:
                section_id = f"section-{len(plan) + 1}"
            row = {"id": section_id, "title": section.get("title") or f"Section {len(plan) + 1}", "order": len(plan) + 1}
            plan.append(row)
            by_id[row["id"]] = row
            by_key[_section_key(row["title"])] = row
            by_family.setdefault(_section_family(row["title"]), []).append(row)

    grouped = {row["id"]: {"section_id": row["id"], "title": row["title"], "questions": []} for row in plan}
    extras: list[dict] = []
    for section in [*filter(lambda item: not _is_synthetic_section(item), source),
                    *filter(_is_synthetic_section, source)]:
        row = by_id.get(section.get("section_id")) or by_key.get(_section_key(section.get("title")))
        if row is None and _is_synthetic_section(section):
            row = by_key.get(_synthetic_base_key(section.get("title")))
        if row is None and _is_synthetic_section(section):
            family = _section_family(section.get("title"))
            matches = by_family.get(family, [])
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            # A recovery response with no trustworthy target stays visible for
            # review; it is never silently assigned to an arbitrary section.
            extras.append(section)
            continue
        target = grouped[row["id"]]
        for question in section.get("questions") or []:
            question["section_id"] = row["id"]
            target["questions"].append(question)

    ordered = [grouped[row["id"]] for row in sorted(plan, key=lambda item: item.get("order", 0)) if grouped[row["id"]]["questions"]]
    ordered.extend(extras)
    current = [(s.get("section_id"), s.get("title"), len(s.get("questions") or [])) for s in ordered]
    return ordered, plan, current != original or not had_question_ids


def align_blueprint_sections(sections: list, spec: dict) -> tuple[list, bool]:
    """Route synthetic recovery/top-up blocks to their allocation sections.

    The model can label a refill block ``Section A - Top-Up Questions`` even
    though each question correctly carries the immutable allocation identity.
    Section labels are presentation text; allocation IDs are the source of
    truth. Move those questions before rendering or merging, preserving the
    blueprint order and leaving any unidentifiable questions visible for review.
    """
    allocations = spec.get("section_allocations") or []
    if not allocations:
        return sections, False

    def is_synthetic(section: dict) -> bool:
        title = (section.get("title") or "").strip().casefold()
        return title == "recovered questions" or title.endswith("top-up questions")

    synthetic = [section for section in sections or [] if is_synthetic(section)]
    if not synthetic:
        return sections, False

    by_allocation = {row.get("allocation_id"): row for row in allocations}
    blueprint_titles = {
        section.get("id"): section.get("title") or section.get("id")
        for section in (spec.get("blueprint") or {}).get("sections", [])
    }
    repaired = [
        dict(section, questions=list(section.get("questions") or []))
        for section in sections if section not in synthetic
    ]
    by_id = {section.get("section_id"): section for section in repaired if section.get("section_id")}
    changed = False
    leftovers: list = []
    for source in synthetic:
        for question in source.get("questions") or []:
            row = by_allocation.get(question.get("allocation_id"))
            if not row:
                leftovers.append(question)
                continue
            section_id = row["section_id"]
            target = by_id.get(section_id)
            if target is None:
                target = {
                    "section_id": section_id,
                    "title": blueprint_titles.get(section_id, f"Section {section_id}"),
                    "questions": [],
                }
                repaired.append(target)
                by_id[section_id] = target
            target["questions"].append(question)
            changed = True
    if leftovers:
        repaired.append({"title": "Recovered Questions", "questions": leftovers})
    blueprint_order = [
        section.get("id") for section in (spec.get("blueprint") or {}).get("sections", [])
    ]
    ordered = [by_id[section_id] for section_id in blueprint_order if section_id in by_id]
    ordered_ids = {id(section) for section in ordered}
    repaired = ordered + [section for section in repaired if id(section) not in ordered_ids]
    return (repaired, changed) if changed else (sections, False)


def restore_recovered_sections(sections: list, spec: dict) -> tuple[list, bool]:
    """Put legacy recovered blueprint questions back in their allocated sections.

    Older truncation recovery stored every surviving question under a synthetic
    ``Recovered Questions`` heading. The question allocation IDs were still
    intact, so this is a lossless repair for papers already saved in that
    shape. Unknown questions remain in the recovery section for manual review.
    """
    return align_blueprint_sections(sections, spec)


async def _finish_topup(
    paper_id: str, sections: list, added: int, pending_count: int, target: int,
    section_plan: Optional[list] = None,
) -> None:
    """Write a successful top-up back to the paper."""
    total = count_questions(sections)
    existing = await db.papers.find_one(
        {"id": paper_id}, {"_id": 0, "diagrams_pending": 1}
    )
    already_pending = int((existing or {}).get("diagrams_pending") or 0)
    update = {
        "sections": sections,
        **({"section_plan": section_plan} if section_plan is not None else {}),
        "question_count": total,
        "diagrams_pending": already_pending + pending_count,
        "topup": {
            "status": "ready",
            "added": added,
            "finished_at": utcnow_iso(),
        },
    }
    # The partial-recovery banner has served its purpose once the paper is whole.
    if target and total >= target:
        update["recovered_from_truncation"] = False
    await db.papers.update_one({"id": paper_id}, {"$set": update})


async def _fail_topup(paper_id: str, err: str) -> None:
    """Record a failed top-up. Deliberately touches nothing but `topup`."""
    await db.papers.update_one(
        {"id": paper_id},
        {"$set": {"topup": {
            "status": "failed",
            "error": str(err)[:500],
            "finished_at": utcnow_iso(),
        }}},
    )


# A paper that came out short used to sit there until a teacher noticed the
# banner and pressed the button. The machinery to fix it already existed --
# this just presses the button automatically, once.
AUTO_TOPUP_ENABLED = os.environ.get("AUTO_TOPUP_ENABLED", "true").strip().lower() not in (
    "false", "0", "no",
)
# Two attempts, and the counter is persisted before each. Two, not one,
# because there are now two independent ways to come up short -- a truncated
# generation and a dedupe pass that dropped repeats -- and a paper that hit
# both would otherwise get one refill for two gaps. Still bounded, so a paper
# can never top up a paper that tops up a paper.
AUTO_TOPUP_MAX_ATTEMPTS = 2


def _trim_targets(wanted: dict, count: int) -> dict:
    """Cut a per-topic shortfall down to `count` questions, worst gaps first.

    The measured shortfall can exceed what is being asked for -- a paper can
    be four questions short in total while five topics are each one light,
    because a topic can also be OVER quota. Handing the model targets that sum
    to more than the request reintroduces exactly the contradiction
    resolve_shares exists to remove.
    """
    if not wanted or count <= 0:
        return {}
    out: dict = {}
    left = count
    for name, gap in sorted(wanted.items(), key=lambda kv: (-kv[1], kv[0])):
        if left <= 0:
            break
        take = min(int(gap), left)
        if take > 0:
            out[name] = take
            left -= take
    return out


async def _auto_topup_if_short(
    paper_id: str,
    owner_id: str,
    context_excerpt: str = "",
    feedback_hints: str = "",
) -> None:
    """Fill in the questions a truncated generation never produced.

    The school path makes one LLM call for the whole paper. When it runs out
    of output tokens, parse_json_response salvages the questions that were
    complete and drops the one that was mid-write, so the paper saves as
    "ready" while being several questions short. question_repair cannot help
    there -- there is no half-written question left to finish, only a gap.

    Best-effort by construction: this runs after the paper is already saved
    and visible, so anything that goes wrong here leaves a short paper with
    its top-up banner, which is exactly the old behaviour.
    """
    if not AUTO_TOPUP_ENABLED:
        return
    try:
        p = await db.papers.find_one(
            {"id": paper_id, "is_deleted": False}, {"_id": 0}
        )
        if not p:
            return
        attempts = int(p.get("topup_auto_attempts") or 0)
        if attempts >= AUTO_TOPUP_MAX_ATTEMPTS:
            return
        # A teacher who already pressed the button owns the topup document.
        if (p.get("topup") or {}).get("status") == "generating":
            return
        # 0 means paper_target_count deliberately declined to guess (a
        # blueprint or free-form paper states its count in prose we never
        # parse). Adding questions to a paper whose real size is unknown is
        # worse than leaving it alone.
        target = paper_target_count(p)
        if not target:
            return
        short_by = target - count_questions(p.get("sections") or [])
        if short_by <= 0:
            return
        count = min(short_by, TOPUP_MAX)
        # Ask for the topics that are actually missing rather than telling the
        # model to spread the new questions around and hoping. Trimmed to what
        # is being requested so the per-topic numbers still sum to `count`.
        wanted = topic_shortfall(p.get("topic_coverage") or {})
        targets = _trim_targets(wanted, count)
        # Claim the slot before doing any work, so a crash mid-top-up cannot
        # come back for a second go.
        await db.papers.update_one(
            {"id": paper_id},
            {"$set": {
                "topup_auto_attempts": attempts + 1,
                "topup": {
                    "status": "generating",
                    "started_at": utcnow_iso(),
                    "requested": count,
                    "added": 0,
                    "auto": True,
                },
            }},
        )
        logger.info(
            f"Auto top-up: paper {paper_id} is {short_by} question(s) short "
            f"of {target}, requesting {count}"
        )
        await generate_missing_questions_background(
            paper_id=paper_id,
            owner_id=owner_id,
            count=count,
            context_excerpt=context_excerpt,
            feedback_hints=feedback_hints,
            topic_targets=targets,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Auto top-up skipped for {paper_id}: {e}")


@tracked('completion')
async def generate_missing_questions_background(
    paper_id: str,
    owner_id: str,
    count: int,
    context_excerpt: str = "",
    feedback_hints: str = "",
    topic_targets: Optional[dict] = None,
) -> None:
    """Generate `count` extra questions and append them to an existing paper.

    `topic_targets` names how many questions each under-covered topic needs.
    Without it the model is merely asked to spread the new questions around,
    which is how a paper that was already skewed stays skewed.
    """
    tag("paper_topup", paper_id)
    try:
        p = await db.papers.find_one(
            {"id": paper_id, "is_deleted": False}, {"_id": 0}
        )
        if not p:
            return
        if p.get("is_competitive"):
            # Lives in competitive_routes so the RAG anchor logic stays with the
            # rest of the competitive path. Imported here rather than at module
            # scope because competitive_routes imports this module.
            from competitive_routes import generate_missing_competitive_questions

            await generate_missing_competitive_questions(paper_id, count)
            return

        existing_sections, section_plan, repaired_recovery = reconcile_section_plan(
            p.get("sections") or [], p.get("section_plan"), p.get("generation_requirements") or {}
        )
        if repaired_recovery or section_plan != (p.get("section_plan") or []):
            # Persist before generating so merge_topup_sections has the same
            # canonical section layout as the paper the teacher sees.
            await db.papers.update_one(
                {"id": paper_id}, {"$set": {"sections": existing_sections, "section_plan": section_plan}}
            )
        difficulty = p.get("difficulty", "medium")
        normal_plan = p.get("normal_generation_plan")
        if normal_plan and normal_plan.get("contract_version") == NORMAL_CONTRACT_VERSION:
            missing_slots = missing_plan_slots(normal_plan, existing_sections)[:count]
            if not missing_slots:
                raise RuntimeError(
                    "This paper has no missing generated slots. Edit the paper directly instead of adding an unallocated question."
                )
            new_data = await _generate_normal_candidate(
                only_slots=missing_slots,
                plan=normal_plan,
                subject=p.get("subject", ""),
                klass=p.get("class_name", ""),
                context_excerpt=context_excerpt,
                custom_instructions=p.get("custom_instructions") or "",
                feedback_hints=feedback_hints,
                existing_questions=flatten_question_texts(existing_sections),
                ref=f"{paper_id}:slot-topup",
            )
            new_sections = new_data.get("sections") or []
            coercions: dict = {}
            _normalise_paper_questions(
                new_sections, difficulty, coercions, assign_ids=False
            )
            await complete_sections(
                new_sections,
                ref=f"{paper_id}:slot-topup",
                context={
                    "subject": p.get("subject", ""),
                    "klass": p.get("class_name", ""),
                    "difficulty": difficulty,
                },
                flags=coercions,
            )
            await dedupe_sections(new_sections, ref=f"{paper_id}:slot-topup", against=existing_sections)
            await repair_sections(new_sections, ref=f"{paper_id}:slot-topup")
            pending_count = apply_diagram_policy(
                new_sections,
                ref=paper_id,
                max_diagrams=len(missing_slots),
            )
            for section in new_sections:
                for question in section.get("questions") or []:
                    question["diagram_status"] = "pending" if question.get("needs_diagram") else "none"
            before = count_questions(existing_sections)
            merged = merge_normal_sections(normal_plan, existing_sections, new_sections)
            added = count_questions(merged) - before
            target = len(normal_plan.get("slots") or [])
            await _finish_topup(
                paper_id,
                merged,
                added,
                pending_count,
                target,
                list(normal_plan.get("sections") or []),
            )
            issues = validate_normal_plan(merged, normal_plan)
            status = normal_review_status(issues)
            await db.papers.update_one({"id": paper_id}, {"$set": {
                "review_status": status,
                "instruction_check": {
                    "honoured": not issues,
                    "misses": [] if not issues else [status["summary"]],
                    "requested": {},
                    "actual": {},
                    "at": utcnow_iso(),
                },
            }})
            if pending_count:
                await generate_diagrams_background(paper_id, owner_id)
            return
        # The teacher's instructions govern the top-up too -- a paper told
        # to stay in syllabus must not drift on the questions added to fix
        # a shortfall. Counts are deliberately NOT reapplied: this call is
        # asking for the gap, not for the whole paper again.
        legacy_spec = p.get("generation_requirements")
        if not legacy_spec:
            legacy_spec = await parse_instructions(
                p.get("custom_instructions") or "",
                ref=paper_id,
                context={
                    "subject": p.get("subject", ""),
                    "klass": p.get("class_name", ""),
                    "difficulty": difficulty,
                },
            )
            legacy_spec = compile_requirements(
                total_marks=int(p.get("total_marks") or 0), distribution=p.get("distribution") or {},
                format_distribution=p.get("format_distribution") or {}, topics=p.get("topics") or [],
                difficulty=difficulty, instruction_spec=legacy_spec,
                has_blueprint=bool(p.get("section_blueprint")),
            )
        topup_spec = remaining_requirements(legacy_spec, existing_sections, count)
        if legacy_spec.get("section_allocations"):
            count = topup_spec["total_questions"]
            topic_targets = None
            if not count:
                raise RuntimeError("All configured section allocations are already filled. Edit the blueprint before adding questions.")
        prompt = qgen_prompt(
            subject=p.get("subject", ""),
            klass=p.get("class_name", ""),
            topics_weighted=p.get("topics") or [],
            difficulty=difficulty,
            distribution=p.get("distribution") or {},
            total_marks=int(p.get("total_marks") or 0),
            duration=int(p.get("duration_minutes") or 0),
            context_excerpt=context_excerpt,
            feedback_hints=feedback_hints,
            format_distribution=p.get("format_distribution") or {},
            custom_instructions=p.get("custom_instructions") or "",
            section_blueprint=p.get("section_blueprint") or "",
            topup_count=count,
            existing_questions=flatten_question_texts(existing_sections),
            topic_targets=topic_targets or None,
            spec=topup_spec,
        )
        data = await _request_paper_json(
            system_message=(
                "You create original, high-quality exam questions in strict JSON. "
                "Never copy textbook content."
            ),
            user_text=prompt,
            ref=f"{paper_id}:topup",
        )

        new_sections = data.get("sections") or []
        coercions: dict = {}
        _normalise_paper_questions(new_sections, difficulty, coercions)
        # The provider occasionally decorates a real title with "Top-Up
        # Questions". Route by allocation identity before merge so it cannot
        # create a second Section A at the end of the document.
        new_sections, _, _ = reconcile_section_plan(new_sections, section_plan, legacy_spec)
        if not count_questions(new_sections):
            raise RuntimeError("The model returned no usable questions.")

        # Same order as first-pass generation: finish what came in
        # incomplete, repair the LaTeX, then let the diagram policy decide
        # what keeps a figure. All three run on the new
        # questions only — the existing ones have already been through this.
        await complete_sections(
            new_sections,
            ref=paper_id,
            context={
                "subject": p.get("subject", ""),
                "klass": p.get("class_name", ""),
                "difficulty": difficulty,
            },
            flags=coercions,
        )
        # A top-up is the likeliest place to re-ask an existing question: the
        # prompt lists only the first 60 stems, truncated, as things to avoid.
        # Compare against the whole paper, and let only the NEW questions lose.
        await dedupe_sections(new_sections, ref=paper_id, against=existing_sections)
        await repair_sections(new_sections, ref=paper_id)
        normalize_internal_choice_counts(new_sections)
        pending_count = apply_diagram_policy(new_sections, ref=paper_id,
            max_diagrams=topup_spec.get("diagram_count", count if legacy_spec.get("section_allocations") else max(0, 5 - sum(
                bool(q.get("needs_diagram")) for s in existing_sections for q in s.get("questions", [])
            ))))
        for s in new_sections:
            for q in s.get("questions", []):
                q["diagram_status"] = "pending" if q.get("needs_diagram") else "none"

        target = paper_target_count(p)
        before = count_questions(existing_sections)
        merged = merge_topup_sections(existing_sections, new_sections, limit=target)
        merged, section_plan, _ = reconcile_section_plan(merged, section_plan, legacy_spec)
        normalize_internal_choice_counts(merged)
        added = count_questions(merged) - before

        await _finish_topup(paper_id, merged, added, pending_count, target, section_plan)
        await db.papers.update_one({"id": paper_id}, {"$set": {
            "instruction_check": verify_instructions(merged, legacy_spec),
        }})
        logger.info(f"Top-up added {added} question(s) to paper {paper_id}")

        if pending_count > 0:
            await generate_diagrams_background(paper_id, owner_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Question top-up failed")
        await _fail_topup(paper_id, str(e))


# ---------------------------------------------------------------------------
# Single-question regeneration — replace one question, keep its slot
# ---------------------------------------------------------------------------
# A teacher who dislikes one question in twenty had two options: throw the whole
# paper away (`/regenerate`) or rewrite it by hand. This replaces exactly one,
# locked to the slot the old question occupied -- same section, marks, format,
# type, difficulty, topic, and on a blueprint paper the same allocation row.
#
# The rule that keeps the duplication fix intact: the replacement is checked
# against every OTHER question on the paper by the same question_dedupe
# constants a generated question faces, and every version this slot has already
# shown the teacher goes into the prompt's avoid list, so clicking the button
# repeatedly cannot cycle back to something already rejected.
REGEN_MAX_ATTEMPTS = int(os.environ.get("REGEN_MAX_ATTEMPTS", "3"))
# Past versions remembered per question. Five is plenty of rope for a teacher
# clicking through options and costs ~650 bytes on the question, against a 16 MB
# document limit.
REGEN_HISTORY_MAX = 5
# Everything about a question that describes its SLOT rather than its wording.
# The model is never asked for any of these -- they are copied from the question
# being replaced, which is the only way to be sure section_blueprints'
# allocation checks still pass: the old question already satisfied them.
from slot_identity import SLOT_IDENTITY_FIELDS, TEACHER_FIELDS
PRESERVED_SLOT_FIELDS = SLOT_IDENTITY_FIELDS + TEACHER_FIELDS
TARGETED_OR_ENABLED = os.environ.get("TARGETED_OR_ENABLED", "false").lower() in ("true", "1", "yes")


def _independent_or_branches(question):
    from section_blueprints import _OR_SEPARATOR_RE
    from question_repair import validate_question
    text = question.get('question') or ''
    if (question.get('format') not in ('short_answer', 'long_answer')
        or any(question.get(key) for key in ('parent_lead_in', 'part_label', 'passage', 'shared_passage', 'needs_diagram', 'diagram_spec', 'diagram_path'))
        or re.search(r'(?im)\b(?:passage|above|below|following|figure|diagram)\b|^\s*\([a-ivx]+\)', text)):
        return None
    branches = [part.strip() for part in _OR_SEPARATOR_RE.split(text)]
    return branches if all(not validate_question({**question, 'question': part}) for part in branches) else None

# What the model IS asked for -- the stem and the things that follow from it.
_CANDIDATE_FIELDS = (
    "question", "options", "correct_option", "needs_diagram", "diagram_spec",
    "shared_stimulus",
)
# Disjoint on purpose: the replacement is built by merging the model's fields
# over the slot's, and that merge IS the re-stamp. Adding a field to both lists
# would silently let the model set a slot value.
assert not set(PRESERVED_SLOT_FIELDS) & set(_CANDIDATE_FIELDS)


class RegenerationFailed(Exception):
    """No replacement could be found that is not already on the paper."""


def _find_question(sections: list, question_id: str) -> tuple:
    """(section, index, question) for `question_id`, or (None, -1, None)."""
    for s in sections or []:
        for i, q in enumerate(s.get("questions") or []):
            if isinstance(q, dict) and q.get("id") == question_id:
                return s, i, q
    return None, -1, None


def _slot_of(question: dict) -> dict:
    """The fixed shape of the slot this question occupies."""
    return {k: question.get(k) for k in PRESERVED_SLOT_FIELDS if k in question}


def internal_choice_repair_targets(sections: list, spec: dict, *, allow_partial: bool = False) -> list[dict]:
    """Choose the smallest deterministic set of slots needed to fix OR totals."""
    by_id = {
        q.get("id"): q
        for section in sections or []
        for q in section.get("questions") or []
        if isinstance(q, dict) and q.get("id")
    }
    targets: list[dict] = []
    for miss in internal_choice_misses(sections, spec):
        questions = [by_id[qid] for qid in miss["eligible_question_ids"] if qid in by_id]
        delta = int(miss["expected"]) - int(miss["actual"])
        # Preserve compliant and teacher-starred questions where possible.
        if delta > 0:
            candidates = [q for q in questions if derived_internal_choice_count(q) == 0]
            candidates.sort(key=lambda q: (bool(q.get("important")), q.get("id", "")))
            if len(candidates) < delta and not allow_partial:
                raise RegenerationFailed("There are not enough question slots to add the required internal OR choices.")
            targets.extend({"question_id": q["id"], "required_internal_choice_count": 1} for q in candidates[:delta])
            continue

        remaining = -delta
        # Remove several accidental separators from one malformed question in a
        # single rewrite before disturbing a second otherwise-valid question.
        candidates = [q for q in questions if derived_internal_choice_count(q) > 0]
        candidates.sort(key=lambda q: (-derived_internal_choice_count(q), bool(q.get("important")), q.get("id", "")))
        for question in candidates:
            if remaining <= 0:
                break
            current = derived_internal_choice_count(question)
            reduced = min(current, remaining)
            targets.append({
                "question_id": question["id"],
                "required_internal_choice_count": current - reduced,
            })
            remaining -= reduced
        if remaining:
            raise RegenerationFailed("Could not select question slots to remove the extra internal OR choices.")
    from question_repair import alternative_issues
    targeted = {t['question_id'] for t in targets}
    targets.extend({'question_id': qid, 'required_internal_choice_count': derived_internal_choice_count(q)}
                   for qid, q in by_id.items() if qid not in targeted and alternative_issues(q))
    return targets


async def repair_internal_choices(
    paper: dict,
    *,
    context_excerpt: str = "",
    feedback_hints: str = "",
    on_accept=None,
) -> dict:
    """Retain each accepted local repair even when a later provider call fails."""
    spec = paper.get("generation_requirements") or {}
    working = copy.deepcopy(paper)
    normalize_internal_choice_counts(working.get("sections") or [])
    targets = internal_choice_repair_targets(working.get("sections") or [], spec, allow_partial=True)
    repairs, errors = [], []
    semaphore, lock = _asyncio.Semaphore(3), _asyncio.Lock()

    async def repair(target):
        async with semaphore:
            try:
                result = await regenerate_question(
                    working, target["question_id"], context_excerpt=context_excerpt,
                    feedback_hints=feedback_hints,
                    required_internal_choice_count=target["required_internal_choice_count"],
                )
                async with lock:
                    if target not in internal_choice_repair_targets(working['sections'], spec, allow_partial=True):
                        return
                    others = [q for s in working['sections'] for q in s.get('questions', []) if q.get('id') != target['question_id']]
                    if (await is_duplicate(result['question'], others, use_llm=False))[0]:
                        return
                    for section in working["sections"]:
                        for index, question in enumerate(section.get("questions") or []):
                            if question.get("id") == target["question_id"]:
                                section["questions"][index] = result["question"]
                    repairs.append({**target, "old_text": result["old_text"], "question": result["question"],
                                    "diagram_pending": result["diagram_pending"]})
                    if on_accept:
                        await on_accept(copy.deepcopy(working["sections"]))
            except Exception as exc:
                errors.append(str(exc)[:500])

    await _asyncio.gather(*(repair(target) for target in targets))
    normalize_internal_choice_counts(working.get("sections") or [])
    return {"sections": working.get("sections") or [], "repairs": repairs,
            "remaining": internal_choice_misses(working.get("sections") or [], spec),
            "errors": errors, "diagram_pending": any(repair["diagram_pending"] for repair in repairs)}


def _extract_candidate(data) -> dict:
    """Pull one question object out of whatever shape the model replied with.

    The prompt asks for a bare object. Models sometimes wrap it in the paper
    shape they have seen a thousand times, or nest it under "question" -- both
    are recoverable, and losing a good question to a wrapper would just spend
    another attempt.
    """
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise RuntimeError("The model returned no usable question.")
    if isinstance(data.get("sections"), list):
        for s in data["sections"]:
            for q in (s or {}).get("questions") or []:
                if isinstance(q, dict) and str(q.get("question") or "").strip():
                    return q
        raise RuntimeError("The model returned no usable question.")
    inner = data.get("question")
    if isinstance(inner, dict):
        data = inner
    if not str(data.get("question") or "").strip():
        raise RuntimeError("The model returned no usable question.")
    return data


def _regen_history(old: list | None, entries: list) -> list:
    """The slot's rejected-versions list, newest last and capped.

    Holds both the versions a teacher clicked away from and the candidates the
    duplicate check turned down: click three must avoid click two's rejects
    exactly as much as click one's output.
    """
    out = list(old or [])
    for text in entries:
        stem = " ".join(str(text or "").split())[:STEM_CHARS]
        if stem and stem not in out:
            out.append(stem)
    return out[-REGEN_HISTORY_MAX:]


async def regenerate_question(
    paper: dict,
    question_id: str,
    *,
    context_excerpt: str = "",
    feedback_hints: str = "",
    required_internal_choice_count: int | None = None,
) -> dict:
    """Produce a replacement for one question, in the same slot.

    Returns {"sections", "question", "old_text", "attempts", "diagram_pending"}.
    Takes a paper dict and RETURNS new sections without touching the database,
    which is what lets the tests run without Mongo and keeps the write in one
    place in the caller.

    Raises RegenerationFailed when every attempt came back as something the
    paper already has; the caller must then write nothing at all.
    """
    paper_id = paper.get("id", "")
    sections = copy.deepcopy(paper.get("sections") or [])
    section, index, old = _find_question(sections, question_id)
    if old is None:
        raise RegenerationFailed("Question not found on this paper.")

    slot = _slot_of(old)
    if required_internal_choice_count is not None:
        # The OR count is normally part of a fixed slot.  Targeted recovery is
        # the deliberate exception: it changes exactly this rendered property
        # while retaining every allocation identity and other slot field.
        slot["internal_choice_count"] = required_internal_choice_count
    old_text = old.get("question") or ""
    # A case passage is shared parent data, not disposable content on the
    # first child. Keep it available to a child rewrite and preserve it when
    # the provider correctly returns only the requested child stem.
    case_siblings = [q for s in sections for q in (s.get("questions") or [])
                     if q.get("id") != question_id and q.get("case_study")
                     and q.get("section_id") == old.get("section_id") and q.get("parent_id") == old.get("parent_id")]
    shared_stimulus = next((q.get("shared_stimulus") for q in [old, *case_siblings]
                            if str(q.get("shared_stimulus") or "").strip()), "")
    difficulty = str(slot.get("difficulty") or paper.get("difficulty") or "medium")
    # Everything the replacement must not collide with. The old question is not
    # in here -- it is leaving -- but its text is in `rejected` below, because
    # returning the question the teacher just rejected is the one answer that is
    # certainly wrong.
    rest = [
        q
        for s in sections
        for q in (s.get("questions") or [])
        if isinstance(q, dict) and q.get("id") != question_id
    ]
    other_texts = [q.get("question", "") for q in rest if q.get("question")]
    # Everything this slot has already produced, oldest first: the versions
    # stored from previous clicks, the one on the paper right now, and anything
    # this run has had to throw away. Kept uncapped for the prompt -- the stored
    # cap is about document size, and quietly reusing it here would start
    # hiding rejects from the model mid-retry.
    prior: list = list(old.get("regen_history") or [])
    rejected_now: list = []

    branches = _independent_or_branches(old) if TARGETED_OR_ENABLED and required_internal_choice_count is not None and not paper.get('is_competitive') else None
    branch_only = bool(branches and required_internal_choice_count > len(branches) - 1)
    candidate = None
    attempts = 0
    for attempt in range(1, REGEN_MAX_ATTEMPTS + 1):
        attempts = attempt
        prompt = regen_question_prompt(
            subject=paper.get("subject", ""),
            klass=paper.get("class_name", ""),
            slot=slot,
            context_excerpt=context_excerpt,
            feedback_hints=feedback_hints,
            custom_instructions=paper.get("custom_instructions") or "",
            other_questions=other_texts,
            rejected=prior + ([old_text] if old_text else []) + rejected_now,
            attempt=attempt,
        )
        if old.get("case_study"):
            prompt += ('\nCASE CONTEXT (immutable for this child repair): ' + json.dumps({
                "shared_stimulus": shared_stimulus,
                "siblings": [{"slot_id": q.get("slot_id"), "question": q.get("question")} for q in case_siblings],
            }, ensure_ascii=False) + '\nWrite only the requested child; do not remove or replace the shared stimulus.')
        if branch_only:
            prompt += ('\nWrite ONLY the missing independent alternative branches, separated by standalone OR lines. '
                       'Each branch must independently match the topic, marks and difficulty. Do not repeat or rewrite these accepted branches: '
                       + json.dumps(branches) + '\nMissing branches: ' + str(required_internal_choice_count - len(branches) + 1))
        try:
            raw = await chat_complete(system_message=REGEN_SYSTEM, user_text=prompt)
            fresh = _extract_candidate(parse_json_response(raw))
        except Exception as exc:
            logger.warning("Regeneration provider attempt %s failed for %s: %s", attempt, paper_id, exc)
            continue
        if branch_only:
            fresh['question'] = '\nOR\n'.join([*branches, str(fresh.get('question') or '')])

        # Build the replacement from the slot outward: the slot's values first
        # (so the normaliser's MCQ branch sees the right format, and so the id,
        # marks, topic and allocation row are the old question's), then only the
        # fields the model was actually asked for. Anything else it sent --
        # a topic it preferred, marks it thought better of -- is dropped here
        # rather than corrected later.
        cand = {**slot, **{k: fresh[k] for k in _CANDIDATE_FIELDS if k in fresh}}
        if old.get("case_study") and shared_stimulus and not cand.get("shared_stimulus"):
            cand["shared_stimulus"] = shared_stimulus

        coercions: dict = {}
        holder = [{"title": section.get("title", ""), "questions": [cand]}]
        _normalise_paper_questions(holder, difficulty, coercions, assign_ids=False)
        await complete_sections(
            holder,
            ref=paper_id,
            context={
                "subject": paper.get("subject", ""),
                "klass": paper.get("class_name", ""),
                "difficulty": difficulty,
            },
            flags=coercions,
        )
        cand = holder[0]["questions"][0]

        actual_choice_count = derived_internal_choice_count(cand)
        if required_internal_choice_count is not None and actual_choice_count != required_internal_choice_count:
            logger.info(
                f"[regen] {paper_id} attempt {attempt}/{REGEN_MAX_ATTEMPTS} "
                f"returned {actual_choice_count} OR choices; expected {required_internal_choice_count}"
            )
            rejected_now.append(cand.get("question") or "")
            continue
        cand["internal_choice_count"] = actual_choice_count

        duplicate, reason = await is_duplicate(
            cand, rest, topic=str(slot.get("topic") or ""), ref=paper_id
        )
        if not duplicate:
            candidate = cand
            break
        logger.info(
            f"[regen] {paper_id} attempt {attempt}/{REGEN_MAX_ATTEMPTS} rejected "
            f"({reason})"
        )
        rejected_now.append(cand.get("question") or "")

    if candidate is None:
        raise RegenerationFailed(
            "Every rewrite came back too close to a question already on this "
            "paper. Nothing was changed."
        )

    # After the gate, not before: a repair call spent on a question about to be
    # thrown away is the reason dedupe_sections documents this same ordering.
    await repair_sections([{"questions": [candidate]}], ref=paper_id)
    normalize_internal_choice_counts([{"questions": [candidate]}])

    from section_blueprints import _OR_SEPARATOR_RE
    from question_repair import validate_question
    parts = _OR_SEPARATOR_RE.split(candidate.get('question') or '')
    if (any(validate_question({**candidate, 'question': part.strip()}) for part in parts)
        or len({part.strip().casefold() for part in parts}) != len(parts)
        or (required_internal_choice_count is not None and derived_internal_choice_count(candidate) != required_internal_choice_count)):
        raise RegenerationFailed("Replacement alternatives did not pass validation.")

    if branch_only:
        checked = await _request_paper_json(
            system_message='Review independent exam alternatives. Return strict JSON.',
            user_text='Check EVERY alternative for completeness, correctness, assigned topic, marks and difficulty; reject duplicates. '
                      'Return {"valid": true} only if all checks pass.\n' + json.dumps({'slot': slot, 'branches': parts}),
            ref=f'{paper_id}:or-review', attempts=1)
        if checked.get('valid') is not True:
            raise RegenerationFailed('The missing alternatives did not pass content review.')
        # Normalizers and repair providers may not rewrite accepted branches.
        if parts[:len(branches)] != branches:
            if [part.strip() for part in parts[:len(branches)]] != branches:
                raise RegenerationFailed('An accepted alternative was changed during repair.')

    # The slot's figure budget, and nothing more. On a blueprint paper
    # allocation_misses complains BOTH ways -- a row that requires a diagram and
    # a row that forbids one -- so matching whatever the old question had is the
    # only setting that cannot introduce a mismatch.
    candidate.pop("diagram_path", None)
    apply_diagram_policy(
        [{"questions": [candidate]}],
        ref=paper_id,
        max_diagrams=1 if old.get("needs_diagram") else 0,
    )
    diagram_pending = bool(candidate.get("needs_diagram"))
    candidate["diagram_status"] = "pending" if diagram_pending else "none"
    if required_internal_choice_count is not None and diagram_pending:
        # An automatic replacement cannot evict a published figure before its
        # own immutable image is available and the whole candidate is usable.
        await generate_diagrams_for_paper(paper_id, paper.get('owner_id', ''), [{'questions': [candidate]}])
        if not candidate.get('diagram_path'):
            raise RegenerationFailed('The replacement figure could not be completed.')
        candidate['diagram_status'] = 'ready'
        diagram_pending = False
    # Dropping diagram_path (above) is what makes the picture update at all:
    # generate_diagrams_for_paper skips any question that already has one, and
    # on the front end QuestionDiagram only refetches because losing the path
    # unmounts the <img>. Content-addressed assets retain earlier previews.

    candidate["regen_history"] = _regen_history(prior, [old_text] + rejected_now)
    section["questions"][index] = candidate

    return {
        "sections": sections,
        "question": candidate,
        "old_text": old_text,
        "attempts": attempts,
        "diagram_pending": diagram_pending,
    }


def _invalidate_solution(paper: dict, question_id: str, targeted: bool) -> dict | None:
    """Blank the one answer a regenerated question just made wrong.

    reconcile_solution_shell derives "missing" from blank answers on questions
    that still have text, so blanking this one is exactly what lets
    POST /solution/generate-missing refill it alone. Rebuilding the shell here
    would do more and could reorder it.

    `targeted` off when a solution job is live: that job rewrites the whole
    document and would put the old answer straight back, so the paper is only
    marked stale and the teacher is shown the existing banner.
    """
    solution = paper.get("solution")
    if not solution:
        return None
    solution = copy.deepcopy(solution)
    solution["is_stale"] = True
    if not targeted:
        return solution
    for sec in solution.get("sections") or []:
        for answer in sec.get("answers") or []:
            if answer.get("question_id") != question_id:
                continue
            if answer.get("answer"):
                solution["completed"] = max(0, int(solution.get("completed") or 0) - 1)
            answer["answer"] = ""
    if solution.get("status") == "ready":
        solution["status"] = "partial"
    return solution


@tracked('regeneration')
async def regenerate_question_background(
    paper_id: str,
    owner_id: str,
    question_id: str,
    context_excerpt: str = "",
    feedback_hints: str = "",
    solution_is_live: bool = False,
) -> None:
    """Regenerate one question and write the result. One write, on success only.

    Best-effort by construction, like the top-up: the paper is already saved and
    on screen, so anything that goes wrong here leaves it exactly as it was, with
    a failure recorded in `regen` for the UI to explain.
    """
    tag("paper_regen", paper_id)
    try:
        p = await db.papers.find_one(
            {"id": paper_id, "is_deleted": False}, {"_id": 0}
        )
        if not p:
            return
        result = await regenerate_question(
            p,
            question_id,
            context_excerpt=context_excerpt,
            feedback_hints=feedback_hints,
        )
        normalize_internal_choice_counts(result["sections"])

        update = {
            "sections": result["sections"],
            "instruction_check": verify_instructions(result["sections"], p.get("generation_requirements") or {}),
            "regen": {
                "status": "ready",
                "question_id": question_id,
                "finished_at": utcnow_iso(),
            },
        }
        normal_plan = p.get("normal_generation_plan")
        if normal_plan:
            issues = validate_normal_plan(result["sections"], normal_plan)
            status = normal_review_status(issues)
            update["review_status"] = status
            update["instruction_check"] = {
                "honoured": not issues,
                "misses": [] if not issues else [status["summary"]],
                "requested": {},
                "actual": {},
                "at": utcnow_iso(),
            }
        if result["diagram_pending"]:
            update["diagrams_pending"] = sum(
                1
                for s in result["sections"]
                for q in (s.get("questions") or [])
                if q.get("diagram_status") == "pending"
            )
        solution = _invalidate_solution(p, question_id, targeted=not solution_is_live)
        if solution is not None:
            update["solution"] = solution
        await db.papers.update_one({"id": paper_id}, {"$set": update})

        # A regenerate is a deliberate rejection of a specific question -- a
        # cleaner signal than most manual edits, and load_paper_feedback_hints
        # reads it back into the next paper's prompt.
        await db.paper_edits.insert_one({
            "paper_id": paper_id,
            "owner_id": owner_id,
            "subject": p.get("subject", ""),
            "class_name": p.get("class_name", ""),
            "edit_type": "regenerate_question",
            "original": result["old_text"],
            "revised": result["question"].get("question", ""),
            "created_at": utcnow_iso(),
        })
        logger.info(
            f"[regen] {paper_id} replaced {question_id} in "
            f"{result['attempts']} attempt(s)"
        )

        if result["diagram_pending"]:
            await generate_diagrams_background(paper_id, owner_id)
    except RegenerationFailed as e:
        logger.info(f"[regen] {paper_id} gave up on {question_id}: {e}")
        await _fail_regen(paper_id, question_id, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("Question regeneration failed")
        await _fail_regen(paper_id, question_id, str(e))


async def _fail_regen(paper_id: str, question_id: str, err: str) -> None:
    """Record a failed regeneration. Deliberately touches nothing but `regen`."""
    await db.papers.update_one(
        {"id": paper_id},
        {"$set": {"regen": {
            "status": "failed",
            "question_id": question_id,
            "error": str(err)[:500],
            "finished_at": utcnow_iso(),
        }}},
    )


@tracked('choice_repair')
async def repair_internal_choices_background(
    paper_id: str,
    owner_id: str,
    context_excerpt: str = "",
    feedback_hints: str = "",
    solution_is_live: bool = False,
) -> None:
    """Run a teacher-requested internal-OR repair without hiding the paper."""
    tag("paper_internal_choice_repair", paper_id)
    try:
        paper = await db.papers.find_one({"id": paper_id, "is_deleted": False}, {"_id": 0})
        if not paper:
            return
        await db.papers.update_one({"id": paper_id}, {"$set": {"generation_recovery.status": "recovering"}})
        result = await repair_internal_choices(
            paper, context_excerpt=context_excerpt, feedback_hints=feedback_hints
        )
        repaired_check = verify_instructions(result["sections"], paper.get("generation_requirements") or {})
        repaired_check["misses"] = list(dict.fromkeys([
            *(repaired_check.get("misses") or []),
            *allocation_misses(result["sections"], paper.get("generation_requirements") or {}),
        ]))
        repaired_check["honoured"] = not repaired_check["misses"]
        remaining_choice_misses = internal_choice_misses(
            result["sections"], paper.get("generation_requirements") or {}
        )
        quality_misses = list(repaired_check["misses"])
        if remaining_choice_misses:
            quality_misses.append("Required internal choices remain incomplete.")
        update = {
            "sections": result["sections"],
            "instruction_check": repaired_check,
            "quality_review.status": "needs_review" if quality_misses else "passed",
            "quality_review.stage": "needs_review" if quality_misses else "ready",
            "quality_review.message": "; ".join(quality_misses)[:500] or None,
            "quality_review.failed_gates": quality_misses,
            "quality_review.internal_choice_diagnostics": remaining_choice_misses,
            "quality_review.finished_at": utcnow_iso(),
            "generation_recovery": {
                "status": "ready", "reason": "internal_choices", "attempt": 1,
                "max_attempts": 1, "finished_at": utcnow_iso(), "validation_summary": [],
                "diagnostics": [],
            },
        }
        solution = paper
        for repair in result["repairs"]:
            changed = _invalidate_solution(solution, repair["question_id"], targeted=not solution_is_live)
            if changed is not None:
                solution = {**solution, "solution": changed}
        if solution.get("solution") is not paper.get("solution"):
            update["solution"] = solution["solution"]
        if result["diagram_pending"]:
            update["diagrams_pending"] = sum(
                1 for section in result["sections"] for question in (section.get("questions") or [])
                if question.get("diagram_status") == "pending"
            )
        await db.papers.update_one({"id": paper_id}, {"$set": update})
        for repair in result["repairs"]:
            await db.paper_edits.insert_one({
                "paper_id": paper_id, "owner_id": owner_id,
                "subject": paper.get("subject", ""), "class_name": paper.get("class_name", ""),
                "edit_type": "repair_internal_choice", "original": repair["old_text"],
                "revised": repair["question"].get("question", ""), "created_at": utcnow_iso(),
            })
        if result["diagram_pending"]:
            await generate_diagrams_background(paper_id, owner_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Internal OR repair failed")
        await db.papers.update_one({"id": paper_id}, {"$set": {"generation_recovery": {
            "status": "needs_review", "reason": "internal_choices", "attempt": 1,
            "max_attempts": 1, "finished_at": utcnow_iso(),
            "validation_summary": [str(exc)[:500]], "diagnostics": [],
        }}})


# ---------------------------------------------------------------------------
# Solution generation (synchronous build + async batched background)
# ---------------------------------------------------------------------------
# Questions go to the LLM in batches so a long paper doesn't blow past token
# limits or trigger a gateway timeout. The sweep pass that retries whatever
# came back blank uses a smaller batch on purpose: the dominant failure mode is
# the model truncating its JSON mid-answer, and asking for less output is the
# direct counter-measure.
BATCH_SIZE = 5
SWEEP_BATCH_SIZE = 2


def _questions_payload(paper: dict) -> list[dict]:
    """Flatten a paper's sections into the shape solution_prompt() expects."""
    out: list[dict] = []
    passages = {
        (q.get("section_id"), q.get("parent_id")): q.get("shared_stimulus")
        for s in paper.get("sections", []) for q in s.get("questions", [])
        if q.get("case_study") and q.get("parent_id") and q.get("shared_stimulus")
    }
    for s in paper.get("sections", []):
        for q in s.get("questions", []):
            if not q.get("question"):
                continue
            item = {
                "id": q["id"],
                "question": q["question"],
                "type": q.get("type", "concept"),
                "marks": q.get("marks", 2),
                "format": q.get("format", ""),
            }
            # MCQ choices live outside the question text, so the solver only
            # sees the distractors if we pass them. The stored key rides along
            # because it is what the mock-test grader marks against — a worked
            # solution that quietly lands on a different option would contradict
            # the student's score. Already normalised to a 4-string list and a
            # valid index by _normalise_paper_questions().
            if (item["format"] or "").lower() == "mcq" and q.get("options"):
                item["options"] = q["options"]
                item["correct_option"] = q.get("correct_option", 0)
            stimulus = passages.get((q.get("section_id"), q.get("parent_id")))
            if stimulus:
                item["shared_stimulus"] = stimulus
                item["parent_id"] = q.get("parent_id")
            out.append(item)
    return out


async def _solution_context(paper: dict, user_id: str) -> tuple[str, str]:
    """(context_excerpt, feedback_hints) — the two prompt inputs that are the
    same for every batch, so they get loaded once per job."""
    tb_ids = paper.get("textbook_ids") or (
        [paper["textbook_id"]] if paper.get("textbook_id") else []
    )
    merged_chunks: list = []
    max_per = max(2, 5 // max(1, len(tb_ids)))
    for tid in tb_ids:
        tb = await db.textbooks.find_one(
            {"id": tid, "is_deleted": False}, {"_id": 0}
        )
        if tb:
            merged_chunks.extend((await load_textbook_chunks(tb))[:max_per])
    context_excerpt = "\n\n".join(merged_chunks)[:8000]

    feedback_hints = await load_solution_feedback(
        user_id, paper.get("subject", ""), paper.get("class_name", "")
    )
    return context_excerpt, feedback_hints


def reconcile_solution_shell(
    paper: dict, existing: Optional[dict]
) -> tuple[dict, list[str]]:
    """Return (solution_shell, missing_question_ids).

    Mirrors paper["sections"], carrying any existing answer across by
    question_id. A question added to the paper after the solution was generated
    gains a blank entry; an answer whose question no longer exists is dropped.

    Three shapes of stored solution have to survive this: the full shell written
    by POST /solution/generate, the three-key dict returned by
    build_solution_for_paper(), and the four-key dict left behind by older
    PATCH /solution writes. That is why "missing" is derived from blank answers
    rather than from the completed/total counters — those are absent on two of
    the three.
    """
    prev: dict[str, str] = {}
    prev_by_slot: dict[str, str] = {}
    previous_answers: list[dict] = []
    for sec in (existing or {}).get("sections") or []:
        for a in sec.get("answers") or []:
            previous_answers.append(a)
            qid = a.get("question_id")
            if qid:
                prev[qid] = a.get("answer") or ""
            if a.get("slot_id"):
                prev_by_slot[a["slot_id"]] = a.get("answer") or ""

    paper_questions = [
        q
        for section in paper.get("sections", [])
        for q in section.get("questions", [])
        if q.get("id")
    ]
    current_ids = {q["id"] for q in paper_questions}
    # Compatibility repair for papers generated during the normal-contract
    # rollout.  Those jobs could assign a fresh UUID on resume after a complete
    # solution had already been saved.  Normal slots have immutable order, so
    # when *none* of the old ids match and the shapes are identical, position
    # is an unambiguous bridge to the stable slot identity.  This path is never
    # used for templates or partially matching/edited papers.
    positional: dict[str, str] = {}
    if (
        paper_questions
        and all(q.get("slot_id") for q in paper_questions)
        and len(previous_answers) == len(paper_questions)
        and not any(a.get("question_id") in current_ids for a in previous_answers)
    ):
        positional = {
            q["id"]: (answer.get("answer") or "")
            for q, answer in zip(paper_questions, previous_answers)
        }

    sections: list = []
    missing: list[str] = []
    total = 0
    completed = 0
    for s in paper.get("sections", []):
        answers = []
        for q in s.get("questions", []):
            qid = q.get("id")
            if not qid:
                continue
            slot_id = q.get("slot_id")
            if qid in prev:
                text = prev[qid]
            elif slot_id and slot_id in prev_by_slot:
                text = prev_by_slot[slot_id]
            else:
                text = positional.get(qid, "")
            answer = {"question_id": qid, "answer": text}
            if slot_id:
                answer["slot_id"] = slot_id
            answers.append(answer)
            total += 1
            if text:
                completed += 1
            elif q.get("question"):
                # Only worth retrying where there is a question to answer.
                missing.append(qid)
        sections.append({"title": s.get("title", "Section"), "answers": answers})

    shell = dict(existing or {})
    shell["sections"] = sections
    shell["total"] = total
    shell["completed"] = completed
    shell.setdefault("generated_at", utcnow_iso())
    shell.setdefault("is_stale", False)
    return shell, missing


async def _run_solution_batches(
    subject: str,
    klass: str,
    payload: list[dict],
    context_excerpt: str,
    feedback_hints: str,
    batch_size: int,
    answers: dict[str, str],
    label: str = "batch",
    on_batch=None,
) -> Optional[Exception]:
    """Answer `payload` in batches, writing results into `answers` in place.

    Returns the last exception seen, or None if every batch succeeded. A batch
    that fails both attempts is logged and skipped — its questions simply stay
    blank, which is what the sweep pass and the partial-retry endpoint exist to
    clean up.
    """
    last_err: Optional[Exception] = None
    n_batches = (len(payload) + batch_size - 1) // batch_size
    for bi in range(n_batches):
        batch = payload[bi * batch_size : (bi + 1) * batch_size]
        prompt = solution_prompt(
            subject=subject,
            klass=klass,
            context_excerpt=context_excerpt,
            questions_payload=batch,
            feedback_hints=feedback_hints,
        )
        for attempt in range(2):
            try:
                raw = await chat_complete(
                    system_message=SOLUTION_SYSTEM, user_text=prompt
                )
                data = parse_json_response(raw)
                for a in data.get("answers") or []:
                    qid = a.get("question_id")
                    text = (a.get("answer") or "").strip()
                    if qid and text:
                        # Deterministic LaTeX fixes only — free, and they land
                        # before _persist() shows the answer to the user. The
                        # model-backed repair for whatever this cannot fix runs
                        # once per solution, after the sweep.
                        answers[qid] = repair_text(text)[0]
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(
                    f"Solution {label} {bi + 1}/{n_batches} attempt {attempt + 1} failed: {e}"
                )
        if on_batch is not None:
            await on_batch()
    return last_err


def _blank_of(payload: list[dict], answers: dict[str, str]) -> list[dict]:
    """The subset of `payload` that still has no answer."""
    return [q for q in payload if not answers.get(q["id"])]


async def build_solution_for_paper(paper: dict, user_id: str) -> dict:
    """Generate a solution for a single paper. Returns the solution dict.
    Raises HTTPException on failure. Does not persist."""
    tag("solution", paper.get("id", ""))

    payload = _questions_payload(paper)
    if not payload:
        raise HTTPException(status_code=400, detail="Paper has no questions to solve")

    context_excerpt, feedback_hints = await _solution_context(paper, user_id)
    subject = paper.get("subject", "")
    klass = paper.get("class_name", "")

    answers: dict[str, str] = {}
    last_err = await _run_solution_batches(
        subject, klass, payload, context_excerpt, feedback_hints,
        BATCH_SIZE, answers,
    )

    blank = _blank_of(payload, answers)
    if blank:
        last_err = await _run_solution_batches(
            subject, klass, blank, context_excerpt, feedback_hints,
            SWEEP_BATCH_SIZE, answers, label="sweep",
        ) or last_err

    if not answers and last_err:
        raise HTTPException(
            status_code=502, detail=f"Solution failed: {last_err}"
        )

    # Whatever the deterministic pass inside _run_solution_batches could not fix
    # goes to the repair model here — one batched call for the whole answer key.
    latex_stats = await repair_answers(answers, ref=paper.get("id", ""))

    # reconcile_solution_shell only reads a flat question_id -> answer map out of
    # `existing`, so the section title here is irrelevant — it rebuilds the
    # section structure from the paper.
    shell, _missing = reconcile_solution_shell(
        paper,
        {
            "generated_at": utcnow_iso(),
            "is_stale": False,
            "sections": [{
                "title": "",
                "answers": [
                    {"question_id": qid, "answer": text}
                    for qid, text in answers.items()
                ],
            }],
        },
    )
    shell["status"] = (
        "ready" if shell["total"] and shell["completed"] == shell["total"] else "partial"
    )
    shell["finished_at"] = utcnow_iso()
    shell["latex_check"] = latex_stats
    return shell


@tracked('solution')
async def generate_solution_background(
    paper_id: str, user_id: str, only_qids: Optional[list[str]] = None
) -> None:
    """Run batched solution generation, updating the paper doc as each batch
    completes so the frontend can show progress.

    `only_qids` restricts the job to a subset of questions — the partial-retry
    path behind POST /solution/generate-missing. In that mode an answer that
    already has content is never overwritten, so retrying one failed question
    cannot clobber the other answers or a teacher's manual edit.
    """
    tag("solution", paper_id)
    fill_only = only_qids is not None
    try:
        p = await db.papers.find_one(
            {"id": paper_id, "is_deleted": False}, {"_id": 0}
        )
        if not p:
            return

        payload = _questions_payload(p)
        if fill_only:
            wanted = set(only_qids or [])
            payload = [q for q in payload if q["id"] in wanted]

        answers: dict[str, str] = {}

        # The questions THIS job was asked to answer. A model that volunteers an
        # answer for anything outside this set must never reach the document —
        # that is what protects a teacher's manual edits on a targeted retry.
        owned = {q["id"] for q in payload}

        async def _persist(force: bool = False) -> None:
            """Merge what we have so far into the stored doc. Re-read every time
            rather than holding the document — a teacher may be editing other
            answers while this runs.

            `force` exists for exactly one caller: the LaTeX repair pass below.
            Those answers were written by an earlier _persist in this same job,
            so the fill_only guard would otherwise read them as pre-existing
            content and silently drop the repair. It is scoped to `owned` so it
            re-opens only the answers this job wrote itself.
            """
            current = await db.papers.find_one(
                {"id": paper_id}, {"_id": 0, "solution": 1, "sections": 1}
            )
            from question_revision import sources
            original_sources = sources(p.get('sections'))
            current_sources = sources((current or {}).get('sections'))
            sol = copy.deepcopy((current or {}).get("solution") or {})
            for sec in sol.get("sections", []):
                for ans in sec.get("answers", []):
                    qid = ans.get("question_id")
                    if qid not in answers or original_sources.get(qid) != current_sources.get(qid):
                        continue
                    if fill_only and ans.get("answer") and not (
                        force and qid in owned
                    ):
                        continue  # never overwrite on a targeted retry
                    ans["answer"] = answers[qid]
            sol["completed"] = sum(
                1
                for sec in sol.get("sections", [])
                for ans in sec.get("answers", [])
                if ans.get("answer")
            )
            sol["status"] = "generating"
            await db.papers.update_one(
                {"id": paper_id, "sections": (current or {}).get("sections", []), "solution": (current or {}).get("solution")}, {"$set": {"solution": sol}}
            )

        if payload:
            context_excerpt, feedback_hints = await _solution_context(p, user_id)
            subject = p.get("subject", "")
            klass = p.get("class_name", "")

            await _run_solution_batches(
                subject, klass, payload, context_excerpt, feedback_hints,
                BATCH_SIZE, answers, on_batch=_persist,
            )

            # One automatic sweep at a smaller batch size before giving up. Most
            # truncation failures clear here, so the user never has to click.
            blank = _blank_of(payload, answers)
            if blank:
                logger.info(
                    f"Solution sweep for {paper_id}: retrying "
                    f"{len(blank)} unanswered question(s)"
                )
                await _run_solution_batches(
                    subject, klass, blank, context_excerpt, feedback_hints,
                    SWEEP_BATCH_SIZE, answers, label="sweep", on_batch=_persist,
                )

            # Whatever the deterministic pass in _run_solution_batches could not
            # fix goes to the repair model here — one batched call covering the
            # whole answer key, before the status flips off "generating".
            latex_stats = await repair_answers(answers, ref=paper_id)
            await _persist(force=True)

        # Mark complete (or partial)
        current = await db.papers.find_one(
            {"id": paper_id}, {"_id": 0, "solution": 1, "sections": 1}
        )
        sol = copy.deepcopy((current or {}).get("solution") or {})
        completed = sol.get("completed", 0)
        total = sol.get("total", 0)
        sol["status"] = "ready" if total and completed == total else "partial"
        sol["finished_at"] = utcnow_iso()
        if payload:
            sol["latex_check"] = latex_stats
        sol.pop("error", None)
        await db.papers.update_one(
            {"id": paper_id, "sections": (current or {}).get("sections", []), "solution": (current or {}).get("solution")}, {"$set": {"solution": sol}}
        )
        logger.info(
            f"Solution background job finished for {paper_id}: {completed}/{total}"
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Solution background job crashed: {e}")
        await db.papers.update_one(
            {"id": paper_id},
            {"$set": {"solution.status": "failed", "solution.error": str(e)[:300]}},
        )
