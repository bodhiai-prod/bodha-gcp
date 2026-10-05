"""Parent-aware integrity helpers for template case studies.

The database stores a case passage on one scored child for backward
compatibility.  This module makes the *assembled parent* the unit used for
validation, review, repair context and change detection.
"""
from __future__ import annotations

import hashlib
import json


def _questions(sections):
    return [q for section in sections or [] for q in section.get("questions") or [] if isinstance(q, dict)]


def _case_slots(plan):
    return [slot for slot in (plan or {}).get("slots") or [] if slot.get("case_study") and slot.get("parent_id")]


def case_groups(sections, plan):
    """Resolve ordered, non-mutating case bundles from server-owned slots."""
    by_slot = {q.get("slot_id"): q for q in _questions(sections)}
    groups = {}
    for slot in _case_slots(plan):
        key = (slot.get("section_id"), slot.get("parent_id"))
        groups.setdefault(key, {"section_id": key[0], "parent_id": key[1], "slots": [], "questions": []})
        groups[key]["slots"].append(slot)
        if by_slot.get(slot.get("slot_id")):
            groups[key]["questions"].append(by_slot[slot["slot_id"]])
    result = []
    for group in groups.values():
        stimuli = [str(q.get("shared_stimulus") or "").strip() for q in group["questions"] if str(q.get("shared_stimulus") or "").strip()]
        group["shared_stimulus"] = stimuli[0] if len(stimuli) == 1 else ""
        group["stimuli"] = stimuli
        result.append(group)
    return result


def case_fingerprint(group):
    payload = {
        "section_id": group.get("section_id"), "parent_id": group.get("parent_id"),
        "shared_stimulus": group.get("shared_stimulus", ""),
        "questions": [{key: q.get(key) for key in ("slot_id", "question", "options", "correct_option", "diagram_spec", "diagram_path")}
                      for q in group.get("questions") or []],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def case_issues(sections, plan):
    issues = []
    for group in case_groups(sections, plan):
        expected = [slot.get("slot_id") for slot in group["slots"]]
        actual = [q.get("slot_id") for q in group["questions"]]
        if actual != expected:
            issues.append({"code": "case_children", "section_id": group["section_id"], "parent_id": group["parent_id"],
                           "slot_ids": expected, "message": "A case-study parent is missing or has misplaced subparts."})
        if len(group["stimuli"]) != 1:
            issues.append({"code": "case_stimulus", "section_id": group["section_id"], "parent_id": group["parent_id"],
                           "slot_ids": expected, "message": "A case-study parent requires exactly one shared stimulus."})
    return issues


def repair_slots(requested_slots, sections, plan):
    """Expand a repair to the whole parent only when shared context is absent."""
    requested = list(requested_slots or [])
    invalid = {(i["section_id"], i["parent_id"]) for i in case_issues(sections, plan)}
    out, seen = [], set()
    for slot in requested:
        key = (slot.get("section_id"), slot.get("parent_id"))
        choices = [s for s in _case_slots(plan) if (s.get("section_id"), s.get("parent_id")) == key] if key in invalid else [slot]
        for choice in choices:
            if choice.get("slot_id") not in seen:
                out.append(choice); seen.add(choice.get("slot_id"))
    return out


def parent_context(sections, plan, slots=None):
    wanted = {(s.get("section_id"), s.get("parent_id")) for s in (slots or []) if s.get("case_study")}
    return [{"section_id": g["section_id"], "parent_id": g["parent_id"], "shared_stimulus": g["shared_stimulus"],
             "siblings": [{"slot_id": q.get("slot_id"), "question": q.get("question"), "options": q.get("options")} for q in g["questions"]]}
            for g in case_groups(sections, plan) if not wanted or (g["section_id"], g["parent_id"]) in wanted]


async def review_cases(sections, plan, request, *, ref):
    groups = case_groups(sections, plan)
    if not groups:
        return {}
    data = await request(
        system_message="Review complete exam case studies. Return strict JSON.",
        user_text=("For every case, determine whether all subparts coherently use the shared stimulus and have enough information. "
                   "Return {\"cases\":[{\"section_id\":...,\"parent_id\":...,\"valid\":true|false,\"checked_slot_ids\":[...],\"reason\":\"...\"}]}.\n"
                   + json.dumps(groups, ensure_ascii=False)), ref=ref, attempts=1)
    answers = {(r.get("section_id"), r.get("parent_id")): r for r in data.get("cases") or [] if isinstance(r, dict)}
    return {(g["section_id"], g["parent_id"]): {"fingerprint": case_fingerprint(g), "review": answers.get((g["section_id"], g["parent_id"]), {})} for g in groups}


def review_misses(sections, plan, cache):
    misses = []
    for group in case_groups(sections, plan):
        key = (group["section_id"], group["parent_id"]); cached = (cache or {}).get(key, {})
        review = cached.get("review") or {}
        expected = {s.get("slot_id") for s in group["slots"]}
        if (cached.get("fingerprint") != case_fingerprint(group) or review.get("valid") is not True
                or set(review.get("checked_slot_ids") or []) != expected):
            misses.append({"code": "case_semantic", "section_id": key[0], "parent_id": key[1], "slot_ids": list(expected),
                           "message": review.get("reason") or "Case study needs a complete coherence review."})
    return misses
