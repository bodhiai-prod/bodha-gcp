"""Presentation status for paper-library rows.

The paper document deliberately keeps its individual job records separate.
This module turns those persisted records into one concise state for list views
without changing the stored schema.
"""


def _positive_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _status(key, label, active=False, progress=None):
    result = {"key": key, "label": label, "active": active}
    if progress is not None:
        result["progress"] = progress
    return result


def paper_work_is_active(paper):
    """Whether a paper is still changing and therefore unsafe to export."""
    return (
        paper.get("generation_status") in {"queued", "pending", "generating", "reviewing"}
        or (paper.get("completion") or {}).get("status") in {"queued", "running"}
        or (paper.get("quality_review") or {}).get("status") in {"queued", "running"}
        or (paper.get("topup") or {}).get("status") == "generating"
        or (paper.get("regen") or {}).get("status") == "generating"
    )


def export_warning_is_ignored(paper):
    """Return true only for a usable, acknowledged, stable paper snapshot."""
    acknowledgement = paper.get("export_warning_acknowledgement") or {}
    has_questions = any(
        section.get("questions") for section in paper.get("sections") or []
    )
    return bool(acknowledgement.get("ignored") and has_questions and not paper_work_is_active(paper))


def library_status_for_paper(paper, needs_review=False):
    """Return the single lifecycle state to show in a paper-library row.

    Initial generation is always authoritative.  After that finishes, active
    recovery or question edits take precedence over validation warnings, so a
    teacher can see work is still underway rather than a stale review signal.
    """
    completion = paper.get("completion") or {}
    if completion:
        state = completion.get("status")
        progress = {"completed": completion.get("valid_scored_parts", 0), "total": completion.get("target_scored_parts", 0)}
        if state in {"running", "queued"}:
            return _status("generating", "Completing questions", active=True, progress=progress)
    if export_warning_is_ignored(paper):
        return _status("ready", "Ready")
    if completion:
        state = completion.get("status")
        progress = {"completed": completion.get("valid_scored_parts", 0), "total": completion.get("target_scored_parts", 0)}
        if state == "incomplete":
            if any(section.get("questions") for section in paper.get("sections") or []):
                return _status("needs_review", "Questions pending", progress=progress)
            return _status("generation_failed", "Generation could not complete", progress=progress)
        if state == "cancelled":
            return _status("generation_cancelled", "Generation cancelled")
        if completion.get("ready"):
            return _status("ready", "Ready")
    generation_status = paper.get("generation_status") or "ready"
    if generation_status in {"queued", "pending", "generating", "reviewing"}:
        progress_data = paper.get("generation_progress") or {}
        completed = _positive_int(progress_data.get("completed"))
        total = _positive_int(
            progress_data.get("total")
            or paper.get("target_question_count")
            or paper.get("total_questions")
        )
        progress = {"completed": completed, "total": total} if total else None
        label = "Checking final quality" if generation_status == "reviewing" else "Generating"
        return _status("generating", label, active=True, progress=progress)
    if generation_status == "needs_review":
        return _status("needs_review", "Needs review")
    if generation_status == "failed":
        return _status("generation_failed", "Generation failed")
    if generation_status == "cancelled":
        return _status("generation_cancelled", "Generation cancelled")

    recovery = paper.get("generation_recovery") or {}
    recovery_status = recovery.get("status")
    recovery_reason = recovery.get("reason")
    if recovery_reason in {"allocation_metadata", "internal_choices"} and recovery_status in {"queued", "recovering"}:
        return _status("updating", "Updating", active=True)

    for job_name in ("topup", "regen"):
        job = paper.get(job_name) or {}
        if job.get("status") == "generating":
            return _status("updating", "Updating", active=True)

    if any((paper.get(job_name) or {}).get("status") == "failed" for job_name in ("topup", "regen")):
        return _status("update_failed", "Update failed")

    # This must be supplied from a fresh validation of the paper's current
    # sections. `instruction_check` is intentionally not consulted here: it
    # is a historical audit snapshot and can be stale after recovery or edits.
    if needs_review:
        return _status("needs_review", "Needs review")

    return _status("ready", "Ready")


def presentation_for_paper(paper):
    """Optional stable vocabulary shared by detail and library consumers."""
    completion = paper.get('completion') or {}
    progress = paper.get('generation_progress') or {}
    phase = completion.get('phase') or progress.get('phase') or 'preparing'
    ready = export_warning_is_ignored(paper) or (bool(completion.get('ready') and completion.get('export_eligible')) if completion else library_status_for_paper(paper)['key'] == 'ready')
    stage = ('Ready' if ready else 'Checking and completing' if phase in
             ('validating', 'finalizing', 'repairing', 'alternatives', 'figures', 'incomplete', 'edited')
             or (paper.get('quality_review') or {}).get('status') in ('running', 'needs_review')
             else 'Drafting' if phase == 'generating' else 'Preparing')
    if not ready and 'presentation_stage' in completion:
        stage = ('Preparing', 'Drafting', 'Checking and completing')[min(2, completion['presentation_stage'])]
    ambiguous = completion.get('recovery_reason') == 'ambiguous_placement'
    return {'stage': stage, 'ready': ready,
            'drafted_parts': completion.get('actual_scored_parts', progress.get('completed', 0)),
            'target_parts': completion.get('target_scored_parts', progress.get('total', 0)),
            'printed_questions': completion.get('actual_printed_questions'),
            'target_printed_questions': completion.get('target_printed_questions'),
            'pending_alternatives': completion.get('pending_alternatives', 0),
            'attemptable_marks': (paper.get('generation_requirements') or {}).get('attemptable_marks', paper.get('total_marks')),
            'printed_marks': (paper.get('generation_requirements') or {}).get('total_marks', paper.get('total_marks')),
            'action': 'review_placement' if ambiguous else 'complete',
            'action_label': 'Review question placement' if ambiguous else 'Finish remaining items',
            'message': ('Review the saved questions and assign their intended positions.' if ambiguous else
                        'Accepted questions remain available. Complete the remaining items to unlock downloads.' if not ready else 'Your paper is ready.')}
