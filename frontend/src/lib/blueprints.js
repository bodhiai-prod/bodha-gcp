// Board / class paper-blueprint presets for the New Paper screen.
//
// Each template is literal prose that the backend hands to the LLM verbatim
// (see the blueprint_block in backend/prompts.py), so the wording IS the
// contract: section titles, mark arithmetic and internal-choice tallies are
// reproduced exactly as written here. Section titles in particular must stay
// stable — workers.merge_topup_sections matches top-up questions to existing
// sections by lowercased title.
//
// Two placeholders are substituted at click time by fillBlueprint():
//   {SUBJECT} - subject of the first selected textbook, upper-cased
//   {YEAR}    - current calendar year
// Everything else is deliberately subject-agnostic: the templates describe the
// ROLE each question plays, never a specific chapter, and defer the actual
// chapter list to the weighted topic block the backend injects above them.

export const SUBJECT_TOKEN = "{SUBJECT}";

const ICSE_CLASS_10 = `Section A (40 marks, compulsory — answer ALL)
- Q1: 15 multiple-choice questions, 1 mark each. Each MCQ must have 4 options labelled (a)-(d). Cover the full topic list.
- Q2: 6 fill-in-the-blank questions, 1 mark each. Provide a short word-bank in brackets where useful.
- Q3: 4-5 short-answer parts (i)-(v), totalling 19 marks. Mix of definitions, reasoning, simple numerical, and graph/figure interpretation.

Section B (40 marks) — Attempt any FOUR out of the following SIX questions
- Q4 to Q9: 10 marks each, every question subdivided into (a) 3 marks + (b) 3 marks + (c) 4 marks.
- Each question covers ONE main topic in depth and mixes conceptual, derivation, and numerical parts.
- Section title MUST literally read: "Section B (40 Marks) — Attempt any FOUR of the following SIX questions".

Total: 80 marks, 2 hours. Print "[15 minutes reading time, no writing allowed]" in the instructions line.`;

const CBSE_CLASS_10 = `Section A (16 marks) — 16 MCQs of 1 mark each (4 options).
Section B (10 marks) — 5 Very Short Answer questions of 2 marks each.
Section C (21 marks) — 7 Short Answer questions of 3 marks each.
Section D (20 marks) — 4 Long Answer questions of 5 marks each (internal choice in 2 of them).
Section E (12 marks) — 3 Case-Study/Source-Based questions of 4 marks each, each with sub-parts (a) 1m + (b) 1m + (c) 2m.

Total: 80 marks, 3 hours.`;

