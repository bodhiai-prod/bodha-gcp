"""Structured board contracts, allocation validation and observable summaries.

No database or model dependencies: the preview and worker use the same rules.
Counts refer to numbered question blocks; subparts and OR alternatives stay in
their parent question and do not add to the attemptable marks.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from pydantic import BaseModel, Field, ConfigDict
from typing import Literal


# Student-facing content is deliberately separate from the generation template.
# The latter contains authoring directives and must never be copied verbatim to
# a paper. These values are hydrated onto built-in catalog entries and stored in
# each paper's compiled blueprint snapshot.
_PRINTABLE_TEMPLATE_CONTENT = {
    "icse10": {
        "instructions": "Answer all questions. Internal choices, where provided, must be answered as directed.",
        "sections": {"A": "Answer all questions in this section.", "B": "Answer this section according to the choices stated."},
    },
    "cbse10": {
        "instructions": "Read the following instructions carefully. All questions are compulsory. This question paper is divided into Sections A, B, C, D and E. Use of calculator is not allowed.",
        "sections": {"A": "This section contains objective questions.", "B": "This section contains very short answer questions.", "C": "This section contains short answer questions.", "D": "This section contains long answer questions.", "E": "This section contains case-study based questions."},
    },
    "isc12": {
        "instructions": "Answer all questions as directed. Where alternative sections are provided, attempt only one complete alternative stream.",
        "sections": {"A": "This section is compulsory.", "B": "Attempt this section only if you choose this optional stream.", "C": "Attempt this section only if you choose this optional stream."},
    },
    "cbse12": {
        "instructions": "Read the following instructions very carefully and strictly follow them:\n(i) This question paper contains 38 questions. All questions are compulsory.\n(ii) This question paper is divided into five Sections – A, B, C, D and E.\n(iii) There is no overall choice. Internal choices are provided only where indicated.\n(iv) Use of calculator is not allowed.",
        "sections": {"A": "This section comprises 20 multiple choice questions (MCQs) of 1 mark each.", "B": "This section comprises 5 Very Short Answer (VSA) type questions of 2 marks each.", "C": "This section comprises 6 Short Answer (SA) type questions of 3 marks each.", "D": "This section comprises 4 Long Answer (LA) type questions of 5 marks each.", "E": "This section comprises 3 case-study based questions of 4 marks each."},
    },
}


def _official_printable_content(row: dict) -> dict:
    """Safe candidate-facing lead-ins for catalog templates added after v1."""
    if row.get("legacy") or row.get("id") == "cbse12":
        return {}
    family = str(row.get("family") or "").upper()
    instructions = (
        f"Read the following instructions carefully. This is the {family} "
        f"{row.get('subject_variant', '')} template for {row.get('academic_session', '')}. "
        "Follow the section-wise choices and marks printed below."
    )
    sections = {}
    for section in row.get("sections") or []:
        section_id = section.get("id")
        title = section.get("title") or f"Section {section_id}"
        sections[section_id] = f"{title}. Answer only the questions and choices specified for this section."
    return {"instructions": instructions, "sections": sections}


class BlueprintGroup(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = ""
    count: int = Field(strict=True, ge=1, le=200)
    marks: int = Field(strict=True, ge=1, le=200)
    format: Literal["mcq", "short_answer", "long_answer", "fill_blank", "true_false"]
    diagram_minimum: int = Field(default=0, ge=0, le=200)
    internal_choices: int | None = Field(default=None, ge=0, le=200)


class BlueprintSection(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str
    groups: list[BlueprintGroup] = Field(min_length=1, max_length=30)
    attempt_count: int | None = None
    choice_set: str | None = None


class BlueprintStructure(BaseModel):
    label: str
    total_marks: int = Field(strict=True, ge=1, le=1000)
    duration_minutes: int = Field(strict=True, ge=1, le=1440)
    sections: list[BlueprintSection] = Field(min_length=1, max_length=30)


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str
    group_id: str
    textbook_id: str
    topic_id: str
    count: int = Field(strict=True, ge=1, le=200)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    diagrams: bool = False


def catalog():
    rows = json.loads(Path(__file__).with_name("blueprint_catalog.json").read_text(encoding="utf-8"))
    rows.extend(json.loads(Path(__file__).with_name("official_blueprint_catalog.json").read_text(encoding="utf-8")))
    for row in rows:
        if row.get("id") in {"icse10", "cbse10", "isc12"}:
            row.setdefault("legacy", True)
            row.setdefault("family", row["id"])
            row.setdefault("subject_variant", "Legacy generic")
            if row["id"] == "isc12":
                row.setdefault("disjoint_section_pairs", [["B", "C"]])
        printable = _PRINTABLE_TEMPLATE_CONTENT.get(row.get("id"), {}) or _official_printable_content(row)
        row["student_instructions"] = printable.get("instructions", "")
        section_instructions = printable.get("sections", {})
        for section in row.get("sections") or []:
            section["section_instructions"] = section_instructions.get(section.get("id"), "")
    return rows


def printable_template_instructions(paper: dict) -> str:
    """Read student instructions from the compiled blueprint, not model output."""
    blueprint = (paper.get("generation_requirements") or {}).get("blueprint") or {}
    fallback = _PRINTABLE_TEMPLATE_CONTENT.get(blueprint.get("id"), {}).get("instructions", "")
    return str(blueprint.get("student_instructions") or fallback).strip()


def hydrate_section_instructions(paper: dict) -> None:
    """Attach known template section lead-ins to persisted or legacy sections."""
    blueprint = (paper.get("generation_requirements") or {}).get("blueprint") or {}
    fallback = _PRINTABLE_TEMPLATE_CONTENT.get(blueprint.get("id"), {}).get("sections", {})
    by_id = {
        str(s.get("id")): s.get("section_instructions") or fallback.get(s.get("id"), "")
        for s in blueprint.get("sections") or []
    }
    for section in paper.get("sections") or []:
        section_id = str(section.get("section_id") or section_key(section.get("title", "")))
        if by_id.get(section_id):
            section["section_instructions"] = by_id[section_id]


def blueprint_section_skeleton(spec: dict) -> list[dict]:
    """Return the only display structure a compiled template is allowed to use."""
    blueprint = (spec or {}).get("blueprint") or {}
    return [
        {
            "section_id": section["id"],
            "title": section.get("title") or f"Section {section['id']}",
            "section_instructions": section.get("section_instructions", ""),
            "choice_set": section.get("choice_set"),
            "attempt_count": section.get("attempt_count"),
            "questions": [],
        }
        for section in blueprint.get("sections") or []
        if section.get("id")
    ]


def restore_allocation_identity(question: dict, spec: dict) -> bool:
    """Fill a missing allocation id only from an exact immutable row match.

    Titles, question order and human-readable topic labels are deliberately not
    considered evidence.  They are provider presentation data and cannot
    safely decide a blueprint slot.
    """
    rows = (spec or {}).get("section_allocations") or []
    known = {row.get("allocation_id"): row for row in rows}
    if question.get("allocation_id") in known:
        return False
    identity = tuple(question.get(key) for key in ("textbook_id", "topic_id", "group_id"))
    if any(value in (None, "") for value in identity):
        return False
    matches = [
        row for row in rows
        if tuple(row.get(key) for key in ("textbook_id", "topic_id", "group_id")) == identity
    ]
    if len(matches) != 1:
        return False
    question["allocation_id"] = matches[0]["allocation_id"]
    return True


def normalize_template_sections(sections: list, spec: dict) -> tuple[list, list[str]]:
    """Put valid template questions into the server-owned blueprint skeleton.

    Questions without a provable allocation identity stay in one explicit
    review section.  This keeps them visible while ensuring they cannot alter
    a board template's titles, ordering or printable instructions.
    """
    rows = (spec or {}).get("section_allocations") or []
    skeleton = blueprint_section_skeleton(spec)
    if not rows or not skeleton:
        return sections, []
    by_allocation = {row.get("allocation_id"): row for row in rows}
    by_section = {section["section_id"]: section for section in skeleton}
    unknown, misses, index = [], [], 0
    for source in sections or []:
        for question in source.get("questions") or []:
            index += 1
            restore_allocation_identity(question, spec)
            row = by_allocation.get(question.get("allocation_id"))
            if not row:
                unknown.append(question)
                misses.append(f"Question {index}: missing or unknown section allocation identity.")
                continue
            # Allocation placement is authoritative; a model-supplied section
            # id or title cannot override it.
            question["section_id"] = row["section_id"]
            by_section[row["section_id"]]["questions"].append(question)
    normalized = [section for section in skeleton if section["questions"]]
    if unknown:
        normalized.append({
            "section_id": "unplaced-generated-questions",
            "title": "Generated Questions Requiring Review",
            "questions": unknown,
        })
    return normalized, misses


def compact_validation_misses(misses: list[str]) -> list[str]:
    """Avoid turning one broken provider contract into dozens of UI errors."""
    structural = [m for m in misses if "allocation identity" in m.lower()]
    if structural:
        other = [m for m in misses if m not in structural]
        return ["Template allocation metadata is incomplete; recovery is required.", *other]
    return list(misses)


# The provider can describe an internal choice correctly while omitting or
# mistyping its bookkeeping field.  The printed separator is the student-facing
# source of truth, so count only a standalone line (not prose such as "and/or").
_OR_SEPARATOR_RE = re.compile(
    r"(?im)^[ \t]*(?:\*{1,3}[ \t]*)?OR(?:[ \t]*\*{1,3})?[ \t]*$"
)
_BREAK_TAG_RE = re.compile(r"(?i)<\s*(?:br\s*/?|/?(?:p|div))\s*[^>]*>")


def derived_internal_choice_count(question: dict) -> int:
    """Count rendered OR separators in one numbered question block."""
    text = _BREAK_TAG_RE.sub("\n", str(question.get("question") or ""))
    return len(_OR_SEPARATOR_RE.findall(text.replace("\r\n", "\n").replace("\r", "\n")))


def normalize_internal_choice_counts(sections: list) -> bool:
    """Persist backend-derived internal-choice metadata; returns whether changed."""
    changed = False
    for section in sections or []:
        for question in section.get("questions") or []:
            count = derived_internal_choice_count(question)
            if question.get("internal_choice_count") != count:
                question["internal_choice_count"] = count
                changed = True
    return changed


def topic_id(book_id, name, parent=""):
    return hashlib.sha256(json.dumps([book_id, parent, name], ensure_ascii=False).encode()).hexdigest()[:24]


def topic_catalog(book):
    rows = []
    for topic in book.get("topics") or []:
        name = topic.get("name", "")
        if not name:
            continue
        for child, parent in [(name, "")] + [(s, name) for s in topic.get("subtopics", []) if isinstance(s, str)]:
            rows.append({"topic_id": topic_id(book["id"], child, parent), "name": child,
                         "parent_topic": parent, "textbook_id": book["id"],
                         "source_chunks": topic.get("source_chunks", []),
                         "book_label": book.get("original_filename", book["id"])})
    return rows


def section_key(value):
    match = re.search(r"section\s*[-–—−:]?\s*([a-z0-9]+)", str(value), re.I)
    return match.group(1).upper() if match else str(value).strip().upper()


def blueprint_errors(bp):
    errors, section_ids = [], set()
    if not isinstance(bp, dict) or not isinstance(bp.get("sections"), list) or not bp["sections"]:
        return ["Blueprint must define at least one section."]
    for section in bp["sections"]:
        sid = section.get("id")
        if not sid or sid in section_ids:
            errors.append("Section identifiers must be unique and nonempty.")
        section_ids.add(sid)
        groups, seen = section.get("groups") or [], set()
        if not groups:
            errors.append(f"Section {sid} has no question groups.")
        for group in groups:
            if not group.get("id") or group["id"] in seen:
                errors.append(f"Section {sid}: group identifiers must be unique.")
            seen.add(group.get("id"))
            for key in ("count", "marks"):
                if type(group.get(key)) is not int or not 1 <= group[key] <= 200:
                    errors.append(f"Section {sid}: {key} must be a positive integer, at most 200.")
            if group.get("format") not in ("mcq", "short_answer", "long_answer", "fill_blank", "true_false"):
                errors.append(f"Section {sid}: specify a supported question format.")
        attempt = section.get("attempt_count")
        if attempt is not None and (type(attempt) is not int or attempt < 1 or
                attempt > sum(g.get("count", 0) for g in groups)):
            errors.append(f"Section {sid}: invalid attempt count.")
        if attempt and len({g.get("marks") for g in groups}) != 1:
            errors.append(f"Section {sid}: optional questions must have equal marks.")
    if errors:
        return errors
    if sum(g["count"] for s in bp["sections"] for g in s["groups"]) > 200:
        errors.append("Blueprint exceeds 200 numbered questions.")
    choices = {}
    for section in bp["sections"]:
        if section.get("choice_set"):
            choices.setdefault(section["choice_set"], []).append(section_marks(section))
    if any(len(set(values)) != 1 for values in choices.values()):
        errors.append("Alternative sections must have equal attemptable marks.")
    actual = attemptable_marks(bp["sections"])
    if actual != bp.get("total_marks"):
        errors.append(f"Template offers {actual} attemptable marks but declares {bp.get('total_marks')}; correct the blueprint before allocation.")
    return errors


def section_marks(section):
    groups = section.get("groups", [])
    if section.get("attempt_count"):
        return section["attempt_count"] * groups[0]["marks"]
    return sum(g["count"] * g["marks"] for g in groups)


def attemptable_marks(sections):
    total, seen = 0, set()
    for section in sections:
        choice = section.get("choice_set")
        if choice and choice in seen:
            continue
        seen.add(choice)
        total += section_marks(section)
    return total


def compile_allocations(bp, allocations, books, difficulty, total_marks, diagram_count=None):
    errors = blueprint_errors(bp)
    if errors:
        return {}, errors
    if bp["total_marks"] != total_marks:
        errors.append(f"Paper marks must match this blueprint ({bp['total_marks']}).")
    topics = {(r["textbook_id"], r["topic_id"]): r for b in books for r in topic_catalog(b)}
    groups = {(s["id"], g["id"]): g for s in bp["sections"] for g in s["groups"]}
    counts, seen, resolved = Counter(), set(), []
    for raw in allocations:
        a = raw.model_dump() if isinstance(raw, Allocation) else dict(raw)
        pair = (a["section_id"], a["group_id"])
        key = (*pair, a["textbook_id"], a["topic_id"])
        topic = topics.get((a["textbook_id"], a["topic_id"]))
        if pair not in groups or not topic or key in seen:
            errors.append("An assignment has a stale section/topic or is duplicated. Review the section editor.")
            continue
        seen.add(key)
        counts[pair] += a["count"]
        resolved.append({**a, **topic, "difficulty": a.get("difficulty") or difficulty,
                         "marks": groups[pair]["marks"], "format": groups[pair]["format"],
                         "allocation_id": hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]})
    for pair, group in groups.items():
        if counts[pair] != group["count"]:
            errors.append(f"Section {pair[0]} / {group.get('label', pair[1])}: assigned {counts[pair]} of {group['count']} questions.")
        if group.get("diagram_minimum", 0) > sum(a["count"] for a in resolved if (a["section_id"], a["group_id"]) == pair and a["diagrams"]):
            errors.append(f"Section {pair[0]} / {pair[1]}: this template requires at least {group['diagram_minimum']} diagram question(s); enable diagrams on an allocation.")
    minimum = sum(bool(a["diagrams"]) for a in resolved)
    maximum = sum(a["count"] for a in resolved if a["diagrams"])
    if diagram_count is not None and not minimum <= diagram_count <= maximum:
        errors.append(f"Supplied diagrams must be between {minimum} and {maximum} for these assignments.")
    for first, second in bp.get("disjoint_section_pairs") or []:
        identities = lambda sid: {(a["textbook_id"], a["parent_topic"] or a["name"])
                                  for a in resolved if a["section_id"] == sid}
        if identities(first) & identities(second):
            errors.append(f"Optional Sections {first} and {second} require disjoint chapters.")
    spec = {"compiled": True, "blueprint": deepcopy(bp), "section_allocations": resolved,
            "total_questions": sum(g["count"] for g in groups.values()),
            "total_marks": sum(g["count"] * g["marks"] for g in groups.values()),
            "attemptable_marks": bp["total_marks"], "topic_difficulty": {},
            "topic_counts": {}, "format_counts": {}, "type_counts": {}}
    if diagram_count is not None:
        spec["diagram_count"] = diagram_count
    return spec, errors


def allocation_prompt(spec):
    if not spec.get("section_allocations"):
        return ""
    bp = spec["blueprint"]
    return ("\nSECTION ALLOCATION CONTRACT: The following rows replace all global topic weights and "
            "topic difficulty defaults. Use exactly the section titles in the blueprint below. "
            "Keep compound subparts and OR alternatives in ONE numbered question. Each question must "
            "return allocation_id, textbook_id, topic_id, group_id and topic (the topic name), "
            "and each section must return section_id. Copy these identifiers exactly. "
            "Each row requires its count of questions with its marks, format and difficulty. "
            "diagrams=true requires at least one necessary supplied diagram in the row; false forbids "
            "supplied diagrams. diagram_minimum_satisfied=true makes diagrams optional for that top-up row. Follow existing diagram_spec requirements. Explicit Additional Instructions "
            "and per-question overrides take precedence over conflicting row fields. "
            "Return internal_choice_count (integer, number of OR blocks) on every question.\n" +
            json.dumps({"sections": bp["sections"], "allocations": spec["section_allocations"]}, ensure_ascii=False) + "\n")


def allocation_misses(sections, spec):
    rows = spec.get("section_allocations")
    if not rows:
        return []
    misses, found, index = [], {}, 0
    by_id = {a["allocation_id"]: a for a in rows}
    overrides = {o["n"]: o for o in spec.get("question_overrides", [])}
    for section in sections or []:
        sid = section.get("section_id") or section_key(section.get("title", ""))
        for q in section.get("questions", []):
            index += 1
            row = by_id.get(q.get("allocation_id"))
            if not row:
                misses.append(f"Question {index}: missing or unknown section allocation identity.")
                continue
            found.setdefault(row["allocation_id"], []).append(q)
            if sid != row["section_id"] or section_key(section.get("title", sid)) != section_key(row["section_id"]):
                misses.append(f"Question {index}: must be in Section {row['section_id']}.")
            if any(q.get(k) != row[k] for k in ("textbook_id", "topic_id", "group_id")) or q.get("topic") != row["name"]:
                misses.append(f"Question {index}: textbook/topic/group identity mismatch.")
            if q.get("marks") != row["marks"] or q.get("format") != row["format"]:
                misses.append(f"Question {index}: expected {row['marks']} marks, {row['format']}.")
            section_level = next((v for k, v in spec.get("section_difficulty", {}).items() if section_key(k) == section_key(sid)), None)
            level = overrides.get(index, {}).get("difficulty") or section_level or spec.get("format_difficulty", {}).get(q.get("format")) or row["difficulty"]
            if q.get("difficulty") != level:
                misses.append(f"Question {index}: expected difficulty {level}.")
    for row in rows:
        qs = found.get(row["allocation_id"], [])
        label = f"Section {row['section_id']} / {row['name']}"
        if len(qs) != row["count"]:
            misses.append(f"{label}: expected {row['count']} questions, got {len(qs)}.")
        if not spec.get("diagram_override"):
            diagrams = sum(bool(q.get("needs_diagram")) for q in qs)
            if row["diagrams"] and not diagrams and not row.get("diagram_minimum_satisfied"):
                misses.append(f"{label}: requires at least one supplied diagram.")
            if not row["diagrams"] and diagrams:
                misses.append(f"{label}: supplied diagrams are disabled.")
    if not spec.get("is_topup"):
        for section in spec["blueprint"]["sections"]:
            for group in section["groups"]:
                group_qs = [q for a in rows if a["section_id"] == section["id"] and a["group_id"] == group["id"] for q in found.get(a["allocation_id"], [])]
                wanted = group.get("internal_choices")
                if wanted is not None and (any(type(q.get("internal_choice_count")) is not int for q in group_qs) or sum(q.get("internal_choice_count", 0) for q in group_qs if type(q.get("internal_choice_count")) is int) != wanted):
                    misses.append(f"Section {section['id']} / {group['id']}: expected {wanted} internal OR choices.")
    return misses


def apply_allocation_overrides(spec, overrides):
    """Layer recognized prose fields without creating contradictory global quotas."""
    result = deepcopy(spec)
    result["instruction_overrides"] = overrides
    for key in ("section_difficulty", "question_overrides", "format_difficulty", "allow_out_of_syllabus", "unparsed"):
        if overrides.get(key):
            result[key] = deepcopy(overrides[key])
    for row in result["section_allocations"]:
        level = overrides.get("difficulty")
        for name, value in overrides.get("topic_difficulty", {}).items():
            if name.casefold() in (row["name"].casefold(), f"{row['parent_topic']} > {row['name']}".casefold()):
                level = value
        section_level = next((v for k, v in overrides.get("section_difficulty", {}).items() if section_key(k) == section_key(row["section_id"])), None)
        row["difficulty"] = section_level or level or row["difficulty"]
        diagram_rule = next((v for k, v in overrides.get("section_diagrams", {}).items() if section_key(k) == section_key(row["section_id"])), None)
        if diagram_rule is not None:
            row["diagrams"] = diagram_rule
    if overrides.get("diagram_count") is not None:
        result["diagram_count"] = overrides["diagram_count"]
        result["diagram_override"] = True
    conflicts = []
    if overrides.get("total_questions") and overrides["total_questions"] != result["total_questions"]:
        conflicts.append("Additional Instructions changes the question total. Update and analyze the blueprint to this total, then reassign topics.")
    formats = Counter()
    for row in result["section_allocations"]:
        formats[row["format"]] += row["count"]
    if overrides.get("format_counts") and dict(formats) != overrides["format_counts"]:
        conflicts.append("Additional Instructions changes the section formats. Update the blueprint and allocations to match the requested formats.")
    return result, conflicts


def remaining_allocations(spec, sections, count):
    result = deepcopy(spec)
    result["is_topup"] = True
    existing = Counter(q.get("allocation_id") for s in sections for q in s.get("questions", []))
    diagrams = Counter(q.get("allocation_id") for s in sections for q in s.get("questions", []) if q.get("needs_diagram"))
    rows, left = [], count
    for original in spec.get("section_allocations", []):
        row = dict(original)
        row["count"] = min(left, max(0, row["count"] - existing[row["allocation_id"]]))
        if row["count"]:
            # Already satisfied minima become optional, not a prohibition.
            if diagrams[row["allocation_id"]] and row["diagrams"]:
                row["diagram_minimum_satisfied"] = True
            rows.append(row)
            left -= row["count"]
    result["section_allocations"] = rows
    result["total_questions"] = sum(r["count"] for r in rows)
    result["total_marks"] = sum(r["count"] * r["marks"] for r in rows)
    return result


def allocation_coverage(sections, spec):
    """Coverage uses allocation identities; repeated names never collapse."""
    actual = Counter(q.get("allocation_id") for s in sections for q in s.get("questions", []))
    rows = {a["allocation_id"]: {"target": a["count"], "actual": actual[a["allocation_id"]]} for a in spec["section_allocations"]}
    return {"per_topic": rows, "under": [k for k, v in rows.items() if v["actual"] < v["target"]],
            "over": [k for k, v in rows.items() if v["actual"] > v["target"]], "unmatched": actual.get(None, 0)}


def paper_summary(paper):
    sections = paper.get("sections") or []
    qs = [q for s in sections for q in s.get("questions", [])]
    spec = paper.get("generation_requirements") or {}
    bp = spec.get("blueprint")
    def marks(q):
        try:
            return max(0, float(q.get("marks") or 0))
        except (ValueError, TypeError):
            return 0
    printed_marks = sum(marks(q) for q in qs)
    def mix(field, known):
        return [{"key": key, "count": sum(1 for q in qs if (q.get(field) if q.get(field) in known else "unspecified") == key),
                 "marks": sum(marks(q) for q in qs if (q.get(field) if q.get(field) in known else "unspecified") == key)} for key in [*known, "unspecified"]]
    misses = allocation_misses(sections, spec)
    actual_sections = []
    for s in sections:
        actual_sections.append({"id": s.get("section_id") or section_key(s.get("title", "")), "title": s.get("title", ""),
                                "count": len(s.get("questions", [])), "marks": sum(marks(q) for q in s.get("questions", []))})
    # A compiled board blueprint is the source of truth for what a student can
    # attempt. Raw question marks include every optional stream and therefore
    # must never replace this total in the primary summary.
    attemptable = bp.get("total_marks") if bp else printed_marks
    return {"question_count": len(qs), "target_question_count": spec.get("total_questions") or paper.get("target_question_count"),
            "target_marks": paper.get("total_marks"), "offered_marks": attemptable, "printed_marks": printed_marks, "attemptable_marks": attemptable,
            "duration_minutes": paper.get("duration_minutes"), "sections": actual_sections,
            "difficulty": mix("difficulty", ["easy", "medium", "hard"]),
            "cognitive": mix("type", ["information", "concept", "application"]),
            "diagrams": {"requested": sum(bool(q.get("needs_diagram")) for q in qs),
                         "ready": sum(bool(q.get("diagram_path")) for q in qs),
                         "failed": sum(q.get("diagram_status") == "failed" for q in qs),
                         "pending": sum(q.get("diagram_status") == "pending" for q in qs)},
            "internal_choices": sum(q.get("internal_choice_count", 0) for q in qs if type(q.get("internal_choice_count")) is int),
            "choices_unspecified": sum(type(q.get("internal_choice_count")) is not int for q in qs),
            "blueprint": {"id": bp["id"], "label": bp.get("label"), "version": bp["version"]} if bp else None,
            "mismatches": misses, "status": "Needs review" if misses else paper.get("generation_status", "ready")}
