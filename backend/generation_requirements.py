"""Compile form values and explicit overrides once, without an LLM round trip."""
from collections import Counter
from copy import deepcopy
import math


def _marks_total(questions):
    try:
        values = [float(q.get("marks") or 0) for q in questions]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 or not value.is_integer() for value in values):
        return None
    return sum(values)


def apportion(values: dict, total: int) -> dict:
    """Stable largest-remainder counts; zero percentages always remain zero."""
    values = {key: max(0, float(value)) for key, value in values.items()}
    denominator = sum(values.values())
    if not denominator:
        return {}
    exact = {key: total * value / denominator for key, value in values.items()}
    counts = {key: int(value) for key, value in exact.items()}
    order = sorted(values, key=lambda key: -(exact[key] - counts[key]))
    for key in order[:total - sum(counts.values())]:
        counts[key] += 1
    return counts


def compile_requirements(*, total_marks, distribution, format_distribution,
                         topics, difficulty, total_questions=None, format_counts=None,
                         format_topic_allocations=None,
                         diagram_count=None, instruction_spec=None, blueprint_spec=None,
                         has_blueprint=False):
    """Additional instructions override only the fields they actually specify.

    A blueprint owns structure, so percentages must not compete with it.
    Plain UI settings are compiled into the same hard counts as parsed prose.
    """
    spec = deepcopy(blueprint_spec or {}) if has_blueprint else {}
    for key, value in (instruction_spec or {}).items():
        if value or (key == "diagram_count" and value == 0):
            if isinstance(value, dict) and isinstance(spec.get(key), dict):
                spec[key] = {**spec[key], **value}
            else:
                spec[key] = deepcopy(value)
    # Format breakdowns replace one another as a unit, never add old quotas.
    explicit_formats = (instruction_spec or {}).get("format_counts") or (
        (blueprint_spec or {}).get("format_counts") if has_blueprint else format_counts
    )
    if (instruction_spec or {}).get("total_questions") and not (instruction_spec or {}).get("format_counts") and explicit_formats:
        explicit_formats = apportion(explicit_formats, instruction_spec["total_questions"])
    if explicit_formats:
        spec["format_counts"] = dict(explicit_formats)
        spec["total_questions"] = sum(explicit_formats.values())
    # Whether `total` is a number the teacher actually stated -- the form's
    # question count, explicit format counts, or a count parsed out of the
    # instructions -- or the marks-derived guess below.
    #
    # The distinction matters because the guess must never overwrite the target
    # stored when the paper was created. It did, and that turned a complete
    # 1-question paper into one that looked 4 short: the auto top-up then fired
    # to "fix" a paper that was already right, and a top-up is a separate call
    # that can only see part of what it must avoid -- one of the places
    # duplicates get in.
    spec["total_questions_stated"] = bool(
        spec.get("total_questions") or total_questions
    )
    total = spec.get("total_questions") or (
        0 if has_blueprint else total_questions or max(5, min(25, total_marks // 3))
    )
    spec["total_questions"] = total
    if not has_blueprint:
        if not explicit_formats:
            spec["format_counts"] = apportion(format_distribution or {}, total)
        spec["type_counts"] = apportion(distribution or {}, total)
        spec["total_marks"] = total_marks
        if format_topic_allocations is not None:
            spec["format_topic_allocations"] = deepcopy(format_topic_allocations)
    if "diagram_count" not in spec and diagram_count is not None:
        spec["diagram_count"] = diagram_count
    levels = {t["name"]: t.get("difficulty") or difficulty for t in topics}
    if spec.get("difficulty"):
        levels = {name: spec["difficulty"] for name in levels}
    for name, level in (spec.get("topic_difficulty") or {}).items():
        for canonical in levels:
            if canonical.casefold() == name.casefold() or canonical.casefold().endswith(" > " + name.casefold()):
                levels[canonical] = level
    spec["topic_difficulty"] = levels
    spec["compiled"] = True
    return spec


def remaining_requirements(spec, sections, count):
    """Only ask a top-up for missing counts; never reapply full-paper quotas."""
    remaining = deepcopy(spec)
    questions = [q for s in sections for q in s.get("questions", [])]
    remaining["total_questions"] = count
    remaining.pop("total_marks", None)
    if spec.get("total_marks") and spec.get("total_questions", 0) - len(questions) == count:
        existing_marks = _marks_total(questions)
        marks_left = spec["total_marks"] - existing_marks if existing_marks is not None else 0
        if marks_left >= count:
            remaining["total_marks"] = marks_left
    for key, field in (("format_counts", "format"), ("type_counts", "type"), ("topic_counts", "topic")):
        actual = Counter(q.get(field, "") for q in questions)
        gaps = {name: max(0, n - actual[name]) for name, n in spec.get(key, {}).items()}
        # The quota is meaningful only when the remaining slots can satisfy it.
        remaining[key] = apportion(gaps, count) if sum(gaps.values()) >= count else {}
    if spec.get("diagram_count") is not None:
        remaining["diagram_count"] = min(count, max(0, spec["diagram_count"] -
            sum(bool(q.get("needs_diagram")) for q in questions)))
    offset = len(questions)
    remaining["question_overrides"] = [
        {**override, "n": override["n"] - offset}
        for override in spec.get("question_overrides", [])
        if offset < override["n"] <= offset + count
    ]
    if spec.get("section_allocations"):
        from section_blueprints import remaining_allocations
        remaining = remaining_allocations(remaining, sections, count)
    return remaining


def check_ui_requirements(sections, spec):
    """Check observable requirements; this does not judge pedagogical quality."""
    questions = [q for s in sections or [] for q in s.get("questions", [])]
    from section_blueprints import allocation_misses
    misses = allocation_misses(sections, spec)
    actual_types = Counter(q.get("type", "") for q in questions)
    actual_topics = Counter(q.get("topic", "") for q in questions)
    actual_format_topics = Counter(
        (q.get("format", ""), q.get("textbook_id", ""), q.get("topic_id", ""))
        for q in questions
    )
    expected_format_topics = Counter()
    for allocation in spec.get("format_topic_allocations") or []:
        identity = (allocation.get("format", ""), allocation.get("textbook_id", ""), allocation.get("topic_id", ""))
        expected_format_topics[identity] += int(allocation.get("count") or 0)
    names = {
        (allocation.get("format", ""), allocation.get("textbook_id", ""), allocation.get("topic_id", "")): allocation.get("name", "")
        for allocation in spec.get("format_topic_allocations") or []
    }
    for identity, expected in expected_format_topics.items():
        if actual_format_topics[identity] != expected:
            misses.append(f"asked for {expected} {identity[0]} questions on {names[identity]}, got {actual_format_topics[identity]}")
    for topic, count in spec.get("topic_counts", {}).items():
        if int(count or 0) <= 0:
            continue
        if actual_topics[topic] != count:
            misses.append(f"asked for {count} questions on {topic}, got {actual_topics[topic]}")
    for kind, count in spec.get("type_counts", {}).items():
        if actual_types[kind] != count:
            misses.append(f"asked for {count} {kind} questions, got {actual_types[kind]}")
    if spec.get("total_marks"):
        marks = _marks_total(questions)
        if marks is None:
            misses.append("every question must have positive integer marks")
        elif marks != spec["total_marks"]:
            misses.append(f"asked for {spec['total_marks']} total marks, got {marks:g}")
    if spec.get("diagram_count") is not None:
        diagrams = sum(bool(q.get("needs_diagram")) for q in questions)
        if diagrams != spec["diagram_count"]:
            misses.append(f"asked for {spec['diagram_count']} diagrams, got {diagrams}")
    return misses