const ISC_CLASS_12 = `Generate a complete ISC Class 12 {SUBJECT} examination paper in the format below.
Output English only.

=== HEADER ===
Print exactly:
  ISC EXAMINATION PAPER – {YEAR}
  {SUBJECT}
  Class – 12th
  Maximum Marks: 80    Time Allotted: Three Hours    Reading Time: Additional Fifteen Minutes

=== INSTRUCTIONS TO CANDIDATES ===
Print these thirteen numbered instructions:
  1.  You are allowed an additional fifteen minutes for only reading the paper.
  2.  You must NOT start writing during reading time.
  3.  A blank page is provided at the end of the Question Paper for rough work.
  4.  The Question Paper is divided into three sections and has 22 questions in all.
  5.  Section A is compulsory and has fourteen questions.
  6.  You are required to attempt all questions either from Section B or Section C.
  7.  Section B and Section C have four questions each.
  8.  Internal choices have been provided in two questions of 2 marks, two questions of
      4 marks and two questions of 6 marks in Section A.
  9.  Internal choices have been provided in one question of 2 marks and one question of
      4 marks each in Section B and Section C.
  10. While attempting Multiple Choice Questions in Section A, B and C, you are required to
      write only ONE option as the answer.
  11. All workings, including rough work, should be done on the same page as, and adjacent to,
      the rest of the answer.
  12. Any tables, data booklets, maps or graph papers this subject requires are provided.
  13. The intended marks for questions or parts of questions are given in the brackets [].

=== SECTION – A (65 MARKS) ===
Header must literally read: "SECTION−A (65 MARKS)"
Contains Question 1 to Question 14. Marks: 15 + (5×2) + (4×4) + (4×6) = 65.

--- Question 1 [15 × 1] ---
Print the stem exactly:
  "In subparts (i) to (xi) choose the correct options and in subparts (xii) to (xv), answer
   the questions as instructed."
Fifteen subparts (i)–(xv), each carrying [1] printed at the right.

  Subparts (i)–(xi) are OBJECTIVE, options labelled (a) (b) (c) (d), laid out two per line
  for short options. Mandatory composition within these eleven:
    - At least TWO plain single-step items: one straight recall of a definition, term, law or
      fact, and one needing a single computation, classification or one-line application.
    - EXACTLY TWO ASSERTION–REASON subparts. Print "Assertion:" and "Reason:" on separate
      lines, then these four options verbatim:
        (a) Both Assertion and Reason are true and Reason is the correct explanation for Assertion.
        (b) Both Assertion and Reason are true but Reason is not the correct explanation for Assertion.
        (c) Assertion is true and Reason is false.
        (d) Both Assertion and Reason are false.
      Keep the four wordings identical across both subparts. The Reason must always be the
      general principle and the Assertion a particular instance of it — never two unrelated facts.
    - EXACTLY TWO STATEMENT-PAIR subparts. Print "Statement 1:" and "Statement 2:" on separate
      lines, then the stem "Which of the following is correct with respect to the above
      statements?" and these four options verbatim:
        (a) Statement 1 is true and Statement 2 is false.
        (b) Statement 2 is true and Statement 1 is false.
        (c) Both the statements are true.
        (d) Both the statements are false.
    - At least ONE FIGURE-BASED subpart: a graph, diagram, map, circuit, structure or plot with
      marked points, followed by a question answerable only by reading the figure (a trend, a
      turning point, a labelled part, or a value read off it). Set needs_diagram true and supply
      a full diagram_spec for it.
    - At least ONE TABLE-BASED subpart: a small two- or three-row table of supplied data, then a
      question that depends on that table.
    - At least ONE applied subpart set in a named real-world context, supplying every figure the
      candidate needs.

  Subparts (xii)–(xv) are VERY SHORT ANSWER, no options. One-line answers only.
  Typical verbs: "Write …", "State …", "Name …", "For what value of … ", "Give one reason why …",
  "Find …". Each still carries [1].

--- Questions 2 to 6 — [2] marks each ---
Single short tasks answerable in 3–5 lines: state and briefly justify a result, work one short
computation, complete a one-step derivation, identify and explain a case, or verify a stated
claim against given data.
EXACTLY TWO of these five carry an internal choice, printed as:
     (i)  <question>
                        OR
     (ii) <alternative of the same marks>
The remaining three are printed as plain stems with no (i)/(ii) labels.
ONE of the five must be a REAL-WORLD DATA question: a 60–100 word stem naming three people,
agents, samples or sources with three quantities each plus a per-unit rate, property or price
list, then two sub-parts: "(i) Represent/organise the above information …" and
"(ii) Find/determine the value of …".

--- Questions 7 to 10 — [4] marks each ---
Multi-step tasks. Include across the four:
  - ONE proof, derivation or formal justification of a general result stated in the stem.
  - ONE multi-step working question that requires a change of method or technique partway
    through.
  - ONE question that establishes a relationship, or shows that a stated condition holds under
    the given constraints.
  - ONE applied question with a real-world stem (people, objects, samples, transactions) split
    into sub-parts (i) [2] and (ii) [2].
  - Optionally ONE figure-reading alternative: a printed graph, diagram or data plot with two
    labelled points A and B, then (a) a one-line read-off [1], (b) identify or compute the two
    labelled quantities [2], (c) compare or order three stated values [1].
EXACTLY TWO of these four carry an internal choice in the (i) / OR / (ii) form above.

--- Questions 11 to 14 — [6] marks each ---
The heaviest questions. Include across the four:
  - ONE extended question that requires a standard property, theorem, law or principle of the
    subject to be named and then applied through to a result.
  - ONE question solved for a particular case from initial, boundary or starting conditions that
    are supplied in full in the stem.
  - ONE COMPARISON question built on two supplied figures, data sets or scenarios labelled
    "Case 1" and "Case 2" (or "Graph 1" and "Graph 2"), then four sub-parts: (i) [1] identify
    which is which, (ii) [1] state the range, scope or conditions of one of them, (iii) [2]
    prove or justify a stated relation between them, (iv) [2] evaluate or apply the result.
  - ONE SYNTHESIS question with an 80–130 word real-world stem: three agents, machines, sources
    or groups with their stated rates and stated allocations, ending in a single "what is / find
    the …" question. No sub-parts.
EXACTLY TWO of these four carry an internal choice in the (i) / OR / (ii) form above.

Internal-choice tally for Section A must be EXACTLY: two 2-mark, two 4-mark, two 6-mark
questions — six OR blocks, no more, no fewer.

=== SECTION – B (15 MARKS) ===
Header must literally read: "SECTION−B (15 MARKS)"
Four questions, Question 15 to Question 18.
Section B is the FIRST OPTIONAL STREAM. Choose ONE coherent group of chapters from the topic
list given above — chapters that naturally belong together — and build ALL FOUR Section B
questions from that group only. Name the group in the opening line of the section.

  Question 15 [5] — mixed objective/short block. Print the stem:
    "In subparts (i) and (ii) choose the correct options and in subparts (iii) and (iv),
     answer the questions as instructed."
      (i)   [1] Assertion–Reason on a core result of this stream, using the same four options
            as Section A.
      (ii)  [1] MCQ, options (a)–(d), on a single-step comparison or relation.
      (iii) [1] a one-line "Show that …" / "State whether …" with a one-line justification.
      (iv)  [2] a real-world stem for this stream (people, machines, animals, sites) with an
            illustrated figure, split into (a) [1] restate or convert the given information into
            the standard form and (b) [1] extend it to a second, related case.
  Question 16 [2] — a single short task from this stream. CARRIES the internal choice.
  Question 17 [4] — a three-part structured question on given data:
       (i) [1] identify or state the quantities involved, (ii) [1] derive one of them in a single
       step, (iii) [2] combine the two into the final result.
  Question 18 [4] — an extended task from this stream. CARRIES the internal choice:
       (i) split as (a) rough sketch, outline or plan [1], (b) the main working [2],
           (c) hence state the complete result [1]
                        OR
       (ii) a single self-contained 4-mark task on the same chapters [4].

Internal-choice tally for Section B: EXACTLY one 2-mark question and one 4-mark question.

=== SECTION – C (15 MARKS) ===
Header must literally read: "SECTION−C (15 MARKS)"
Four questions, Question 19 to Question 22.
Section C is the SECOND OPTIONAL STREAM. Choose a group of chapters from the topic list that is
DISJOINT from the Section B group — no chapter may appear in both sections — and build all four
Section C questions from that group only. Name the group in the opening line of the section.

  Question 19 [5] — mixed objective/short block. Print the stem:
    "In subparts (i) and (ii) choose the correct options and in subpart (iii), answer the
     questions as instructed."
      (i)   [1] MCQ on a key measure, rate or index of this stream, options (a)–(d).
      (ii)  [1] a FOUR-STATEMENT MCQ: statements labelled (I), (II), (III), (IV) about the
            properties of that measure, with combination options
            ("Only (I) and (IV) are correct.").
      (iii) [3] a supplied data table with two filled rows and two blank rows the candidate must
            complete; sub-parts (a) [1] compute the summary values, (b) [1] complete the table,
            (c) [1] compute the final quantity from it.
  Question 20 [2] — a short task on the same measure. CARRIES the internal choice:
       (i) compute the result from a set of supplied summary figures
                        OR
       (ii) given two stated results, identify which one applies and justify it in one line.
  Question 21 [4] — an applied question from this stream. CARRIES the internal choice:
       (i) a stated real-world scenario with a supplied relation and a supplied cost, rate or
           constraint; (a) [1] write the first derived function or quantity, (b) [1] write the
           second, (c) [2] find the value that optimises or satisfies the stated goal
                        OR
       (ii) a stated aggregate quantity is given; (a) [1] recover the base quantity, (b) [1]
            find its rate of change, (c) [1] find the related average rate, (d) [1] verify the
            standard relation between the three.
  Question 22 [4] — an extended applied word problem with two item types and two resource
       constraints stated in words (time/cost, weight/space, area/budget, labour/material),
       ending with a single instruction to formulate the problem and solve or justify it fully.
       No sub-parts, no internal choice.

Internal-choice tally for Section C: EXACTLY one 2-mark question and one 4-mark question.

=== GLOBAL RULES ===
- Question numbering is continuous 1 → 22 across all three sections. Never restart.
- Marks are printed in square brackets right-aligned at the question or sub-part level: [1],
  [2], [4], [6]. The subparts of Question 1 each carry [1].
- Section B and Section C are ALTERNATIVES to each other, not sequential. They must be
  independently complete, cover disjoint chapters, and total 15 marks each.
- Internal choice appears in EXACTLY ten places in the whole paper: six in Section A
  (2+2+2 by mark value), two in Section B, two in Section C. Verify before output.
- The word OR is centred, bold, on its own line, between (i) and (ii). Both alternatives must
  be worth the same marks and come from the same or adjacent chapters.
- Sub-part labels are roman (i) (ii) (iii) …; options are lower-case (a) (b) (c) (d);
  nested sub-parts inside a numbered part use (a) (b) (c).
- Every question is self-contained: no question may depend on another question's answer, and
  all constants, data, extracts and initial conditions must be supplied in the stem.
- Real-world stems must name plausible people, businesses, places or objects and supply every
  figure the candidate needs — no outside assumptions.
- Numbers, where the subject uses them, must be chosen so answers come out clean (integers,
  simple surds, simple multiples of π, or terminating decimals).
- If this subject is quantitative, typeset the mathematics properly: stacked fractions, integrals
  with limits, matrices and determinants in the correct brackets, vectors with over-arrows, unit
  vectors with hats, Σ notation for sums, and the correct currency symbol. If it is not a
  quantitative subject, ignore this rule entirely.
- No two questions in the paper may test the same result, technique or extract.
- Draw every chapter from the weighted topic list given above, in proportion to the stated
  weights. Any chapter with a weight above zero must appear at least once.
- Verify before output: subparts of Question 1 = 15; Section A questions = 14 and = 65 marks;
  Section B = 4 questions and 15 marks; Section C = 4 questions and 15 marks;
  total questions = 22; total marks = 80.`;

