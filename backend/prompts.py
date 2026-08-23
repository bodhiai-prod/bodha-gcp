"""Prompt builders for topic extraction, question generation and solutions."""

import json

TOPIC_SYSTEM = (
    "You are an expert academic content analyst. Given chapter text from a textbook, "
    "you extract a clean hierarchy of topics and subtopics that align strictly with the "
    "text. Do not invent topics not covered by the content."
)


def topic_extract_prompt(subject: str, klass: str, text_excerpt: str) -> str:
    return (
        f"Analyse the following textbook content for Class {klass} - {subject}. "
        "Identify ALL main topics and their subtopics. Return ONLY strict JSON of the form:\n"
        "{\n  \"topics\": [ { \"name\": \"Topic Name\", \"subtopics\": [\"Subtopic 1\", ...] } ]\n}\n\n"
        "Rules:\n"
        "- Aim for 8 to 18 top-level topics (one per chapter when the content "
        "shows a table of contents or chapter headings). Each topic should have 2-6 subtopics.\n"
        "- The content may include a table-of-contents page (chapter numbers + "
        "page ranges). USE the chapter list there as the authoritative list of "
        "main topics, plus any additional chapters revealed in body text.\n"
        "- Names must be short (max 6 words), title case.\n"
        "- Cover ONLY what is present in the text — never invent topics.\n"
        "- No duplicates. No prose outside JSON.\n\n"
        f"=== TEXTBOOK CONTENT ===\n{text_excerpt}\n=== END ==="
    )


QGEN_SYSTEM = (
    "You are an expert teacher who designs original, high-quality exam questions. "
    "You NEVER copy textbook questions or content verbatim. You use the textbook only "
    "as a scope and style guide, never as a source to copy from. All questions must be "
    "originally phrased while staying strictly within the given topics."
)


QUESTION_TYPE_GUIDE = {
    "information": "Direct / information-based questions that recall facts, definitions, "
    "dates, formulas, or labelled concepts. Short, factual.",
    "concept": "Concept-based questions that test understanding of principles, "
    "relationships, reasoning and 'why/how' behind the topic.",
    "application": "Application-based questions that require applying concepts to "
    "scenarios, numerical problems, real-life cases or analysis.",
}


# How each 'format' value must be rendered. This is emitted UNCONDITIONALLY —
# unlike the percentage targets it used to sit inside, which are suppressed when
# a blueprint or custom instructions override the sliders. A board blueprint
# (ICSE/CBSE/ISC) is typically MCQ-heavy, and without these conventions the
# model inlines "(A) ... (B) ..." into the question text and leaves the options
# array empty, which breaks MCQ rendering and the answer key.
FORMAT_CONVENTIONS = (
    "FORMAT CONVENTIONS (apply to whichever formats the paper uses):\n"
    "  - mcq: do NOT inline options inside the 'question' text. Instead "
    "emit a separate 'options' array of EXACTLY 4 plain-text choices and a "
    "0-indexed 'correct_option' integer pointing to the right one. Example: "
    '{"format":"mcq","question":"What is the SI unit of force?",'
    '"options":["Newton","Pascal","Joule","Watt"],"correct_option":0}.\n'
    "  - short_answer: 2-3 sentence answer expected.\n"
    "  - long_answer: detailed multi-paragraph answer expected.\n"
    "  - fill_blank: include one or more '_____' blanks in the question.\n"
    "  - true_false: end with 'True or False?'\n"
    "  - For any custom format label, follow the spirit of the label "
    "(e.g., 'case_study' → present a short scenario then ask).\n\n"
)


