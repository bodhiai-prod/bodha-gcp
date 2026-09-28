"""Background workers — long-running LLM jobs that run off the request loop
to bypass the 60s ingress proxy timeout.

These functions are imported and kicked off via `asyncio.create_task(...)` from
the API endpoints in `server.py`.
"""
from __future__ import annotations

import asyncio as _asyncio
import copy
import os
import re
import uuid
from typing import Optional

from fastapi import HTTPException

from deps import db, logger, utcnow_iso
from activity import record_activity
from diagram_policy import apply_diagram_policy, normalise_spec
from latex_repair import repair_answers, repair_sections, repair_text
from llm_adapter import chat_complete, generate_diagram, parse_json_response
from prompts import (
    derive_target_question_count,
    regen_question_prompt,
    resolve_shares,
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
    hydrate_section_instructions,
    normalize_template_sections,
    printable_template_instructions,
)
from generation_requirements import compile_requirements, remaining_requirements
from section_blueprints import allocation_coverage
from question_dedupe import dedupe_sections, is_duplicate
from question_repair import complete_sections
from storage import APP_NAME, put_object
from textbook_content import load_textbook_chunks
from topic_balance import measure_coverage, partition_topics, topic_shortfall
from token_usage import tag


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
    apply_diagram_policy(sections, ref=paper_id)

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
            path = f"{APP_NAME}/diagrams/{owner_id}/{paper_id}/{q['id']}.png"
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


# ---------------------------------------------------------------------------
# Question paper generation
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Batched generation — large papers
# ---------------------------------------------------------------------------
# The school path made ONE call for the whole paper. That was fine while a
# paper was at most 25 questions, which is where derive_target_question_count
# capped it. Once an explicit total_questions (up to 200) could be requested,
# the same single call had to fit a paper several times that size into
# BEDROCK_MAX_TOKENS -- so it truncated, parse_json_response salvaged whatever
# was complete, the paper saved short, and the auto top-up refilled it from a
# separate call that could only ever see part of what it had to avoid. Every
# step of that chain is somewhere a duplicate gets in.
#
# Batching removes the truncation, and -- because each batch gets its own slice
# of the topic list plus the stems every earlier batch wrote -- removes the
# reason two calls would write the same question in the first place.
SCHOOL_BATCH_THRESHOLD = int(os.environ.get("SCHOOL_BATCH_THRESHOLD", "25"))
SCHOOL_BATCH_SIZE = int(os.environ.get("SCHOOL_BATCH_SIZE", "20"))
# Batches inside one wave run together and cannot see each other; only their
# disjoint topic slices keep them apart. Waves are what carry the avoid-list
# forward, so this is a duplication/throughput trade, not just a rate limit.
SCHOOL_BATCH_CONCURRENCY = int(os.environ.get("SCHOOL_BATCH_CONCURRENCY", "3"))


