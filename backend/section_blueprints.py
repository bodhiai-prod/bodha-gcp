"""Structured board contracts, allocation validation and observable summaries.

No database or model dependencies: the preview and worker use the same rules.
Legacy counts refer to numbered question blocks.  Current official templates
also declare scored *slots*: a slot is a printable subpart with its own topic
allocation, while its parent question remains one printed question.
"""
from board_instructions import candidate_content, detailed_template_prompt
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Literal


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


def _isc_printable_content(subject: str) -> dict:
    """Candidate directions from the current ISC specimen patterns.

    Printed-page counts are deliberately omitted: unlike a specimen PDF, a
    generated paper's page count is only known after DOCX/PDF layout.
    """
    common = (
        "1. You are allowed an additional fifteen minutes for only reading the question paper.\n"
        "2. You must NOT start writing during reading time.\n"
    )
    if subject == "mathematics":
        instructions = common + (
            "3. The Question Paper is divided into three sections and has 22 questions in all.\n"
            "4. Section A is compulsory and has fourteen questions.\n"
            "5. You are required to attempt all questions either from Section B or Section C.\n"
            "6. Section B and Section C have four questions each.\n"
            "7. Internal choices have been provided in two questions of 2 marks, two questions of 4 marks and two questions of 6 marks in Section A.\n"
            "8. Internal choices have been provided in one question of 2 marks and one question of 4 marks each in Section B and Section C.\n"
            "9. While attempting Multiple Choice Questions in Section A, B and C, you are required to write only ONE option as the answer.\n"
            "10. The intended marks for questions or parts of questions are given in brackets [ ].\n"
            "11. All workings, including rough work, should be done on the same page as, and adjacent to, the rest of the answer.\n"
            "12. Mathematical tables and graph papers are provided."
        )
    elif subject == "physics":
        instructions = common + (
            "3. There are twenty questions in this paper. Answer all questions.\n"
            "4. There are four sections in the paper: A, B, C and D. Internal choices have been provided in two questions each in Sections B, C and D.\n"
            "5. Section A consists of one question having fourteen sub-parts of one mark each.\n"
            "6. While attempting Multiple Choice Questions in Section A, you are required to write only ONE option as the answer.\n"
            "7. Section B consists of seven questions of two marks each.\n"
            "8. Section C consists of nine questions of three marks each.\n"
            "9. Section D consists of three questions of five marks each.\n"
            "10. The intended marks for questions are given in brackets [ ].\n"
            "11. A list of useful constants and relations is given at the end of this paper.\n"
            "12. A simple scientific calculator without a programmable memory may be used for calculations."
        )
    elif subject == "chemistry":
        instructions = common + (
            "3. It is divided into four sections and has twenty one questions in all.\n"
            "4. Answer all questions.\n"
            "5. Section A has fourteen subparts. Each question carries 1 mark.\n"
            "6. While attempting Multiple Choice Questions in Section A, you are required to write only ONE option as the answer.\n"
            "7. Section B has ten questions. Each question carries 2 marks.\n"
            "8. Section C has seven questions. Each question carries 3 marks.\n"
            "9. Section D has three questions. Each question carries 5 marks.\n"
            "10. Internal choices have been provided in one question each in Sections B, C and D.\n"
            "11. The intended marks for questions are given in brackets [ ].\n"
            "12. All working, including rough work, should be done on the same sheet as, and adjacent to the rest of the answer.\n"
            "13. Balanced equations must be given wherever possible and diagrams where they are helpful.\n"
            "14. When solving numerical problems, all essential workings must be shown."
        )
    else:  # biology
        instructions = common + (
            "3. There are eighteen questions in this paper. Answer all questions.\n"
            "4. There are four sections in the paper: A, B, C and D.\n"
            "5. Section A consists of one question with fourteen one-mark sub-parts and three two-mark sub-parts.\n"
            "6. Section B consists of seven questions of two marks each.\n"
            "7. Section C consists of seven questions of three marks each.\n"
            "8. Section D consists of three questions of five marks each.\n"
            "9. Internal choices have been provided in one question each in Sections B, C and D.\n"
            "10. The intended marks for questions are given in brackets [ ].\n"
            "11. Draw diagrams in pencil wherever necessary, with clear labels."
        )
    if subject == "mathematics":
        sections = {
            "A": "Attempt all questions from this Section.",
            "B": "Attempt all questions from this Section or Section C, but not both.",
            "C": "Attempt all questions from this Section or Section B, but not both.",
        }
    else:
        sections = {"A": "Answer all questions from this Section.", "B": "Answer all questions from this Section.",
                    "C": "Answer all questions from this Section.", "D": "Answer all questions from this Section."}
    return {"instructions": instructions, "sections": sections}


def _official_printable_content(row: dict) -> dict:
    """Safe candidate-facing lead-ins for catalog templates added after v1."""
    if row.get("legacy") or row.get("id") == "cbse12":
        return {}
    if row.get("family") == "isc12":
        return _isc_printable_content(str(row.get("subject_variant") or "").casefold())
    return candidate_content(row) or {}