# Shared by the school and competitive question prompts. The wording this
# replaced ("some questions benefit from a figure...") produced two bad
# outcomes: figures on questions where DRAWING the figure was the task (LPP, ray
# diagrams), and figures with the answer printed on them. Both are stated as
# prohibitions here and enforced again in diagram_policy.py, because the model
# ignored the softer version often enough that the prompt alone can't be the
# only line of defence.
DIAGRAM_POLICY = (
    "FIGURES / DIAGRAMS — two duties, both mandatory:\n"
    "  (a) SUPPLY a figure whenever the question cannot be solved without one.\n"
    "  (b) NEVER supply one otherwise, and never let one show the answer.\n"
    "\n"
    "THE TEST: emit a figure when a capable student could not attempt the "
    "question without it, because the figure carries a configuration or data "
    "that cannot be stated unambiguously in words. A figure is part of the "
    "paper's DATA — never decoration, never a visual aid 'for interest'.\n"
    "\n"
    "REQUIRED — the figure IS the data. Omitting it here makes the question "
    "UNANSWERABLE, which is just as serious an error as adding a needless one. "
    "Where a topic naturally produces such a question (a circle with tangents, "
    "a circuit, a labelled specimen), it is entirely correct to write the "
    "question in 'In the given figure, ...' form and supply the figure:\n"
    "  - Geometry: a specific configuration (circle with centre, chord, tangent "
    "and named points; a composite shape; two figures in a stated relation) "
    "that prose cannot pin down without ambiguity.\n"
    "  - Physics: a circuit whose topology cannot be written out; an optical "
    "set-up ALREADY ARRANGED for the student; a pulley, inclined-plane or beam "
    "arrangement whose geometry is given.\n"
    "  - Chemistry: a structural formula the student must name, identify or "
    "react; a given apparatus set-up the question asks about.\n"
    "  - Biology: an unlabelled specimen, organ, tissue or cell the student "
    "must identify or label.\n"
    "  - Data handling / Geography / Economics: a graph, histogram, table, map "
    "or chart the student must READ VALUES OFF.\n"
    "\n"
    "FORBIDDEN — producing the drawing IS the task. Supplying a figure here "
    "destroys the question:\n"
    "  - Linear programming: the student must plot the feasible region.\n"
    "  - Anything phrased 'draw', 'sketch', 'plot', 'construct', 'shade', "
    "'trace'.\n"
    "  - Ray diagrams, free-body diagrams or graphs the student is asked to "
    "produce.\n"
    "  - Geometric constructions (compass and straightedge).\n"
    "\n"
    "FORBIDDEN — decorative. The figure would add nothing the question text "
    "already carries: an illustration of photosynthesis beside a recall "
    "question, a picture of a car beside a kinematics sum.\n"
    "\n"
    "NEVER REVEAL THE ANSWER. The figure shows the GIVENS only. Every quantity "
    "the student has to find is marked with a symbol ('x', 'y', 'theta') or a "
    "question mark — NEVER with its value. If the figure could only be drawn by "
    "showing the answer, the question gets no figure.\n"
    "\n"
    "For each question that passes the test, set 'needs_diagram': true and "
    "supply 'diagram_spec':\n"
    '  { "figure": "<what to draw, one sentence, carrying no measurement that '
    'is not listed in given_values>",\n'
    '    "labels": ["<point / part names to print, e.g. \\"O\\", \\"A\\", '
    '\\"B\\">"],\n'
    '    "given_values": ["<every measurement the figure is allowed to print, '
    'e.g. \\"OA = 5 cm\\">"],\n'
    '    "unknowns": ["<what the student must find, named WITHOUT its value, '
    'e.g. \\"angle APB - mark as x\\">"],\n'
    '    "why_required": "<why words alone cannot convey this configuration>" }\n'
    "Any measurement absent from 'given_values' will be stripped from the "
    "figure. Otherwise set 'needs_diagram': false and omit 'diagram_spec'.\n"
    "\n"
    "WORKED EXAMPLE of a question that correctly gets a figure:\n"
    '  { "question": "In the given figure, O is the centre of the circle and PB '
    'is a tangent at B. If OA = 5 cm and angle OAB = 30 degrees, find angle '
    'APB.",\n'
    '    "needs_diagram": true,\n'
    '    "diagram_spec": {\n'
    '      "figure": "circle with centre O, radius OA and OB, chord AB, tangent '
    'at B meeting OA extended at P",\n'
    '      "labels": ["O", "A", "B", "P"],\n'
    '      "given_values": ["OA = 5 cm", "angle OAB = 30 degrees"],\n'
    '      "unknowns": ["angle APB - mark as x"],\n'
    '      "why_required": "the tangent/chord/extended-radius configuration '
    'cannot be stated unambiguously in words" } }\n'
    "Note what the figure does NOT carry: the value of angle APB. That is what "
    "the student must find.\n\n"
)


def difficulty_mix(difficulty: str) -> dict:
    """Per-question easy/medium/hard split for a paper-level difficulty choice.

    A 'hard' paper is not N brutal questions — real papers open with accessible
    marks and escalate. Left to itself the model reads "Overall difficulty:
    hard" and pitches every single item at the ceiling, which is why the split
    is computed here and handed over as counts rather than described in prose.
    """
    table = {
        "easy": {"easy": 60, "medium": 35, "hard": 5},
        "medium": {"easy": 25, "medium": 50, "hard": 25},
        "hard": {"easy": 20, "medium": 40, "hard": 40},
    }
    return table.get((difficulty or "medium").strip().lower(), table["medium"])


def difficulty_calibration_block(
    klass: str, difficulty: str, target_q: int, include_mix: bool = True
) -> str:
    """Define easy/medium/hard against an average student of the target class.

    Before this the whole of the difficulty signal was one line — "Overall
    difficulty: hard" — leaving the model to invent its own scale, which drifted
    towards "obscure" rather than "demanding".

    `include_mix` is off when a blueprint or custom instructions are supplied:
    the teacher's own structure decides the spread then, but the definitions
    below still apply to whatever they asked for.
    """
    block = (
        "DIFFICULTY CALIBRATION (mandatory):\n"
        f"Judge every question against ONE reference student: an average Class "
        f"{klass} student who attended the lessons and worked through the "
        "textbook exercises once. Not a topper, not a struggler.\n"
        "  - easy: about 80-90% of that class solves it unaided. One step — "
        "direct recall, a definition, or substitution into a formula they have "
        "practised. Phrased close to how the textbook puts it.\n"
        "  - medium: about half the class solves it. Two steps, or one step "
        "plus a non-obvious choice of formula. Requires connecting two ideas.\n"
        "  - hard: only the strongest ~20% solves it unaided. Three or more "
        "chained steps, an unfamiliar entry point, or a case the textbook does "
        "not drill directly.\n"
        "\n"
        "'hard' does NOT mean obscure trivia, heavier arithmetic, uglier "
        f"numbers, more sub-parts, or out-of-syllabus content. It stays strictly "
        f"within the Class {klass} syllabus — harder to SEE, not longer to "
        "compute. Never olympiad-style.\n"
        "'easy' does NOT mean trivial — it must still test the topic.\n\n"
    )
    if not include_mix:
        return block

    mix = difficulty_mix(difficulty)
    n_easy = round(target_q * mix["easy"] / 100)
    n_hard = round(target_q * mix["hard"] / 100)
    n_medium = max(0, target_q - n_easy - n_hard)
    return block + (
        f"The paper's overall difficulty is '{difficulty}'. That is a MIX, not a "
        "level applied to every item. Set each question's 'difficulty' field so "
        "the paper contains approximately:\n"
        f"  - easy: {n_easy} question(s)\n"
        f"  - medium: {n_medium} question(s)\n"
        f"  - hard: {n_hard} question(s)\n"
        f"Do NOT make every question '{difficulty}'.\n\n"
    )