const CBSE_CLASS_12 = `Generate a complete CBSE Class 12 {SUBJECT} examination paper in the format below.
Output English only.

=== HEADER ===
Print exactly:
  {SUBJECT}
  Time allowed : 3 hours                    Maximum Marks : 80
Include a Q.P. Code line and a Roll No. field placeholder at the top.

=== GENERAL INSTRUCTIONS ===
Print the line "Read the following instructions very carefully and strictly follow them :"
then these nine roman-numbered instructions verbatim:
  (i)   This question paper contains 38 questions. All questions are compulsory.
  (ii)  This question paper is divided into five Sections – A, B, C, D and E.
  (iii) In Section A, Questions no. 1 to 18 are multiple choice questions (MCQs) and
        questions number 19 and 20 are Assertion-Reason based questions of 1 mark each.
  (iv)  In Section B, Questions no. 21 to 25 are very short answer (VSA) type questions,
        carrying 2 marks each.
  (v)   In Section C, Questions no. 26 to 31 are short answer (SA) type questions,
        carrying 3 marks each.
  (vi)  In Section D, Questions no. 32 to 35 are long answer (LA) type questions
        carrying 5 marks each.
  (vii) In Section E, Questions no. 36 to 38 are case study based questions carrying
        4 marks each.
  (viii)There is no overall choice. However, an internal choice has been provided in
        2 questions in Section B, 3 questions in Section C, 2 questions in Section D and
        3 questions in Section E.
  (ix)  Use of calculator is not allowed.

=== SECTION A  (Questions 1–20, 20 marks) ===
Header line: "SECTION A"
Sub-line in italics: "This section comprises 20 multiple choice questions (MCQs) of 1 mark each."

Questions 1 to 18 — MCQs, options labelled (A), (B), (C), (D).
  - Stem always ends with a colon: "… is :" / "… is equal to :" / "… is/are :".
  - Short numeric or one-word options are laid out TWO PER LINE: (A) … (B) … / (C) … (D) …
    Long prose options are laid out one per line.
  - Every MCQ must be a one-step solvable item — a value, a term, an interval, a date, a
    classification, a cause, or a closed form. Never "which of the following statements is
    true" essays.
  - Distractors must be the results of plausible specific errors a student actually makes
    (a sign slip, a confused pair of terms, a reversed cause and effect, an adjacent value),
    not random filler.
  - Distribute Questions 1 to 18 across the chapters in the weighted topic list given above, in
    proportion to their stated weights, and print them in SYLLABUS ORDER — the order the
    chapters are listed above — not in order of difficulty. No single chapter may supply more
    than 3 of the 18.

Questions 19 and 20 — ASSERTION–REASON.
  Before Question 19 print this lead-in paragraph in italics:
    "Questions number 19 and 20 are Assertion and Reason based questions. Two statements are
     given, one labelled as Assertion (A) and the other labelled as Reason (R). Select the
     correct answer from the codes (A), (B), (C) and (D) as given below."
  Then these four codes verbatim, each on its own line:
    (A) Both Assertion (A) and Reason (R) are true and Reason (R) is the correct explanation
        of the Assertion (A).
    (B) Both Assertion (A) and Reason (R) are true, but Reason (R) is not the correct
        explanation of the Assertion (A).
    (C) Assertion (A) is true, but Reason (R) is false.
    (D) Assertion (A) is false, but Reason (R) is true.
  Then Questions 19 and 20, each printed as two labelled lines:
    "Assertion (A) :   <a specific instance>"
    "Reason (R) :      <the general theorem, law, principle or definition the instance is
                        tested against>"
  The Reason must always be a general statement and the Assertion a particular case of it —
  never two unrelated facts. The codes are NOT reprinted under each question.

=== SECTION B  (Questions 21–25, 2 marks each, 10 marks) ===
Header: "SECTION B"
Sub-line: "This section comprises 5 Very Short Answer (VSA) type questions of 2 marks each."
Each question is a single self-contained task answerable in 2–4 lines. Typical verbs: "Find",
"Evaluate", "Show that", "Solve", "State and explain", "Give reasons for", "Distinguish between".
EXACTLY TWO of the five carry an internal choice, printed as:
    21.  (a)  <question>
                        OR
         (b)  <alternative question of the same marks>
The word OR is centred, bold, on its own line. Both alternatives must come from the SAME or
adjacent chapters and be of comparable length.
The remaining three questions have NO (a)/(b) labelling — they are printed as plain stems.
The five questions must come from five DIFFERENT chapters of the topic list.

=== SECTION C  (Questions 26–31, 3 marks each, 18 marks) ===
Header: "SECTION C"
Sub-line: "This section comprises 6 Short Answer (SA) type questions of 3 marks each."
Each question requires 3–6 lines of working or writing. Typical tasks: a computation needing a
change of method partway through, a short derivation or proof, a cause-and-effect explanation,
a comparison of two related ideas, an interpretation of supplied data, or the deduction of a
relation between stated quantities.
EXACTLY THREE of the six carry an internal choice in the (a) / OR / (b) form above.
The other three are printed as plain stems.
The six questions must come from six DIFFERENT chapters of the topic list, favouring the
heavier-weighted ones.

=== SECTION D  (Questions 32–35, 5 marks each, 20 marks) ===
Header: "SECTION D"
Sub-line: "This section comprises 4 Long Answer (LA) type questions of 5 marks each."
Each question is a multi-step problem or an extended written answer that has to be planned
before it is written: a full derivation applied to a given case, a complete analysis of a
supplied scenario, a long structured explanation with examples, or a two-part task in which the
second part depends on the first.
EXACTLY TWO of the four carry an internal choice in the (a) / OR / (b) form above.
The four questions must come from the four heaviest-weighted chapters of the topic list — one
chapter each, no chapter used twice.

=== SECTION E  (Questions 36–38, 4 marks each, 12 marks) ===
Header: "SECTION E"
Sub-line: "This section comprises 3 case-study based questions of 4 marks each."
Before each question print a centred label: "Case Study – 1", "Case Study – 2", "Case Study – 3".

Each case study has this fixed anatomy:
  1. A 40–120 word stem — a real-world situation, a short source extract, a described
     experiment, a small data table, or a classroom scenario — that sets up the context and
     defines every term, symbol and figure the sub-parts will use. For a theory-heavy chapter
     the stem may instead state the definition, law or formula that the sub-parts apply.
  2. The line: "Based on the above information, answer the following questions :"
  3. THREE sub-parts, with marks printed in italics at the right margin:
       (i)   ...........................................  1
       (ii)  ...........................................  1
       (iii) (a) .......................................  2
                          OR
       (iii) (b) .......................................  2
     Marks are ALWAYS 1 + 1 + 2. The internal choice is ALWAYS on sub-part (iii) and ONLY on
     (iii) — never on (i) or (ii). Both (iii)(a) and (iii)(b) must be worth 2 and test the same
     skill on different data.
  4. Sub-parts (i) and (ii) must be answerable in one or two lines and must not depend on
     each other's answers; (iii) is the harder extension.
All three case studies carry the internal choice — none is exempt.
Use three DIFFERENT chapters from the topic list for the three case studies, and pick chapters
that lend themselves to a real-world or source-based stem.

=== GLOBAL RULES ===
- Question numbering is continuous 1 → 38 across all five sections. Never restart.
- Section headers are exactly "SECTION A" … "SECTION E", each followed by its italic
  sub-line stating the count, type, and marks per question.
- Marks are NOT printed next to questions in Sections A–D (the section sub-line carries them).
  Marks ARE printed at the right of each sub-part in Section E only.
- Internal choice count is FIXED and must be exactly: Section A – 0, Section B – 2,
  Section C – 3, Section D – 2, Section E – 3. Ten OR blocks in the whole paper, no more,
  no fewer. Verify this before output.
- Option labels are capital (A) (B) (C) (D) in Section A; internal-choice labels are lower-case
  (a) / (b); sub-part labels are roman (i) (ii) (iii).
- Every question must be self-contained: no question may reference the answer to another
  question, and no data, extract or constant may be assumed from outside the stem.
- No two questions in the paper may test the same result, technique or extract.
- Where the subject uses numbers, choose them so answers come out clean (integers, simple surds,
  or simple multiples of π) — no unresolvable decimals.
- If this subject is quantitative, typeset the mathematics properly: fractions stacked, integrals
  with limits, matrices in square brackets, vectors with over-arrows and unit vectors with hats,
  ℝ and ℕ for number sets, and Greek letters as parameters. If it is not a quantitative subject,
  ignore this rule entirely.
- Use of calculator is not allowed, so no question may require one.
- Draw every chapter from the weighted topic list given above, in proportion to the stated
  weights. Any chapter with a weight above zero must appear at least once.
- Verify before output: Section A = 20 questions, B = 5, C = 6, D = 4, E = 3;
  total questions = 38; marks = 20 + 10 + 18 + 20 + 12 = 80.`;

