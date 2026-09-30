"""Candidate directions adapted from the source PDFs recorded on each preset.

These are board rules in our own wording, not model-generated summaries.
Specimen page counts and examiner-only directions do not apply to generated papers.
"""


def numbered(lines):
    return '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, 1))


def candidate_content(row):
    family = row.get('family')
    subject = str(row.get('subject_variant', '')).lower()
    if family == 'icse10':
        lines = [
            'Write your responses on the separate answer sheets supplied.',
            'Use the first fifteen minutes to read the paper; writing is prohibited during this period.',
            'The duration in the header applies to answering time, excluding the reading period.',
            'Complete Section A. Select and answer any four questions in Section B.',
            'The bracketed numbers beside questions and subparts indicate their marks.',
        ]
        if subject == 'mathematics':
            lines += [
                'Keep calculations and rough work with the corresponding answer on the same sheet.',
                'Include every essential step; missing working can cost marks.',
                'Mathematical tables are supplied for this examination.',
            ]
        return {'instructions': numbered(lines), 'sections': {
            'A': 'Complete every question in this section.',
            'B': 'Select any four questions from this section.',
        }}
    if family not in {'cbse10', 'cbse12'}:
        return None
    if family == 'cbse10' and subject == 'science':
        return {'instructions': numbered([
            'There are 39 questions, arranged in three subject sections.',
            'Section A covers Biology; Section B covers Chemistry; Section C covers Physics.',
            'Every question must be attempted. Where alternatives are offered within a question, select just one.',
        ]), 'sections': {
            'A': 'Biology: complete each question, selecting one alternative where a choice is offered.',
            'B': 'Chemistry: complete each question, selecting one alternative where a choice is offered.',
            'C': 'Physics: complete each question, selecting one alternative where a choice is offered.',
        }}
    if subject.startswith('mathematics'):
        lines = [
            'Answer all 38 questions; none of the five sections A–E may be omitted.',
            'Section A: Q1–18 are multiple-choice items; Q19–20 use assertion and reason. Each is worth 1 mark.',
            'Section B: Q21–25 require very short answers, worth 2 marks apiece.',
            'Section C: Q26–31 require short answers, worth 3 marks apiece.',
            'Section D: Q32–35 require long answers, worth 5 marks apiece.',
            'Section E: Q36–38 are case studies worth 4 marks apiece.',
        ]
        if family == 'cbse10':
            lines += ['Each case study has subparts worth 1, 1 and 2 marks.',
                      'Internal choices occur in two questions apiece in B, C and D, and every 2-mark case-study subpart. Select one alternative at each choice.']
        else:
            lines += ['Internal choices occur in two questions in B, three in C, two in D, and one subpart of two case studies in E. Select one alternative at each choice.']
        lines += ['Provide clear figures when needed. Unless a question specifies otherwise, use π = 22/7.',
                  'Calculators must not be used.']
        sections = {key: line.split(': ', 1)[1] for key, line in zip('ABCDE', lines[1:6])}
        return {'instructions': numbered(lines), 'sections': sections}
    lines = [
        'Answer all 33 questions across the five compulsory sections A–E.',
        'Section A has sixteen objective items worth 1 mark apiece.',
        'Section B has five very short answers worth 2 marks apiece.',
        'Section C has seven short answers worth 3 marks apiece.',
        'Section D has two case studies worth 4 marks apiece.',
        'Section E has three long answers worth 5 marks apiece.',
    ]
    if subject == 'physics':
        lines[1] = 'Section A has twelve MCQs and four assertion–reason items, each worth 1 mark.'
        lines += [
            'Internal choices occur in two questions in B, one in C and all three in E. Select one alternative at each choice; no section may be omitted.',
            'Calculators must not be used.',
            'Use these constants when needed: c = 3 × 10⁸ m/s; mₑ = 9.1 × 10⁻³¹ kg; mₚ = 1.7 × 10⁻²⁷ kg; e = 1.6 × 10⁻¹⁹ C.',
            'Also: μ₀ = 4π × 10⁻⁷ T m/A; h = 6.63 × 10⁻³⁴ J s; ε₀ = 8.854 × 10⁻¹² C²/(N m²); Nₐ = 6.023 × 10²³ mol⁻¹; nuclear radius ≈ 10⁻¹⁵ m.',
        ]
    elif subject == 'chemistry':
        lines += [
            'At each internal choice, answer one alternative only.',
            'For an MCQ, record both the selected option and its answer.',
            'Neither logarithm tables nor calculators may be used.',
        ]
    else:
        lines += [
            'No whole question may be skipped. At each internal choice, answer one alternative only.',
            'Include clear diagrams with labels wherever the answer requires them.',
        ]
    return {'instructions': numbered(lines), 'sections': {
        key: line + ' Complete each question, choosing one alternative where offered.'
        for key, line in zip('ABCDE', lines[1:6])
    }}