def derive_target_question_count(total_marks: int) -> int:
    """How many questions a paper of this size should carry.

    Lives here because `qgen_prompt` is what actually tells the model this
    number; anything that later needs to know how many questions a paper was
    *supposed* to have must derive it the same way or the two will drift.
    """
    return max(5, min(25, total_marks // 3))


def qgen_prompt(
    subject: str,
    klass: str,
    topics_weighted: list,  # list of {"name": str, "weight": int}
    difficulty: str,
    distribution: dict,
    total_marks: int,
    duration: int,
    context_excerpt: str,
    feedback_hints: str = "",
    format_distribution: dict | None = None,
    custom_instructions: str = "",
    section_blueprint: str = "",
    topup_count: int = 0,
    existing_questions: list | None = None,
) -> str:
    target_q = derive_target_question_count(total_marks)
    info_n = round(target_q * distribution.get("information", 0) / 100)
    concept_n = round(target_q * distribution.get("concept", 0) / 100)
    app_n = max(1, target_q - info_n - concept_n) if target_q - info_n - concept_n > 0 else round(
        target_q * distribution.get("application", 0) / 100
    )

    guide = "\n".join(f"- {k}: {v}" for k, v in QUESTION_TYPE_GUIDE.items())
    feedback_block = ""
    if feedback_hints:
        feedback_block = (
            "\n=== TEACHER PREFERENCES (learned from past edits) ===\n"
            f"{feedback_hints}\n=== END ===\n\n"
        )

    # Compute proportional question counts per topic so the LLM has a hard target.
    total_w = sum(max(1, int(t.get("weight", 5))) for t in topics_weighted) or 1
    topic_lines = []
    for t in topics_weighted:
        w = max(1, int(t.get("weight", 5)))
        share = round(target_q * w / total_w)
        topic_lines.append(
            f"- {t['name']}  (weight {w}, target ~{share} question{'s' if share != 1 else ''})"
        )
    topics_block = "\n".join(topic_lines)

    has_blueprint = bool(section_blueprint and section_blueprint.strip())
    has_custom = bool(custom_instructions and custom_instructions.strip())
    # When EITHER a blueprint or free-form custom instructions are given,
    # the user-supplied prompt takes precedence over the slider-based
    # Question Distribution and Format Mix. The teacher is expected to
    # describe those inside their prompt/blueprint if they care about them.
    has_override = has_blueprint or has_custom

    # Optional question-format mix (MCQ / Short Answer / Long Answer / etc).
    format_block = ""
    if format_distribution and not has_override:
        fmt_lines = []
        for fmt, pct in format_distribution.items():
            if pct <= 0:
                continue
            n = max(1, round(target_q * pct / 100))
            fmt_lines.append(f"- {fmt}: ~{n} question{'s' if n != 1 else ''} ({pct}%)")
        if fmt_lines:
            format_block = (
                "QUESTION FORMAT MIX (mandatory — set each question's 'format' field):\n"
                + "\n".join(fmt_lines)
                + "\n"
            )

    custom_block = ""
    if has_custom:
        custom_block = (
            "TEACHER'S ADDITIONAL INSTRUCTIONS (HIGHEST PRIORITY — these "
            "override the default question-type and format defaults. The "
            "teacher will usually describe their desired section / type / "
            "format mix inside this block; honour it strictly):\n"
            f"{custom_instructions.strip()}\n\n"
        )

    blueprint_block = ""
    if has_blueprint:
        blueprint_block = (
            "PAPER BLUEPRINT (HIGHEST PRIORITY — overrides any default section / type "
            "split). Reproduce sections, question counts, marks, internal-choice rules, "
            "and per-question formats EXACTLY as described below. Use the section "
            "TITLES verbatim as the LLM 'sections[].title' values. If the blueprint "
            "specifies internal choice (e.g. 'attempt any 4 of 6'), generate ALL the "
            "alternatives shown (e.g. 6 questions for a 4-of-6 section) and reflect the "
            "rule in the section's title (e.g. 'Section B (40 Marks) — Attempt any "
            "FOUR of the following SIX questions').\n\n"
            f"{section_blueprint.strip()}\n\n"
        )

    # Default Bloom-based section split — only used when neither blueprint nor
    # custom_instructions are given. The teacher's prompt is expected to
    # describe their preferred type/marks distribution.
    default_section_block = "" if has_override else (
        f"Target question counts: information={info_n}, concept={concept_n}, application={app_n}.\n\n"
        f"Question type definitions:\n{guide}\n\n"
    )
    marks_rule = (
        "" if has_override else
        "Marks allocation: assign 1-2 marks for information, 3-4 for concept, 5-6 for application, "
        "ensuring the sum equals the total marks as closely as possible.\n\n"
    )


    coverage_rule = (
        "TOPIC COVERAGE RULE (mandatory):\n"
        "- Distribute questions across ALL listed topics in proportion to their weights.\n"
        "- No single topic should exceed its target by more than 1 question.\n"
        "- If a topic has weight > 0 it MUST receive at least one question.\n\n"
    )

    # Top-up mode: the paper already exists and came up short, so this call
    # asks for the shortfall only. The whole-paper targets above would fight
    # that — a 3-question top-up cannot also cover every topic, sum to the
    # total marks, or reproduce the section split — so they are dropped
    # here and replaced with a "do not repeat what is already there" rule.
    topup_block = ""
    if topup_count > 0:
        default_section_block = ""
        marks_rule = ""
        coverage_rule = (
            "TOPIC COVERAGE RULE: spread the new questions across the listed "
            "topics, favouring any that look under-represented in the "
            "existing questions below.\n\n"
        )
        stems = []
        for q in (existing_questions or [])[:60]:
            text = " ".join(str(q).split())[:200]
            if text:
                stems.append(f"- {text}")
        existing_block = (
            "QUESTIONS ALREADY ON THIS PAPER (do NOT repeat or paraphrase "
            "any of these):\n" + "\n".join(stems) + "\n\n"
        ) if stems else ""
        plural = "s" if topup_count != 1 else ""
        topup_block = (
            "TOP-UP REQUEST (HIGHEST PRIORITY — read this before "
            "anything else):\n"
            f"This paper already exists and is short by {topup_count} "
            f"question{plural}. Generate EXACTLY {topup_count} ADDITIONAL "
            "question(s) — NOT a whole new paper. Return the same JSON "
            "shape containing ONLY the new questions. Do NOT restate the "
            "existing questions. Give each new question its own sensible "
            "marks value; the new questions do not need to sum to the "
            "paper's total marks.\n\n"
            + existing_block
        )

    return (
        f"{topup_block}"
        f"Generate an ORIGINAL question paper for Class {klass} - {subject}.\n"
        f"Topics to cover (with weightage — heavier topics get more questions):\n"
        f"{topics_block}\n\n"
        f"Overall difficulty: {difficulty}\n"
        f"Total marks: {total_marks}, Duration: {duration} minutes.\n\n"
        f"{difficulty_calibration_block(klass, difficulty, target_q, include_mix=not has_override)}"
        f"{blueprint_block}"
        f"{default_section_block}"
        f"{format_block}"
        f"{FORMAT_CONVENTIONS}"
        f"{marks_rule}"
        f"{coverage_rule}"
        f"{custom_block}"
        f"{feedback_block}"
        "MATH FORMATTING (STRICT — critical for PDF rendering):\n"
        "Wrap EVERY mathematical or scientific expression in LaTeX delimiters — "
        "'$...$' for inline, '$$...$$' for block. This includes:\n"
        "  - Units with exponents: write '$\\mathrm{m\\,s^{-1}}$' not 'm s⁻¹'.\n"
        "  - Units with a middle dot: write '$\\mathrm{N\\cdot m}$' not 'N·m' or 'Nm'.\n"
        "  - Any power / subscript: '$v^2$', '$x_1$' (never Unicode ², ₁).\n"
        "  - Degrees: '$60^\\circ$' not '60°'.\n"
        "  - Vectors and fractions: '$\\vec{F}=m\\vec{a}$', '$\\frac{1}{2}mv^2$'.\n"
        "  - Greek letters: '$\\theta$', '$\\omega$'.\n"
        "Use only KaTeX + matplotlib-mathtext compatible commands. "
        "Prefer '\\mathrm{...}' over '\\text{...}'. Do NOT output Unicode "
        "superscripts, subscripts, or the degree symbol directly — always use "
        "LaTeX. For non-math subjects, ignore this.\n\n"
        f"{DIAGRAM_POLICY}"
        "Return ONLY strict JSON of the form:\n"
        "{\n"
        '  "instructions": "short instruction line",\n'
        '  "sections": [\n'
        '    { "title": "Section A - Information Based",\n'
        '      "questions": [\n'
        '        { "question": "...", "topic": "Topic name", "type": "information",\n'
        '          "format": "mcq|short_answer|long_answer|fill_blank|true_false|custom_label|\\"\\"",\n'
        '          "difficulty": "easy|medium|hard", "marks": 1, "needs_diagram": false,\n'
        '          "diagram_spec": { "figure": "...", "labels": [], "given_values": [],\n'
        '                            "unknowns": [], "why_required": "..." },\n'
        '          "options": ["A","B","C","D"], "correct_option": 0 }\n'
        '      ]\n'
        '    }, ...\n'
        "  ]\n"
        "}\n\n"
        "STRICT RULES:\n"
        "- Do NOT copy any sentence from the textbook content.\n"
        "- Every question must be originally phrased.\n"
        "- Stay strictly within the listed topics.\n"
        "- Set 'topic' field on every question to the matching topic name from the list above.\n"
        "- Set 'format' field on every question (use one of the provided format labels, or empty string if none specified).\n"
        "- For format='mcq', 'options' MUST be an array of EXACTLY 4 plain strings, and 'correct_option' MUST be one of 0,1,2,3 pointing to the correct entry. Omit these fields for non-MCQ questions.\n"
        "- Ensure sum of marks of all questions equals the total marks.\n"
        "- At most 5 questions in the entire paper may have needs_diagram=true. "
        "Papers in diagram-heavy subjects (geometry, circuits, biology) should "
        "normally have 2-4; a paper in a subject with no figural content may "
        "have none.\n"
        "- Set 'difficulty' on every question per the calibration above; the "
        "mix must match the stated counts.\n"
        "- Do NOT use markdown formatting in any field. No '**bold**', no '*italic*', "
        "no '#' headings, no backticks, no bullet '-' or '*' markers at the start of "
        "lines. Use plain prose. Question numbering is added by the renderer; do NOT "
        "prefix questions with 'Q1.', '1.', '(i)' or similar yourself.\n"
        "- No prose outside the JSON.\n\n"
        f"=== TEXTBOOK CONTEXT (style & scope only, DO NOT COPY) ===\n{context_excerpt}\n=== END ==="
    )


QPAPER_EXTRACT_SYSTEM = (
    "You extract structured questions from existing question paper PDFs. "
    "You output strict JSON only. You never invent questions not in the source."
)


def qpaper_extract_prompt(text_excerpt: str) -> str:
    return (
        "Extract every question you can find in the following question-paper text. "
        "The text was extracted from a PDF (often via OCR), so it may include "
        "noisy markers like 'Question Number : N', 'Question Id : XXX', "
        "'Question Type : MCQ', 'Options :' and option numeric IDs like '6911215.' "
        "— IGNORE these wrapper lines and only capture the actual question prose. "
        "OCR may also produce minor character glitches in math (e.g. 'a, B €' for "
        "'α, β ∈', '—' for '−'); reconstruct the cleanest plausible question text "
        "without inventing content.\n\n"
        "For each question, return its full text, your best guess of marks (integer, "
        "default 2), type (one of information|concept|application), difficulty "
        "(easy|medium|hard), and 'topic' (a short 2-5 word topic label inferred "
        "from the question content — e.g. 'Newton's Laws', 'Quadratic Equations').\n\n"
        "Return ONLY strict JSON:\n"
        "{\n"
        '  "subject": "best guess of subject or empty",\n'
        '  "class_name": "best guess of class or empty",\n'
        '  "questions": [ { "question": "...", "marks": 2, "type": "concept", '
        '"difficulty": "medium", "topic": "Topic name" } ]\n'
        "}\n\n"
        f"=== PAPER TEXT ===\n{text_excerpt}\n=== END ==="
    )


COMPETITIVE_QGEN_SYSTEM = (
    "You design ORIGINAL practice questions for competitive exams. You are given "
    "anchor samples retrieved from past papers of the SAME exam — use them ONLY "
    "to calibrate style, depth and difficulty (Easy/Medium/Hard). Never copy."
)


_EXAM_SYSTEM_OVERLAYS = {
    "JEE_MAINS": (
        "You are setting questions for JEE Main. Numerical answers must be exact "
        "(2 or 3 significant figures). Use SI units. Apply real JEE Main weightage: "
        "Physics — mechanics/electrodynamics/optics emphasis; Chemistry — physical, "
        "organic name-reactions, inorganic blocks; Mathematics — calculus, "
        "coordinate geometry, algebra. MCQ options must be plausible distractors "
        "based on common student errors (sign flips, factor of 2, unit-swap)."
    ),
    "JEE_ADV": (
        "You are setting questions for JEE Advanced. Demand multi-concept "
        "reasoning, integrated 2-3 step derivations, and unit/dimension awareness. "
        "Use the actual JEE Adv styles: 'single correct', 'one or more correct', "
        "and numerical-answer types when allowed. Avoid trivially memorisable "
        "items. Distractors must reflect plausible but wrong reasoning paths, "
        "not random wrong values."
    ),
    "UPSC": (
        "You are setting questions for the UPSC Civil Services examination. "
        "Use Indian-government and current-affairs context where appropriate. "
        "For GS/Essay prompts, demand structured, analytical, multi-perspective "
        "responses (historical, economic, social, ethical lenses). Avoid yes/no "
        "framing; favour 'critically examine', 'discuss', 'analyse' verbs."
    ),
    "CAT": (
        "You are setting questions for CAT (IIM admissions). For LRDI build a "
        "compact data set or scenario the student must reason over. For VARC "
        "favour passage-based inference and assumption-detection. For QA stay "
        "within high-school arithmetic/algebra/geometry but require multi-step "
        "manipulation. MCQs follow CAT's 4-option format with strong, close "
        "distractors."
    ),
    "NEET": (
        "You are setting questions for NEET (UG). Stay strictly within the "
        "NCERT Class 11-12 Biology / Physics / Chemistry syllabus. Use NCERT-style "
        "diction. For Biology, anchor on classification, physiology, genetics, "
        "ecology and human anatomy. MCQs use the standard 4-option format with "
        "one definitively correct answer."
    ),
}


def competitive_system_for_exam(exam_type: str | None) -> str:
    """Layer the exam-specific overlay onto the base competitive system."""
    base = COMPETITIVE_QGEN_SYSTEM
    overlay = _EXAM_SYSTEM_OVERLAYS.get((exam_type or "").upper())
    return f"{base}\n\n{overlay}" if overlay else base


def competitive_qgen_prompt(
    exam_name: str,
    topics: list[str],
    difficulty: str,
    question_count: int,
    duration: int,
    anchors: list[dict],
    rag_dist: dict,
    format_distribution: dict | None = None,
    custom_instructions: str = "",
    exam_type: str = "GENERIC",
) -> str:
    """RAG-calibrated competitive-exam paper prompt.

    `anchors` is a list of {text, difficulty, topic, marks, _score} retrieved
    from past_questions. `rag_dist` is the easy/medium/hard distribution
    actually observed across the anchors — the prompt hands this to the LLM
    as a calibration anchor for what *real* Easy/Medium/Hard looks like for
    this exam, rather than a generic notion of difficulty."""
    anchor_lines = []
    for i, a in enumerate(anchors[:8]):
        diff = (a.get("difficulty") or "medium").lower()
        tpc = a.get("topic") or ""
        marks = a.get("marks") or ""
        text = (a.get("text") or "").strip().replace("\n", " ")[:280]
        anchor_lines.append(
            f"[{i+1}] difficulty={diff} topic={tpc} marks={marks}\n    {text}"
        )
    anchors_block = "\n".join(anchor_lines) or "(none — no past papers indexed yet)"

    rag_summary = (
        f"Past-paper sample distribution for these topics: "
        f"easy={rag_dist.get('easy',0)}, medium={rag_dist.get('medium',0)}, "
        f"hard={rag_dist.get('hard',0)}.\n"
    )

    format_block = ""
    is_pure_mcq = (
        format_distribution
        and len(format_distribution) == 1
        and (format_distribution.get("mcq") or 0) >= 100
    )
    if format_distribution:
        fmt_lines = []
        for fmt, pct in format_distribution.items():
            if pct <= 0:
                continue
            n = max(1, round(question_count * pct / 100))
            fmt_lines.append(f"- {fmt}: ~{n} ({pct}%)")
        if fmt_lines:
            format_block = (
                "QUESTION FORMAT MIX (mandatory, set 'format' on each question):\n"
                + "\n".join(fmt_lines)
                + "\nFor 'mcq', emit options:[4 strings] and correct_option:0-3 "
                "as SEPARATE JSON fields (NOT inlined into the question text).\n\n"
            )
    if is_pure_mcq:
        format_block += (
            "ABSOLUTE RULE: Every single question MUST be format='mcq' with "
            "EXACTLY 4 options and a correct_option in [0,3]. Do NOT emit any "
            "numerical-answer, short-answer, fill-in-the-blank or true/false "
            "questions. For items that would normally be numerical (e.g. 'find "
            "the value of x'), wrap the numerical answer as one of 4 plausible "
            "MCQ options — the correct value plus 3 distractors derived from "
            "common student errors (off-by-one, sign flip, unit swap, dropped "
            "factor of 2, etc.). Any question lacking 4 options is INVALID.\n\n"
        )

    custom_block = ""
    if custom_instructions.strip():
        custom_block = (
            "TEACHER'S ADDITIONAL INSTRUCTIONS (HIGHEST PRIORITY — override "
            "defaults where they conflict):\n"
            f"{custom_instructions.strip()}\n\n"
        )

    topics_block = "\n".join(f"- {t}" for t in topics)
    return (
        f"Generate an ORIGINAL practice paper for the {exam_name} competitive exam "
        f"(exam_type={exam_type}).\n"
        f"Target difficulty: {difficulty}. Target question count: {question_count}. "
        f"Duration: {duration} minutes.\n\n"
        f"Topics to cover:\n{topics_block}\n\n"
        "DIFFICULTY CALIBRATION (mandatory):\n"
        f"{rag_summary}"
        "Use the anchor samples below as your concrete reference for what "
        "Easy/Medium/Hard looks like FOR THIS EXAM — they came from real past "
        "papers retrieved by topic similarity. Match their depth and style. "
        "If the target difficulty is 'medium', anchor primarily on items "
        "labelled medium; for 'hard' anchor on hard, etc. NEVER copy any "
        "anchor verbatim — produce ORIGINAL questions only.\n\n"
        f"=== RAG ANCHORS ({len(anchor_lines)} items) ===\n{anchors_block}\n=== END ===\n\n"
        f"{format_block}"
        f"{custom_block}"
        f"{DIAGRAM_POLICY}"
        "MATH FORMATTING (STRICT): wrap every math expression in '$...$' "
        "(inline) or '$$...$$' (block). Use KaTeX/matplotlib-mathtext "
        "compatible LaTeX. No raw Unicode superscripts, subscripts or '°'.\n\n"
        "Return ONLY strict JSON of the form:\n"
        "{\n"
        '  "instructions": "short instruction line",\n'
        '  "sections": [\n'
        '    { "title": "Section A",\n'
        '      "questions": [\n'
        '        { "question": "...", "topic": "Topic name",\n'
        '          "type": "information|concept|application",\n'
        '          "format": "mcq|short_answer|long_answer|fill_blank|true_false|\\"\\"",\n'
        '          "difficulty": "easy|medium|hard", "marks": 1,\n'
        '          "needs_diagram": false,\n'
        '          "diagram_spec": { "figure": "...", "labels": [], "given_values": [],\n'
        '                            "unknowns": [], "why_required": "..." },\n'
        '          "options": ["A","B","C","D"], "correct_option": 0 }\n'
        '      ]\n'
        '    }, ...\n'
        "  ]\n"
        "}\n\n"
        "STRICT RULES:\n"
        "- Originally phrased, never copy anchor wording.\n"
        "- Stay on the listed topics.\n"
        "- For format='mcq', options MUST be 4 plain strings and correct_option in [0,3].\n"
        "- Sum of marks should approximate question_count * average mark-per-question of anchors (default 1 mark each if unsure).\n"
        "- No markdown formatting in any field. No prose outside JSON.\n"
    )


SOLUTION_SYSTEM = (
    "You are an expert teacher writing the WORKED SOLUTIONS for an exam paper. "
    "A worked solution shows a student HOW the answer was reached, not merely "
    "what it is. Your reasoning is accurate, academically rigorous, and laid "
    "out in the order a teacher would work the problem on a blackboard.\n\n"
    "THE RULE THAT OVERRIDES EVERYTHING ELSE: the working comes first and the "
    "answer comes last. Never open with the result and justify it afterwards.\n\n"
    "Depth of working by question type:\n"
    "- information: the fact, then ONE line of justification naming the "
    "definition, rule or source it follows from.\n"
    "- concept: the reasoning chain in order (premise, then mechanism, then "
    "conclusion), ending with the conclusion stated plainly.\n"
    "- application: the full derivation - the given quantities, the formula "
    "chosen, the substitution of numbers into that formula, the arithmetic, "
    "the units, and only then the final answer."
)


def solution_prompt(
    subject: str,
    klass: str,
    context_excerpt: str,
    questions_payload: list,
    feedback_hints: str = "",
) -> str:
    """Build a prompt that asks the LLM to produce a mirrored answer array.

    questions_payload is a list of dicts like:
       { "id": "...", "question": "...", "type": "concept", "marks": 3,
         "format": "mcq", "options": [...], "correct_option": 0 }
    The last three are present only for MCQ items; see _render_questions().
    """
    feedback_block = ""
    if feedback_hints:
        feedback_block = (
            "\n=== TEACHER PREFERENCES FOR SOLUTIONS (learned from past edits) ===\n"
            f"{feedback_hints}\n=== END ===\n\n"
        )
    return (
        f"Write the WORKED SOLUTIONS for an exam paper for Class {klass} - "
        f"{subject}.\n"
        "Every answer must show how the result was reached and then state it. "
        "An answer that gives only the result is wrong output, however correct "
        "that result happens to be.\n\n"
        "ANSWER SKELETON (use these exact labels, each starting a new line):\n"
        "Given: the quantities or facts taken from the question\n"
        "Formula / Principle: the rule being applied, written in LaTeX\n"
        "Working:\n"
        "1. first step, showing the substitution\n"
        "2. next step\n"
        "Final Answer: the value with units, or the concluding statement\n\n"
        "SKELETON RULES (mandatory):\n"
        "- 'Working:' and 'Final Answer:' are ALWAYS present, and 'Final "
        "Answer:' is ALWAYS the last line of the answer.\n"
        "- Drop 'Given:' and 'Formula / Principle:' only when the question is "
        "not quantitative and there is genuinely nothing to put there.\n"
        "- EXCEPTION, type=information: skip the skeleton. Write the fact in "
        "one sentence, then one line starting 'Because:' that names the "
        "definition, rule or source it follows from. If such a question is "
        "also format=mcq, keep that two-line shape but make the last line the "
        "'Final Answer: (X) ...' line required below.\n"
        "- Every numeric step shows the substitution, not just its result: "
        "write '$v = 20 \\times 3 = 60\\,\\mathrm{m\\,s^{-1}}$', never "
        "'$v = 60\\,\\mathrm{m\\,s^{-1}}$'.\n"
        "- Carry units through the working, not only into the final line.\n"
        "- Scale the working to the marks: roughly one numbered step per mark. "
        "A 5-mark question answered in one step is under-worked; a 1-mark "
        "question does not need five.\n"
        "- Name the reason for a step whenever it is not self-evident ('by "
        "conservation of momentum', 'differentiating both sides').\n\n"
        "MCQ QUESTIONS (format=mcq):\n"
        "- The four choices are listed under the question, lettered (A) to (D), "
        "with the paper's official key flagged '[MARKED CORRECT]'.\n"
        "- Work toward one of the listed choices and end with its letter AND "
        "its text: 'Final Answer: (B) $3.2\\,\\mathrm{m\\,s^{-1}}$'.\n"
        "- Where a distractor is a likely student mistake, add one line saying "
        "what mistake produces it.\n"
        "- The marked choice is the paper's official key. If your own working "
        "genuinely leads elsewhere, do NOT bend the working to reach the marked "
        "choice. Show the honest derivation and add a last line: 'Note: this "
        "working gives (C), which differs from the marked answer (B).'\n\n"
        "FORMAT:\n"
        "- Plain text with real line breaks. Numbered steps are '1.', '2.', '3.'.\n"
        "- No markdown headings or bold, but you may use bullet points ('- ') inside steps.\n"
        "- Each answer must directly address the specific question.\n"
        "- Wrap any mathematical expression (integral, vector, fraction, derivative, sum, "
        "Greek letter, matrix, square root, units with exponent, degrees) in LaTeX "
        "delimiters: '$...$' inline or '$$...$$' for display. Use only KaTeX / "
        "matplotlib-mathtext compatible commands. Prefer '\\mathrm{...}' over '\\text{...}'. "
        "Do NOT output raw Unicode superscripts/subscripts or '°' — always use LaTeX "
        "('$9.8\\,\\mathrm{m\\,s^{-2}}$', '$60^\\circ$', '$\\vec{F}=m\\vec{a}$', "
        "'$\\int_0^1 x^2\\, dx = \\tfrac{1}{3}$').\n\n"
        f"{feedback_block}"
        "Return ONLY strict JSON of the form:\n"
        "{\n"
        '  "answers": [ { "question_id": "...", "answer": "..." } ]\n'
        "}\n\n"
        "The answers array MUST contain exactly one entry per question in the input, "
        "matched by question_id. Do not invent question_ids not in the input.\n\n"
        f"=== TEXTBOOK CONTEXT (style & scope reference) ===\n{context_excerpt[:6000]}\n=== END ===\n\n"
        f"=== QUESTIONS (answer each) ===\n{_render_questions(questions_payload)}\n=== END ==="
    )


_MCQ_LETTERS = "ABCDEFGH"


def _render_questions(questions: list) -> str:
    """Render one block per question for the solution prompt.

    MCQ items carry their choices in a separate `options` array rather than
    inline in the question text, so they are printed here under the question
    with the same (A)-(D) lettering the student saw, and the paper's stored key
    flagged. Without that the solver cannot reason over the actual distractors
    and cannot name the option it lands on. Non-MCQ items render as before.
    """
    lines = []
    for q in questions:
        head = f"[{q['id']}] type={q.get('type','concept')} marks={q.get('marks',2)}"
        fmt = (q.get("format") or "").strip()
        if fmt:
            head += f" format={fmt}"
        block = f"{head}\n{q.get('question','')}"

        opts = q.get("options") or []
        if opts:
            key = q.get("correct_option")
            choices = []
            for i, opt in enumerate(opts):
                letter = _MCQ_LETTERS[i] if i < len(_MCQ_LETTERS) else str(i + 1)
                flag = "   [MARKED CORRECT]" if i == key else ""
                choices.append(f"({letter}) {opt}{flag}")
            block += "\n" + "\n".join(choices)

        lines.append(block)
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# LaTeX repair
# ---------------------------------------------------------------------------
# Used by latex_repair.py, and ONLY for items the deterministic pass could not
# fix on its own. The instructions are deliberately narrow: this model is a
# proofreader with no licence to think about the subject matter. Whatever it
# returns is re-checked by latex_repair._accept() before it may replace the
# original, so the prompt and the guardrail say the same thing twice on purpose.
LATEX_REPAIR_SYSTEM = (
    "You repair broken LaTeX markup in exam questions and answer keys. "
    "You are a formatting tool, not a teacher and not an editor.\n\n"
    "ABSOLUTE RULES:\n"
    "- Never change the mathematics. Every number, variable, unit, operator "
    "and result must survive exactly as it was.\n"
    "- Never change the wording, the reasoning, the ordering or the length of "
    "the prose. Do not improve, shorten, expand or rephrase anything.\n"
    "- Never answer the question or comment on whether it is correct.\n"
    "- Change ONLY the markup needed to make the text render under KaTeX.\n"
    "- If an item is already fine, return it byte-for-byte unchanged.\n\n"
    "A reply that alters content is discarded outright, so the safe move when "
    "you are unsure is to return the input unchanged."
)


def latex_repair_prompt(items: list) -> str:
    """Build one batched repair request.

    `items` is a list of {"id", "text", "issues"}. The issue codes come from
    latex_repair.validate() and are passed through so the model is told what was
    detected rather than having to rediscover it.
    """
    payload = json.dumps(
        [
            {
                "id": str(it.get("id", "")),
                "issues": it.get("issues") or [],
                "text": it.get("text", ""),
            }
            for it in items
        ],
        ensure_ascii=False,
        indent=1,
    )
    return (
        "Each item below contains LaTeX that fails to render. Repair the markup "
        "only.\n\n"
        "WHAT THE CHECKER FOUND (the issue codes on each item):\n"
        "- unbalanced_delims: a '$' or '$$' was opened and never closed.\n"
        "- unbalanced_braces: '{' and '}' do not match inside a math span.\n"
        "- bad_arity: a command is missing a required argument, e.g. "
        "'\\frac{1}{2' or '\\sqrt' with nothing after it.\n"
        "- unknown_command: a command KaTeX does not support. Replace it with "
        "the nearest supported equivalent, or with plain text if there is none.\n"
        "- raw_latex_outside_math: a LaTeX command is sitting in prose with no "
        "'$...$' around it. Wrap it.\n"
        "- unicode_math_leak: a raw Unicode symbol that belongs in math, such as "
        "a degree sign, a superscript digit or a Greek letter. Convert it to its "
        "LaTeX spelling inside '$...$'.\n"
        "- empty_math: an empty '$$' or '$ $'. Remove the delimiters.\n\n"
        "HOUSE STYLE:\n"
        "- Inline math uses '$...$', display math uses '$$...$$'. Never use "
        "'\\(', '\\)', '\\[' or '\\]'.\n"
        "- Prefer '\\mathrm{...}' over '\\text{...}' for units and words inside "
        "math.\n"
        "- A literal currency dollar in prose is written '\\$'.\n"
        "- Keep every command inside the KaTeX supported set.\n\n"
        "Return ONLY strict JSON, one entry per input item, with the same ids:\n"
        "{\n"
        '  "fixes": [ { "id": "...", "text": "..." } ]\n'
        "}\n\n"
        f"=== ITEMS ===\n{payload}\n=== END ==="
    )
