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