class BlueprintGroup(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = ""
    count: int = Field(strict=True, ge=1, le=200)
    marks: int = Field(strict=True, ge=1, le=200)
    format: Literal["mcq", "short_answer", "long_answer", "fill_blank", "true_false"]
    diagram_minimum: int = Field(default=0, ge=0, le=200)
    internal_choices: int | None = Field(default=None, ge=0, le=200)
    # Current official templates use this to describe printable subparts.  It
    # intentionally remains a JSON-shaped extension so old saved blueprints
    # with just count/marks keep their original contract.
    slots: list[dict] = Field(default_factory=list, max_length=200)


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
    slot_id: str | None = None
    # A contiguous run of slots with the same format and marks.  Teachers
    # allocate topics to this visible bucket; compilation expands it back to
    # the exact server-owned slots before generation.
    bucket_id: str | None = None
    textbook_id: str
    topic_id: str
    count: int = Field(strict=True, ge=1, le=200)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    diagrams: bool = False


def _slot(parent, index, marks, fmt, *, lead_in="", label=None):
    """Describe one independently allocatable, printable question part."""
    part = label or (f"({index})" if isinstance(index, int) else str(index))
    return {
        "id": f"{parent}-{str(index).lower().replace(' ', '-')}",
        "parent_id": parent,
        "parent_label": f"Question {parent[1:]}" if parent.startswith("q") else parent,
        "part_label": part,
        "lead_in": lead_in,
        "marks": marks,
        "format": fmt,
    }


def _range_group(group_id, start, count, marks, fmt, *, label=None, internal_choices=None):
    """One printed question/one allocation slot for a numbered range."""
    group = {
        "id": group_id,
        "count": count,
        "marks": marks,
        "format": fmt,
        "label": label or f"Questions {start}-{start + count - 1}: {count} x {marks}",
        "slots": [_slot(f"q{n}", "", marks, fmt, label="") for n in range(start, start + count)],
    }
    # A blank label means this is the entire numbered question, not a visible
    # '(i)' subpart.  The renderer uses parent_label for the question heading.
    for n, slot in zip(range(start, start + count), group["slots"]):
        slot["id"] = f"q{n}"
        slot["parent_id"] = f"q{n}"
        slot["parent_label"] = f"Question {n}"
        slot["part_label"] = ""
    if internal_choices is not None:
        group["internal_choices"] = internal_choices
    return group


def _compound_group(group_id, number, parts, fmt, *, label=None, lead_in="", internal_choices=None,
                    case_study=False, case_profile=""):
    """One printed question made from individually allocatable scored parts."""
    slots = [_slot(f"q{number}", i + 1, mark, fmt, lead_in=lead_in) for i, mark in enumerate(parts)]
    group = {
        "id": group_id,
        "count": 1,
        "marks": sum(parts),
        "format": fmt,
        "label": label or f"Question {number}: {len(parts)} scored parts",
        "slots": slots,
    }
    if case_study:
        group["case_study"] = True
        group["case_profile"] = case_profile or "shared stimulus with connected subparts"
        for slot in slots:
            slot["case_study"] = True
            slot["case_profile"] = group["case_profile"]
    if internal_choices is not None:
        group["internal_choices"] = internal_choices
        if case_study and internal_choices:
            for slot in slots[-int(internal_choices):]:
                slot["internal_choice_count"] = 1
    return group


def _section(identifier, title, groups, **extra):
    return {"id": identifier, "title": title, "groups": groups, **extra}


def _icse_sections(subject):
    """Exact 2026 ICSE printable structures, including their subpart marks."""
    mcq_lead = "Choose the correct answers from the given options."
    if subject == "mathematics":
        a = [
            _compound_group("q1", 1, [1] * 15, "mcq", lead_in=mcq_lead),
            _compound_group("q2", 2, [4, 4, 4], "short_answer"),
            _compound_group("q3", 3, [4, 4, 5], "short_answer"),
        ]
        b = [_compound_group(f"q{n}", n, ([5, 5] if n == 7 else [3, 3, 4]), "long_answer") for n in range(4, 11)]
    elif subject == "physics":
        a = [
            _compound_group("q1", 1, [1] * 15, "mcq", lead_in=mcq_lead),
            _compound_group("q2", 2, [1] * 6 + [2, 2], "short_answer"),
            _compound_group("q3", 3, [2] * 6 + [3], "short_answer"),
        ]
        b = [_compound_group(f"q{n}", n, [3, 3, 4], "long_answer") for n in range(4, 11)]
    elif subject == "chemistry":
        a = [
            _compound_group("q1", 1, [1] * 15, "mcq", lead_in=mcq_lead),
            _compound_group("q2", 2, [5] * 5, "short_answer"),
        ]
        b = [_compound_group(f"q{n}", n, [2, 2, 3, 3], "long_answer") for n in range(3, 9)]
    else:  # biology
        a = [
            _compound_group("q1", 1, [1] * 15, "mcq", lead_in=mcq_lead),
            _compound_group("q2", 2, [5] * 5, "short_answer"),
        ]
        b = [_compound_group(f"q{n}", n, [1, 2, 2, 2, 3], "long_answer") for n in range(3, 9)]
    return [
        _section("A", "SECTION A (40 MARKS)", a, section_instructions="Attempt all questions from this Section."),
        _section("B", "SECTION B (40 MARKS)", b, attempt_count=4,
                 section_instructions="Attempt any four questions from this Section."),
    ]


def _isc_sections(subject):
    if subject == "mathematics":
        return [
            _section("A", "SECTION A - 65 MARKS", [
                _compound_group("q1", 1, [1] * 15, "mcq", lead_in="In subparts (i) to (xi), choose the correct option; answer the remaining parts as instructed."),
                _range_group("q2-q6", 2, 5, 2, "short_answer", internal_choices=2),
                _range_group("q7-q10", 7, 4, 4, "short_answer", internal_choices=2),
                _range_group("q11-q14", 11, 4, 6, "long_answer", internal_choices=2),
            ], section_instructions="Attempt all questions from this Section."),
            _section("B", "SECTION B - 15 MARKS", [
                _compound_group("q15", 15, [1, 1, 1, 2], "short_answer"),
                _range_group("q16", 16, 1, 2, "short_answer", internal_choices=1),
                _range_group("q17-q18", 17, 2, 4, "long_answer", internal_choices=1),
            ], choice_set="optional-stream", section_instructions="Attempt this complete section or Section C, not both."),
            _section("C", "SECTION C - 15 MARKS", [
                _compound_group("q19", 19, [1, 1, 3], "short_answer"),
                _range_group("q20", 20, 1, 2, "short_answer", internal_choices=1),
                _range_group("q21-q22", 21, 2, 4, "long_answer", internal_choices=1),
            ], choice_set="optional-stream", section_instructions="Attempt this complete section or Section B, not both."),
        ]
    distributions = {
        "physics": (14, 2, 7, 3, 9, 5, 3, (2, 2, 2)),
        "chemistry": (14, 2, 10, 3, 7, 5, 3, (1, 1, 1)),
        "biology": (20, 2, 7, 3, 7, 5, 3, (1, 1, 1)),
    }
    first, bmarks, bcount, cmarks, ccount, dmarks, dcount, choices = distributions[subject]
    q1parts = ([1] * 14 + [2] * 3) if subject == "biology" else [1] * first
    return [
        _section("A", f"SECTION A - {first} MARKS", [_compound_group("q1", 1, q1parts, "mcq", lead_in="Choose the correct answer or answer each subpart as instructed.")]),
        _section("B", f"SECTION B - {bmarks * bcount} MARKS", [_range_group("main", 2, bcount, bmarks, "short_answer", internal_choices=choices[0])]),
        _section("C", f"SECTION C - {cmarks * ccount} MARKS", [_range_group("main", 2 + bcount, ccount, cmarks, "short_answer", internal_choices=choices[1])]),
        _section("D", f"SECTION D - {dmarks * dcount} MARKS", [_range_group("main", 2 + bcount + ccount, dcount, dmarks, "long_answer", internal_choices=choices[2])]),
    ]


def _cbse12_row(subject, source, *, mathematics=False):
    if mathematics:
        sections = [
            _section("A", "SECTION A (20 x 1 = 20)", [_range_group("mcq", 1, 18, 1, "mcq"), _range_group("assertion_reason", 19, 2, 1, "mcq")], section_instructions="This section comprises 18 MCQs and two Assertion-Reason questions of 1 mark each."),
            _section("B", "SECTION B (5 x 2 = 10)", [_range_group("vsa", 21, 5, 2, "short_answer", internal_choices=2)]),
            _section("C", "SECTION C (6 x 3 = 18)", [_range_group("sa", 26, 6, 3, "short_answer", internal_choices=3)]),
            _section("D", "SECTION D (4 x 5 = 20)", [_range_group("la", 32, 4, 5, "long_answer", internal_choices=2)]),
            _section("E", "SECTION E (3 x 4 = 12)", [
                _compound_group("q36", 36, [1, 1, 2], "short_answer", internal_choices=1, case_study=True, case_profile="passage/application case; 1+1+(2 OR 2)"),
                _compound_group("q37", 37, [1, 1, 2], "short_answer", internal_choices=1, case_study=True, case_profile="passage/application case; 1+1+(2 OR 2)"),
                _compound_group("q38", 38, [2, 2], "short_answer", case_study=True, case_profile="passage/application case; 2+2; no internal choice"),
            ]),
        ]
        marks = 80
    else:
        case_shapes = {
            "Physics": [([1, 1, 1, 1], None, "contextual passage/application; four connected 1-mark parts; no internal choice")] * 2,
            "Chemistry": [([1, 1, 2], 1, "case/data/graph-based stimulus; 1+1+(2 OR 2)")] * 2,
            "Biology": [([1, 2, 1], 1, "biological/clinical/ecological case; 1+2+(1 OR 1)")] * 2,
        }
        shapes = case_shapes[subject]
        sections = [
            _section("A", "SECTION A (16 x 1 = 16)", [_range_group("mcq", 1, 12, 1, "mcq"), _range_group("assertion_reason", 13, 4, 1, "mcq")]),
            _section("B", "SECTION B (5 x 2 = 10)", [_range_group("vsa", 17, 5, 2, "short_answer", internal_choices=2)]),
            _section("C", "SECTION C (7 x 3 = 21)", [_range_group("sa", 22, 7, 3, "short_answer", internal_choices=1)]),
            _section("D", "SECTION D (2 x 4 = 8)", [
                _compound_group("q29", 29, shapes[0][0], "short_answer", internal_choices=shapes[0][1], case_study=True, case_profile=shapes[0][2]),
                _compound_group("q30", 30, shapes[1][0], "short_answer", internal_choices=shapes[1][1], case_study=True, case_profile=shapes[1][2]),
            ], section_instructions="This section contains two case-based questions of 4 marks each. Read each shared stimulus and answer only its connected subparts."),
            _section("E", "SECTION E (3 x 5 = 15)", [_range_group("la", 31, 3, 5, "long_answer", internal_choices=3)]),
        ]
        marks = 70
    return {
        "id": f"cbse12-{subject.lower()}", "version": 2,
        "label": f"CBSE Class 12 {subject}", "family": "cbse12", "subject_variant": subject,
        "subject_aliases": [subject.lower(), "maths" if mathematics else subject.lower()],
        "academic_session": "2026-27", "official_source": source, "legacy": False,
        "total_marks": marks, "duration_minutes": 180, "sections": sections,
        "template": f"Generate the current CBSE Class 12 {subject.upper()} paper using the server-owned section, question, part, marks and choice contract. Print only candidate-facing question content. Section instructions and question lead-ins are printed by the server; never turn them into the first numbered question.",
    }


def _apply_current_official_structures(rows):
    """Upgrade catalog rows to slot-aware official 2026 structures."""
    by_id = {row.get("id"): row for row in rows}
    for subject in ("physics", "chemistry", "biology", "mathematics"):
        row = by_id.get(f"icse10-{subject}")
        if row:
            row["version"] = 2
            row["sections"] = _icse_sections(subject)
    for subject in ("mathematics", "physics", "chemistry", "biology"):
        row = by_id.get(f"isc12-{subject}")
        if row:
            row["version"] = 2
            row["sections"] = _isc_sections(subject)
    # CBSE X already has official question ranges.  Add a slot for every
    # scorable part; case-study groups are expanded below by their 1+1+2 form.
    for row in rows:
        if row.get("family") == "cbse10" and not row.get("legacy"):
            row["version"] = 2
            for section in row.get("sections") or []:
                for group in section.get("groups") or []:
                    if group.get("slots"):
                        continue
                    start = _question_start(group.get("label", ""))
                    if start is None:
                        continue
                    slots = []
                    for number in range(start, start + group["count"]):
                        if "case" in group.get("id", ""):
                            slots.extend(_compound_group(group["id"], number, [1, 1, max(1, group["marks"] - 2)], group["format"], internal_choices=1, case_study=True, case_profile="context/source/data-based stimulus; 1+1+(2 OR 2)").get("slots", []))
                        else:
                            slots.extend(_range_group(group["id"], number, 1, group["marks"], group["format"]).get("slots", []))
                    group["slots"] = slots
    legacy_cbse12 = by_id.get("cbse12")
    if legacy_cbse12:
        legacy_cbse12.setdefault("family", "cbse12")
        legacy_cbse12.setdefault("subject_variant", "Legacy generic")
        legacy_cbse12.setdefault("legacy", True)
    rows.extend([
        _cbse12_row("Mathematics", "https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Maths-SQP.pdf", mathematics=True),
        _cbse12_row("Physics", "https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Physics-SQP.pdf"),
        _cbse12_row("Chemistry", "https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Chemistry-SQP.pdf"),
        _cbse12_row("Biology", "https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Biology-SQP.pdf"),
    ])
    return rows


def _question_start(label):
    match = re.search(r"Questions?\s+(\d+)", str(label), re.I)
    return int(match.group(1)) if match else None


def catalog():
    rows = json.loads(Path(__file__).with_name("blueprint_catalog.json").read_text(encoding="utf-8"))
    rows.extend(json.loads(Path(__file__).with_name("official_blueprint_catalog.json").read_text(encoding="utf-8")))
    rows = _apply_current_official_structures(rows)
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
            section["section_instructions"] = section_instructions.get(section.get("id")) or section.get("section_instructions", "")
        if not row.get("legacy"):
            row["template"] = detailed_template_prompt(row)
    return rows


def printable_template_instructions(paper: dict) -> str:
    """Resolve maintained board directions, or a custom blueprint snapshot."""
    blueprint = (paper.get("generation_requirements") or {}).get("blueprint") or {}
    row = next((row for row in catalog() if row.get("id") == blueprint.get("id")), None)
    # Built-in candidate directions are maintained centrally. Older snapshots
    # may contain placeholders or abbreviated instructions, so refresh prose
    # without rewriting their persisted section/question allocation contract.
    if row and not row.get("legacy"):
        return row["student_instructions"]
    fallback = _PRINTABLE_TEMPLATE_CONTENT.get(blueprint.get("id"), {}).get("instructions", "")
    return str(blueprint.get("student_instructions") or fallback).strip()


def hydrate_section_instructions(paper: dict) -> None:
    """Attach known template section lead-ins to persisted or legacy sections."""
    blueprint = (paper.get("generation_requirements") or {}).get("blueprint") or {}
    fallback = _PRINTABLE_TEMPLATE_CONTENT.get(blueprint.get("id"), {}).get("sections", {})
    row = next((row for row in catalog() if row.get("id") == blueprint.get("id")), None)
    if row and not row.get("legacy"):
        fallback = {section["id"]: section.get("section_instructions", "") for section in row["sections"]}
    by_id = {
        str(s.get("id")): fallback.get(s.get("id")) or s.get("section_instructions", "")
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
    keys = ("textbook_id", "topic_id", "group_id", "slot_id") if any(row.get("slot_id") for row in rows) else ("textbook_id", "topic_id", "group_id")
    identity = tuple(question.get(key) for key in keys)
    if any(value in (None, "") for value in identity):
        return False
    matches = [
        row for row in rows
        if tuple(row.get(key) for key in keys) == identity
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
            from slot_identity import restore_missing_identity
            restore_missing_identity(question, {**row, **({'topic': row['name']} if 'name' in row else {})})
            # Allocation placement is authoritative; a model-supplied section
            # id or title cannot override it.
            question["section_id"] = row["section_id"]
            for key in ("slot_id", "parent_id", "parent_label", "part_label", "parent_lead_in"):
                if row.get(key) not in (None, ""):
                    question[key] = row[key]
            by_section[row["section_id"]]["questions"].append(question)
    normalized = [section for section in skeleton if section["questions"]]
    if unknown:
        normalized.append({
            "section_id": "unplaced-generated-questions",
            "title": "Generated Questions Requiring Review",
            "questions": unknown,
        })
    return normalized, misses


def printable_question_blocks(section: dict) -> list[dict]:
    """Group adjacent allocated slots into their server-owned printed question.

    Older papers have no parent metadata and deliberately remain one printed
    question per stored object.
    """
    blocks = []
    current = None
    for question in section.get("questions") or []:
        parent_id = question.get("parent_id")
        if parent_id and current and current["parent_id"] == parent_id:
            current["parts"].append(question)
            continue
        current = {
            "parent_id": parent_id,
            "label": question.get("parent_label") or "",
            "lead_in": question.get("parent_lead_in") or "",
            "parts": [question],
        }
        blocks.append(current)
    return blocks


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


def internal_choice_misses(sections: list, spec: dict) -> list[dict]:
    """Return machine-readable internal-OR mismatches for a compiled blueprint.

    The rendered standalone ``OR`` line is the source of truth.  Keeping this
    structured means recovery code never has to parse a teacher-facing warning
    string to learn which question slots are safe to replace.
    """
    if not spec.get("section_allocations") or spec.get("is_topup"):
        return []
    questions_by_row: dict[str, list] = {}
    for section in sections or []:
        for question in section.get("questions") or []:
            if isinstance(question, dict) and question.get("allocation_id"):
                questions_by_row.setdefault(question["allocation_id"], []).append(question)

    rows = spec.get("section_allocations") or []
    out = []
    for section in (spec.get("blueprint") or {}).get("sections") or []:
        for group in section.get("groups") or []:
            wanted = group.get("internal_choices")
            if wanted is None:
                continue
            group_questions = [
                question
                for row in rows
                if row.get("section_id") == section.get("id") and row.get("group_id") == group.get("id")
                for question in questions_by_row.get(row.get("allocation_id"), [])
            ]
            # Do not trust provider or legacy metadata here: this validator is
            # specifically about what a student can see in the rendered text.
            counts = [derived_internal_choice_count(question) for question in group_questions]
            actual = sum(counts)
            unspecified = 0
            if unspecified or actual != wanted:
                out.append({
                    "section_id": section.get("id"),
                    "group_id": group.get("id"),
                    "expected": wanted,
                    "actual": actual,
                    "unspecified": unspecified,
                    "eligible_question_ids": [q.get("id") for q in group_questions if q.get("id")],
                })
    return out


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


def group_slots(group):
    """Return allocation units for a group; synthesize legacy numbered slots."""
    declared = group.get("slots") or []
    if declared:
        return declared
    return [{
        "id": f"{group.get('id')}-{index}", "parent_id": f"{group.get('id')}-{index}",
        "parent_label": "", "part_label": "", "lead_in": "",
        "marks": group.get("marks"), "format": group.get("format"),
    } for index in range(1, int(group.get("count") or 0) + 1)]


def allocation_buckets(group):
    """Return contiguous same-format/same-mark allocation buckets for a group."""
    buckets = []
    for slot in group_slots(group):
        marks = slot.get("marks", group.get("marks"))
        fmt = slot.get("format", group.get("format"))
        if buckets and buckets[-1]["marks"] == marks and buckets[-1]["format"] == fmt:
            buckets[-1]["slots"].append(slot)
            continue
        buckets.append({
            "id": f"{group.get('id')}:{slot.get('id')}",
            "marks": marks,
            "format": fmt,
            "slots": [slot],
        })
    return buckets


def normalize_template_allocations(bp: dict, allocations: list[Any]) -> tuple[list[dict], list[str]]:
    """Convert every current-template selection into canonical bucket rows.

    Earlier versions of the editor saved a count against a printable slot (or
    its parent question).  Current templates allocate a count across a bucket
    of compatible slots.  Keeping that migration only in the browser meant a
    saved configuration could look complete in the UI but fail differently in
    the API.  This backend boundary is authoritative for preview *and*
    generation, so both paths now see exactly the same allocation contract.
    """
    groups = {(section["id"], group["id"]): group
              for section in bp.get("sections") or []
              for group in section.get("groups") or []}
    bucket_by_id, bucket_for_slot, buckets_for_parent = {}, {}, {}
    for pair, group in groups.items():
        for bucket in allocation_buckets(group):
            bucket_by_id[(*pair, bucket["id"])] = bucket
            for slot in bucket["slots"]:
                bucket_for_slot[(*pair, slot["id"])] = bucket
                parent_key = (*pair, slot.get("parent_id"))
                buckets_for_parent.setdefault(parent_key, []).append(bucket)

    normalized: list[dict] = []
    errors: list[str] = []
    occupancy: Counter = Counter()
    merged: dict[tuple, dict] = {}

    def add(row: dict, pair: tuple[str, str], bucket: dict, count: int) -> None:
        key = (*pair, bucket["id"], row["textbook_id"], row["topic_id"],
               row.get("difficulty"), bool(row.get("diagrams")))
        if key in merged:
            merged[key]["count"] += count
        else:
            item = {**row, "slot_id": None, "bucket_id": bucket["id"], "count": count}
            merged[key] = item
            normalized.append(item)
        occupancy[(*pair, bucket["id"])] += count

    for raw in allocations or []:
        row = raw.model_dump() if isinstance(raw, Allocation) else dict(raw)
        pair = (row.get("section_id"), row.get("group_id"))
        group = groups.get(pair)
        if not group:
            errors.append("An assignment refers to a section or question type that is no longer in this template. Re-select its topics.")
            continue
        # Count-based custom/legacy templates deliberately keep their old
        # behaviour.  Only official slot templates need migration.
        if not group.get("slots"):
            normalized.append(row)
            continue
        requested = int(row.get("count") or 0)
        bucket_id, slot_id = row.get("bucket_id"), row.get("slot_id")
        if bucket_id:
            bucket = bucket_by_id.get((*pair, bucket_id))
            candidates = [bucket] if bucket else []
        elif slot_id:
            exact = bucket_for_slot.get((*pair, slot_id))
            candidates = [exact] if exact else []
            if not candidates:
                seen_bucket_ids = set()
                for candidate in buckets_for_parent.get((*pair, slot_id), []):
                    if candidate["id"] not in seen_bucket_ids:
                        candidates.append(candidate)
                        seen_bucket_ids.add(candidate["id"])
        else:
            candidates = []
        if not candidates:
            errors.append(f"Section {pair[0]} / {group.get('label', pair[1])}: an assignment uses an outdated question-part identifier. Re-select its topics.")
            continue
        remaining = requested
        for bucket in candidates:
            if remaining <= 0:
                break
            capacity = len(bucket["slots"]) - occupancy[(*pair, bucket["id"])]
            accepted = min(remaining, max(0, capacity))
            if accepted:
                add(row, pair, bucket, accepted)
                remaining -= accepted
        if remaining:
            available = requested - remaining
            errors.append(f"Section {pair[0]} / {group.get('label', pair[1])}: requested {requested} parts but only {available} matching parts are available. Re-select this assignment.")
    return normalized, errors


def group_offered_marks(group):
    return sum(slot.get("marks", 0) for slot in group_slots(group))


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
            slot_ids = set()
            for slot in group_slots(group):
                if not slot.get("id") or slot["id"] in slot_ids:
                    errors.append(f"Section {sid} / {group.get('id')}: slot identifiers must be unique.")
                slot_ids.add(slot.get("id"))
                if type(slot.get("marks")) is not int or not 1 <= slot["marks"] <= 200:
                    errors.append(f"Section {sid} / {group.get('id')}: slot marks must be positive integers.")
                if slot.get("format", group.get("format")) not in ("mcq", "short_answer", "long_answer", "fill_blank", "true_false"):
                    errors.append(f"Section {sid} / {group.get('id')}: slot format is unsupported.")
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
    return sum(group_offered_marks(g) for g in groups)


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
    allocations, migration_errors = normalize_template_allocations(bp, allocations)
    errors.extend(migration_errors)
    if bp["total_marks"] != total_marks:
        errors.append(f"Paper marks must match this blueprint ({bp['total_marks']}).")
    topics = {(r["textbook_id"], r["topic_id"]): r for b in books for r in topic_catalog(b)}
    groups = {(s["id"], g["id"]): g for s in bp["sections"] for g in s["groups"]}
    slots = {(sid, gid, slot["id"]): {**slot, "marks": slot.get("marks", group["marks"]),
                                       "format": slot.get("format", group["format"])}
             for (sid, gid), group in groups.items() for slot in group_slots(group)}
    buckets = {
        (sid, gid, bucket["id"]): bucket
        for (sid, gid), group in groups.items()
        for bucket in allocation_buckets(group)
    }
    counts, seen, resolved = Counter(), set(), []

    def resolve(a, pair, slot, *, bucket_id=None):
        key = (*pair, slot.get("id") if slot else None, a["textbook_id"], a["topic_id"])
        topic = topics.get((a["textbook_id"], a["topic_id"]))
        if not topic or key in seen:
            errors.append("An assignment has a stale section/topic or is duplicated. Review the section editor.")
            return
        seen.add(key)
        slot_id = slot.get("id") if slot else None
        counts[(*pair, slot_id)] += 1 if slot else a["count"]
        resolved.append({**a, **topic, "difficulty": a.get("difficulty") or difficulty,
                         "slot_id": slot_id, "bucket_id": bucket_id or a.get("bucket_id"),
                         "count": 1 if slot else a["count"],
                         "marks": (slot or groups[pair])["marks"],
                         "format": (slot or groups[pair])["format"],
                         "parent_id": (slot or {}).get("parent_id"),
                         "parent_label": (slot or {}).get("parent_label", ""),
                         "part_label": (slot or {}).get("part_label", ""),
                         "parent_lead_in": (slot or {}).get("lead_in", ""),
                         "case_study": bool((slot or {}).get("case_study") or groups[pair].get("case_study")),
                         "case_profile": (slot or {}).get("case_profile") or groups[pair].get("case_profile", ""),
                         **({"internal_choice_count": (slot or {}).get("internal_choice_count")}
                            if "internal_choice_count" in (slot or {}) else {}),
                         "allocation_id": hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]})

    for raw in allocations:
        a = raw.model_dump() if isinstance(raw, Allocation) else dict(raw)
        pair = (a["section_id"], a["group_id"])
        # Slot-aware templates require one topic assignment per exact printed
        # part.  Legacy rows deliberately retain their count-based behaviour.
        explicit_slots = bool(groups.get(pair, {}).get("slots"))
        slot_id = a.get("slot_id")
        bucket_id = a.get("bucket_id")
        if explicit_slots and not slot_id and not bucket_id:
            errors.append("An official template assignment is missing its question-part slot. Review the section editor.")
            continue
        if bucket_id:
            bucket = buckets.get((*pair, bucket_id))
            topic = topics.get((a["textbook_id"], a["topic_id"]))
            key = (*pair, bucket_id, a["textbook_id"], a["topic_id"])
            if not bucket or not topic or key in seen:
                errors.append("An assignment has a stale section/topic or is duplicated. Review the section editor.")
                continue
            seen.add(key)
            available_slots = [slot for slot in bucket["slots"] if counts[(*pair, slot["id"])] == 0]
            if a["count"] > len(available_slots):
                errors.append(f"Section {pair[0]} / {pair[1]}: requested {a['count']} compatible parts but only {len(available_slots)} remain. Re-select this assignment.")
                continue
            # Legacy blueprints expose one count-based group rather than exact
            # printed slots.  Keep their existing aggregate contract intact.
            if not explicit_slots:
                seen.discard((*pair, None, a["textbook_id"], a["topic_id"]))
                resolve(a, pair, None, bucket_id=bucket_id)
                continue
            # Replace the bucket-level identity with exact slot identities.
            # This keeps the generation contract, exports, and recovery logic
            # unchanged while the editor stays compact.
            for slot in available_slots[:a["count"]]:
                seen.discard((*pair, slot["id"], a["textbook_id"], a["topic_id"]))
                resolve(a, pair, {**slot, "marks": slot.get("marks", groups[pair]["marks"]),
                                  "format": slot.get("format", groups[pair]["format"])}, bucket_id=bucket_id)
            continue
        if not explicit_slots:
            slot_id = slot_id or None
        slot = slots.get((*pair, slot_id)) if slot_id else None
        if pair not in groups or not topics.get((a["textbook_id"], a["topic_id"])) or (explicit_slots and not slot):
            errors.append("An assignment has a stale section/topic or is duplicated. Review the section editor.")
            continue
        if explicit_slots and a["count"] != 1:
            errors.append("Each official question part must be assigned exactly once.")
            continue
        resolve(a, pair, slot)
    for pair, group in groups.items():
        if group.get("slots"):
            # Report missing selections by their printable parent question,
            # rather than flooding the teacher with one warning per subpart.
            # The exact-slot count remains enforced; only the presentation is
            # compact and actionable.
            by_parent: dict[str, list[dict]] = {}
            for slot in group_slots(group):
                parent = slot.get("parent_id") or slot["id"]
                by_parent.setdefault(parent, []).append(slot)
            for parent_slots in by_parent.values():
                missing = [slot for slot in parent_slots if counts[(*pair, slot["id"])] == 0]
                overfilled = [slot for slot in parent_slots if counts[(*pair, slot["id"])] > 1]
                label = parent_slots[0].get("parent_label") or group.get("label", pair[1])
                if missing:
                    errors.append(f"Section {pair[0]} / {label}: {len(missing)} of {len(parent_slots)} parts still need topic assignments.")
                if overfilled:
                    errors.append(f"Section {pair[0]} / {label}: {len(overfilled)} parts have conflicting topic assignments. Re-select those parts.")
        elif counts[(*pair, None)] != group["count"]:
            errors.append(f"Section {pair[0]} / {group.get('label', pair[1])}: assigned {counts[(*pair, None)]} of {group['count']} questions.")
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
            # Generation operates on scored slots; printable parent-question
            # count remains separately available for UI and exports.
            "total_questions": sum(len(group_slots(g)) for g in groups.values()),
            "printed_question_count": sum(g["count"] for g in groups.values()),
            "total_marks": sum(group_offered_marks(g) for g in groups.values()),
            "attemptable_marks": bp["total_marks"], "topic_difficulty": {},
            "topic_counts": {}, "format_counts": {}, "type_counts": {}}
    if diagram_count is not None:
        spec["diagram_count"] = diagram_count
    return spec, errors


def allocation_prompt(spec):
    if not spec.get("section_allocations"):
        return ""
    bp = spec["blueprint"]
    rows = deepcopy(spec['section_allocations'])
    for section in bp['sections']:
        for group in section.get('groups') or []:
            if group.get('internal_choices') is None:
                continue
            eligible = [row for row in rows if row['section_id'] == section['id'] and row['group_id'] == group['id']]
            # Exact slot allocations can carry explicit per-slot OR requests.
            # Legacy count rows retain the group-level quota.
            if any('internal_choice_count' in row for row in eligible):
                continue
            if all(row.get('slot_id') and int(row.get('count') or 1) == 1 for row in eligible):
                for index, row in enumerate(eligible):
                    row['internal_choice_count'] = int(index < int(group['internal_choices']))
    return ("\nSECTION ALLOCATION CONTRACT: The following rows replace all global topic weights and "
            "topic difficulty defaults. Use exactly the section titles in the blueprint below. "
            "Each returned object is one scored printable slot. The renderer groups slots under their server-owned parent question; "
            "do not write numbering, section directions, parent lead-ins, or part labels in question text. Each question must "
            "return allocation_id, textbook_id, topic_id, group_id, slot_id and topic (the topic name), "
            "and each section must return section_id. Copy these identifiers exactly. "
            "Each row requires its count of questions with its marks, format and difficulty. "
            "diagrams=true requires at least one necessary supplied diagram in the row; false forbids "
            "supplied diagrams. diagram_minimum_satisfied=true makes diagrams optional for that top-up row. Follow existing diagram_spec requirements. Explicit Additional Instructions "
            "and per-question overrides take precedence over conflicting row fields. "
            "Section-level selection (including optional Sections B/C) does not satisfy a group's internal OR quota. "
            "Generate ALL optional sections and ALL scored slots. Where a row supplies internal_choice_count, "
            "write exactly that many standalone OR separators in that slot. Count the slot's marks only once. "
            "For each group's internal_choices quota, write both complete alternatives inside "
            "the question text, separated by a standalone OR line. Each alternative must have "
            "the same assigned topic, marks and difficulty. A metadata count is not an alternative. "
            "Return internal_choice_count (integer, number of OR blocks) on every question.\n" +
            json.dumps({"sections": bp["sections"], "allocations": rows}, ensure_ascii=False) + "\n")


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
            if any(q.get(k) != row.get(k) for k in ("textbook_id", "topic_id", "group_id")) or (row.get("slot_id") and q.get("slot_id") != row["slot_id"]) or q.get("topic") != row["name"]:
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
            if row.get("diagrams") and not diagrams and not row.get("diagram_minimum_satisfied"):
                misses.append(f"{label}: requires at least one supplied diagram.")
            if not row.get("diagrams") and diagrams:
                misses.append(f"{label}: supplied diagrams are disabled.")
    for miss in internal_choice_misses(sections, spec):
        actual = f"{miss['actual']} rendered"
        if miss["unspecified"]:
            actual += f", {miss['unspecified']} unclassified"
        misses.append(
            f"Section {miss['section_id']} / {miss['group_id']}: expected "
            f"{miss['expected']} internal OR choices, got {actual}."
        )
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
    printed_count = len({(s.get("section_id") or s.get("title"), q.get("parent_id") or q.get("id") or str(i))
                         for s in sections for i, q in enumerate(s.get("questions") or [])})
    return {"question_count": printed_count, "scored_part_count": len(qs),
            "actual_scored_part_count": len(qs), "actual_printed_question_count": printed_count,
            "target_scored_part_count": spec.get("total_questions") or paper.get("target_question_count"),
            "target_printed_question_count": spec.get("printed_question_count") or spec.get("total_questions") or paper.get("target_question_count"),
            "target_question_count": spec.get("printed_question_count") or spec.get("total_questions") or paper.get("target_question_count"),
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