def detailed_template_prompt(row):
    """Describe the actual compiled contract instead of an opaque server reference."""
    parts = [
        f"Generate a {row['label']} paper ({row.get('academic_session', '')}).",
        f"HEADER: {row['label']}; maximum {row['total_marks']} marks; writing time {row['duration_minutes']} minutes.",
        'GENERAL INSTRUCTIONS — print the following numbered candidate directions before Section A, without shortening them:\n' + row['student_instructions'],
        'SECTION STRUCTURE — keep the following question numbers, marks, subparts and choices:',
    ]
    for section in row.get('sections', []):
        parts.append(section['title'] + ': ' + section.get('section_instructions', ''))
        for group in section.get('groups', []):
            slots = group.get('slots') or []
            parents = {}
            for slot in slots:
                parents.setdefault(slot.get('parent_label') or slot.get('parent_id'), []).append(slot['marks'])
            if len(parents) > 1 and all(len(marks) == 1 for marks in parents.values()):
                labels = list(parents)
                description = f"{labels[0]} to {labels[-1]}: {len(parents)} questions × {group['marks']} marks"
            elif parents:
                descriptions = []
                for parent, marks in parents.items():
                    if len(marks) == 1:
                        detail = f'{marks[0]} marks'
                    elif len(set(marks)) == 1:
                        detail = f'{len(marks)} subparts × {marks[0]} marks'
                    else:
                        detail = ' + '.join(map(str, marks)) + ' marks in subparts'
                    descriptions.append(f'{parent}: {detail}')
                description = '; '.join(descriptions)
            else:
                description = f"{group.get('label', group['id'])}: {group['count']} questions × {group['marks']} marks"
            formats = {'mcq': 'multiple-choice', 'short_answer': 'short-answer',
                       'long_answer': 'long-answer', 'fill_blank': 'fill-in-the-blank',
                       'true_false': 'true/false'}
            description += f"; format: {formats.get(group.get('format'), group.get('format', ''))}"
            lead_ins = list(dict.fromkeys(slot.get('lead_in') for slot in slots if slot.get('lead_in')))
            if lead_ins:
                description += '; ' + ' '.join(lead_ins)
            choices = group.get('internal_choices', 0)
            parts.append(description + (f'; internal choices in {choices} question(s).' if choices else '.'))
        if section.get('attempt_count'):
            parts.append(f"Attempt {section['attempt_count']} questions from this section.")
        if section.get('choice_set'):
            parts.append('This section is an alternative stream: attempt it or the other section in the same choice set, not both.')
    for pair in row.get('disjoint_section_pairs', []):
        parts.append('Choose non-overlapping chapters for Sections ' + ' and '.join(pair) + '.')
    subject = str(row.get('subject_variant', '')).lower()
    if subject == 'chemistry':
        parts.append('Require balanced chemical equations, correct notation and essential numerical working.')
    elif subject == 'biology':
        parts.append('Use precise biological terminology and clearly labelled diagrams where needed.')
    parts.append('Use continuous question numbering and self-contained, candidate-facing questions. Put section directions under headings, never inside Question 1 or another numbered question. Keep all required internal alternatives and subpart marks. Check the attemptable total against the header.')
    return '\n\n'.join(parts)