// Order here is the order the preset buttons render in.
export const BLUEPRINT_PRESETS = [
  {
    id: "icse10",
    label: "Use ICSE Class 10 (80m / 2h)",
    testId: "blueprint-preset-icse",
    template: ICSE_CLASS_10,
  },
  {
    id: "cbse10",
    label: "Use CBSE Class 10 (80m / 3h)",
    testId: "blueprint-preset-cbse",
    template: CBSE_CLASS_10,
  },
  {
    id: "isc12",
    label: "Use ISC Class 12 (80m / 3h)",
    testId: "blueprint-preset-isc-12",
    template: ISC_CLASS_12,
  },
  {
    id: "cbse12",
    label: "Use CBSE Class 12 (80m / 3h)",
    testId: "blueprint-preset-cbse-12",
    template: CBSE_CLASS_12,
  },
];

/**
 * Resolve a blueprint template into the text that goes in the textarea.
 * {SUBJECT} is replaced by the upper-cased subject when one is known; when it
 * is not, the token is left in place so the teacher can type over it.
 */
export function fillBlueprint(template, { subject, year } = {}) {
  const trimmed = (subject || "").trim();
  let out = template.replace(/\{YEAR\}/g, String(year || new Date().getFullYear()));
  if (trimmed) out = out.replace(/\{SUBJECT\}/g, trimmed.toUpperCase());
  return out;
}