def _batch_sizes(total: int, batch_size: int) -> list:
    """Split `total` questions into near-equal batches of at most `batch_size`."""
    num = max(1, (total + batch_size - 1) // max(1, batch_size))
    sizes = [total // num] * num
    for i in range(total - sum(sizes)):
        sizes[i] += 1
    return [n for n in sizes if n > 0]


def _merge_batch_sections(merged: dict, order: list, sections: list) -> None:
    """Fold one batch's sections into the accumulator, keyed by section title.

    Merging by title rather than concatenating is what lets a batched paper
    keep the multi-section structure a single call would have produced -- two
    batches that both wrote a "Section A" contribute to one heading, not two.
    """
    for sec in sections or []:
        title = str(sec.get("title") or "").strip() or "Section A"
        if title not in merged:
            merged[title] = {"title": title, "questions": []}
            order.append(title)
        merged[title]["questions"].extend(
            q for q in (sec.get("questions") or []) if isinstance(q, dict)
        )


async def _generate_paper_in_batches(
    *,
    ref: str,
    target_q: int,
    topics_weighted: list,
    spec: dict,
    total_marks: int,
    system_message: str,
    prompt_kwargs: dict,
) -> dict:
    """Generate a large paper as several smaller calls.

    Returns the same {"instructions", "sections"} shape one call returns, so
    everything downstream -- completion, dedupe, LaTeX repair, the diagram
    policy -- is unchanged and does not know the difference.

    A failed batch costs its own questions and nothing else; the paper comes
    back short and the existing top-up path fills it, which is strictly better
    than losing the whole paper to one bad response.
    """
    sizes = _batch_sizes(target_q, SCHOOL_BATCH_SIZE)
    names = [
        str(t.get("name", "")).strip()
        for t in topics_weighted or []
        if str(t.get("name", "")).strip()
    ]
    weight_of = {
        str(t.get("name", "")).strip(): t.get("weight", 5)
        for t in topics_weighted or []
    }
    topic_sets = partition_topics(names, sizes)

    merged: dict = {}
    order: list = []
    instructions = ""
    failures = 0

    for start in range(0, len(sizes), SCHOOL_BATCH_CONCURRENCY):
        wave = list(range(start, min(start + SCHOOL_BATCH_CONCURRENCY, len(sizes))))
        # Snapshot once per wave: every batch in it sees everything the waves
        # before wrote. Batches inside a wave still cannot see each other --
        # that is exactly what their disjoint topic slices are for.
        sections_so_far = [merged[t] for t in order]
        written = [
            q.get("question", "")
            for s in sections_so_far
            for q in s["questions"]
            if q.get("question")
        ]

        async def _run(idx: int, _seen=written, _so_far=sections_so_far) -> dict:
            batch_topics = [
                {"name": n, "weight": weight_of.get(n, 5)} for n in topic_sets[idx]
            ] or list(topics_weighted or [])
            # Reuses the top-up apportioner: it already knows how to take a
            # compiled spec plus what exists and produce the quotas for the
            # next `n` questions only, which is precisely what a batch is.
            batch_spec = remaining_requirements(spec, _so_far, sizes[idx])
            kwargs = dict(prompt_kwargs)
            kwargs.update(
                topics_weighted=batch_topics,
                total_marks=max(1, round(total_marks * sizes[idx] / max(1, target_q))),
                total_questions=sizes[idx],
                existing_questions=_seen,
                spec=batch_spec,
            )
            raw = await chat_complete(
                system_message=system_message, user_text=qgen_prompt(**kwargs)
            )
            return parse_json_response(raw)

        results = await _asyncio.gather(
            *[_run(i) for i in wave], return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                failures += 1
                logger.warning(f"[batch] {ref or '-'} batch failed: {r}")
                continue
            if not instructions:
                instructions = (r or {}).get("instructions", "") or ""
            _merge_batch_sections(merged, order, (r or {}).get("sections"))

    produced = sum(len(merged[t]["questions"]) for t in order)
    if not produced:
        raise RuntimeError(
            "Every generation batch failed - the upstream provider is not "
            "responding right now. Try again in a few minutes."
        )
    logger.info(
        f"[batch] {ref or '-'} batches={len(sizes)} produced={produced} "
        f"target={target_q} failed={failures}"
    )
    return {"instructions": instructions, "sections": [merged[t] for t in order]}


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
    if spec.get("total_questions") and not spec.get("section_allocations"):
        spec["topic_counts"] = resolve_shares(topics_weighted, spec["total_questions"])
    await db.papers.update_one({"id": paper_id}, {"$set": {"generation_requirements": spec}})
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
    # Below the threshold this is exactly the call it always was -- one request,
    # no extra latency, no behaviour change for the papers that already work.
    #
    # A blueprint paper is never batched, section allocations or not. The
    # blueprint fixes per-section structure that batch slicing has no way to
    # honour: splitting a paper by topic would cut across the very sections the
    # allocation contract is defined in terms of.
    batched = (
        target_q > SCHOOL_BATCH_THRESHOLD
        and not (section_blueprint or "").strip()
        and not spec.get("section_allocations")
    )
    prompt = None if batched else qgen_prompt(**prompt_kwargs)
    try:
        if batched:
            data = await _generate_paper_in_batches(
                ref=paper_id,
                target_q=target_q,
                topics_weighted=topics_weighted,
                spec=spec,
                total_marks=total_marks,
                system_message=system_message,
                prompt_kwargs=prompt_kwargs,
            )
        else:
            raw = await chat_complete(
                system_message=system_message,
                user_text=prompt,
            )
            data = parse_json_response(raw)
    except Exception as e:  # noqa: BLE001
        logger.exception("Question generation failed")
        await db.papers.update_one(
            {"id": paper_id, "generation_status": "pending"},
            {"$set": {
                "generation_status": "failed",
                "generation_error": str(e)[:500],
            }},
        )
        await record_activity(owner_id, "paper.failed", {"paper_id": paper_id})
        return

    sections = data.get("sections") or []
    # Publish the only truthful question count available once the model output
    # has been parsed. A single provider response moves from 0 straight to this
    # value; the UI continues to poll it while the finishing checks run.
    await db.papers.update_one({"id": paper_id, "generation_status": "pending"}, {"$set": {
        "generation_progress.completed": count_questions(sections),
        "generation_progress.total": target_q,
    }})
    coercions: dict = {}
    _normalise_paper_questions(sections, difficulty, coercions)
    (allocation_coverage(sections, spec) if spec.get("section_allocations") else measure_coverage(sections, topics_weighted, spec.get("total_questions") or derive_target_question_count(total_marks)))
    # One bounded correction attempt for a complete but noncompliant paper.
    # Truncated papers use the existing top-up path instead.
    initial_check = verify_instructions(sections, spec)
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
        prompt is not None
        and spec.get("total_questions")
        and not initial_check["honoured"]
        and count_questions(sections) >= spec["total_questions"]
    ):
        try:
            corrected_raw = await chat_complete(
                system_message="Create original exam questions in strict JSON. Satisfy the resolved requirements exactly.",
                user_text=prompt + "\nCORRECTION REQUIRED: A previous attempt failed these checks:\n" +
                    "\n".join(initial_check["misses"]) +
                    "\nGenerate the complete paper again, correcting these mismatches. Return only JSON.",
            )
            corrected_data = parse_json_response(corrected_raw)
            corrected = corrected_data.get("sections") or []
            corrected_coercions: dict = {}
            _normalise_paper_questions(corrected, difficulty, corrected_coercions)
            (allocation_coverage(corrected, spec) if spec.get("section_allocations") else measure_coverage(corrected, topics_weighted, spec.get("total_questions") or derive_target_question_count(total_marks)))
            corrected_check = verify_instructions(corrected, spec)
            corrected_check["misses"].extend(
                "generated an invalid cognitive type"
                for flags in corrected_coercions.values() if "invalid_question_type" in flags
            )
            if corrected and len(corrected_check["misses"]) < len(initial_check["misses"]):
                data, sections = corrected_data, corrected
                coercions = corrected_coercions
        except Exception as exc:
            logger.warning(f"Requirement correction skipped for {paper_id}: {exc}")

    # Capture the first valid set of headings as this paper's durable section
    # plan. From here on recovered and top-up questions are routed by ID, not
    # by whatever display title the provider happens to emit.
    sections, section_plan, _ = reconcile_section_plan(sections, None, spec)
    hydrate_section_instructions({"generation_requirements": spec, "sections": sections})

    # Finish the questions that arrived structurally incomplete: an MCQ with
    # two options, a stem cut mid-sentence, an answer key pointing at
    # nothing. Runs ahead of repair_sections so options written here get the
    # same LaTeX gate as the ones the first call produced.
    completeness_stats = await complete_sections(
        sections,
        ref=paper_id,
        context={"subject": subject, "klass": klass, "difficulty": difficulty},
        flags=coercions,
    )

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
    coverage = (
        allocation_coverage(sections, spec)
        if spec.get("section_allocations")
        else measure_coverage(sections, topics_weighted, coverage_target)
    )

    # Check and repair the LaTeX before anything downstream reads it. Runs
    # ahead of apply_diagram_policy so diagram_policy.plain_text() flattens
    # already-clean markup, and ahead of the $set below so generation_status
    # only reaches "ready" once the questions actually render.
    latex_stats = await repair_sections(sections, ref=paper_id)

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
    still_pending = await db.papers.find_one({"id": paper_id, "generation_status": "pending"}, {"_id": 0, "id": 1})
    if not still_pending:
        logger.info("Paper generation cancelled before publish: %s", paper_id)
        return
    final_check = verify_instructions(sections, spec)
    allocation_issues = template_validation_misses(sections, spec) if spec.get("section_allocations") else []
    final_check["misses"].extend(allocation_issues)
    if not spec.get("section_allocations"):
        actual_marks = sum(float(q.get("marks") or 0) for s in sections for q in (s.get("questions") or []))
        if actual_marks != float(total_marks):
            final_check["misses"].append(f"asked for {total_marks} total marks, got {actual_marks:g}")
    final_check["honoured"] = not final_check["misses"]
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
    validation_error = "; ".join([*mark_misses, *( ["One or more questions had an invalid cognitive type."] if final_type_misses else [])])[:500] or None
    await db.papers.update_one(
        {"id": paper_id, "generation_status": "pending"},
        {"$set": {
            "instructions": printable_instructions(
                data.get("instructions", ""),
                template_instructions=printable_template_instructions({"generation_requirements": spec}),
                custom_instructions=custom_instructions,
            ),
            "sections": sections,
            "section_plan": section_plan,
            "question_count": count_questions(sections),
            "diagrams_pending": pending_count,
            "generation_status": "ready",
            "generation_error": None,
            "generation_recovery": (
                {"status": "queued", "reason": "allocation_metadata", "attempt": 0,
                 "max_attempts": TEMPLATE_RECOVERY_MAX_ATTEMPTS, "started_at": None,
                 "finished_at": None, "validation_summary": compact_validation_misses(allocation_issues)}
                if structural_recovery else (
                    {"status": "queued", "reason": "validation", "attempt": 0,
                     "max_attempts": 0, "validation_summary": [validation_error]}
                    if validation_error else None
                )
            ),
            "generation_progress.completed": count_questions(sections),
            "generation_progress.total": target_q,
            "generation_progress.status": "ready",
            "recovered_from_truncation": bool(data.get("_recovered_from_truncation")),
            "latex_check": latex_stats,
            "completeness_check": completeness_stats,
            "dedupe_check": dedupe_stats,
            "topic_coverage": coverage,
            "instruction_check": final_check,
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
    await record_activity(owner_id, "paper.ready", {"paper_id": paper_id, "questions": count_questions(sections)})

    if structural_recovery:
        _spawn_recovery(recover_template_generation_background(
            paper_id, owner_id, context_excerpt=context_excerpt, feedback_hints=feedback_hints
        ))
        return

    # Kick off diagram generation asynchronously.
    if pending_count > 0:
        await generate_diagrams_background(paper_id, owner_id)

    # Last: if the paper is still short of what it was meant to be, top it
    # up. Deliberately after the $set above, so the teacher already has the
    # partial paper on screen while this runs.
    await _auto_topup_if_short(
        paper_id, owner_id, context_excerpt, feedback_hints
    )


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
    stored = paper.get("target_question_count")
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
    blueprint = spec.get("blueprint") or {}
    expected_marks = blueprint.get("total_marks")
    if expected_marks is not None:
        actual_marks = attemptable_marks(sections)
        if actual_marks != expected_marks:
            misses.append(f"asked for {expected_marks} attemptable marks, got {actual_marks}")
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
            raw = await chat_complete(
                system_message="Create original exam questions in strict JSON. Return every required blueprint identity exactly.",
                user_text=prompt + "\nRECOVERY: This is a complete replacement candidate. Every section and question identity is mandatory. Return only JSON.",
            )
            candidate = parse_json_response(raw)
            sections = candidate.get("sections") or []
            coercions: dict = {}
            _normalise_paper_questions(sections, paper.get("difficulty", "medium"), coercions)
            sections, section_plan, _ = reconcile_section_plan(sections, None, spec)
            await complete_sections(sections, ref=f"{paper_id}:recovery", context={
                "subject": paper.get("subject", ""), "klass": paper.get("class_name", ""),
                "difficulty": paper.get("difficulty", "medium"),
            }, flags=coercions)
            await repair_sections(sections, ref=f"{paper_id}:recovery")
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
        raw = await chat_complete(
            system_message=(
                "You create original, high-quality exam questions in strict JSON. "
                "Never copy textbook content."
            ),
            user_text=prompt,
        )
        data = parse_json_response(raw)

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
PRESERVED_SLOT_FIELDS = (
    "id", "topic", "type", "format", "difficulty", "marks", "important",
    "allocation_id", "textbook_id", "topic_id", "group_id",
    "internal_choice_count",
)
# What the model IS asked for -- the stem and the things that follow from it.
_CANDIDATE_FIELDS = (
    "question", "options", "correct_option", "needs_diagram", "diagram_spec",
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
    old_text = old.get("question") or ""
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
        raw = await chat_complete(system_message=REGEN_SYSTEM, user_text=prompt)
        fresh = _extract_candidate(parse_json_response(raw))

        # Build the replacement from the slot outward: the slot's values first
        # (so the normaliser's MCQ branch sees the right format, and so the id,
        # marks, topic and allocation row are the old question's), then only the
        # fields the model was actually asked for. Anything else it sent --
        # a topic it preferred, marks it thought better of -- is dropped here
        # rather than corrected later.
        cand = {**slot, **{k: fresh[k] for k in _CANDIDATE_FIELDS if k in fresh}}

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
    # Dropping diagram_path (above) is what makes the picture update at all:
    # generate_diagrams_for_paper skips any question that already has one, and
    # on the front end QuestionDiagram only refetches because losing the path
    # unmounts the <img>. The S3 key is the question id, so the new render
    # overwrites the old file rather than orphaning it.

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

        update = {
            "sections": result["sections"],
            "regen": {
                "status": "ready",
                "question_id": question_id,
                "finished_at": utcnow_iso(),
            },
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
    for sec in (existing or {}).get("sections") or []:
        for a in sec.get("answers") or []:
            qid = a.get("question_id")
            if qid:
                prev[qid] = a.get("answer") or ""

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
            text = prev.get(qid, "")
            answers.append({"question_id": qid, "answer": text})
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
                {"id": paper_id}, {"_id": 0, "solution": 1}
            )
            sol = (current or {}).get("solution") or {}
            for sec in sol.get("sections", []):
                for ans in sec.get("answers", []):
                    qid = ans.get("question_id")
                    if qid not in answers:
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
                {"id": paper_id}, {"$set": {"solution": sol}}
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
            {"id": paper_id}, {"_id": 0, "solution": 1}
        )
        sol = (current or {}).get("solution") or {}
        completed = sol.get("completed", 0)
        total = sol.get("total", 0)
        sol["status"] = "ready" if total and completed == total else "partial"
        sol["finished_at"] = utcnow_iso()
        if payload:
            sol["latex_check"] = latex_stats
        sol.pop("error", None)
        await db.papers.update_one(
            {"id": paper_id}, {"$set": {"solution": sol}}
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
