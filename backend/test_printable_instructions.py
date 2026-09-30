from export_utils import printable_instructions
from section_blueprints import catalog, printable_template_instructions


def test_math_instructions_are_candidate_facing():
    row = next(row for row in catalog() if row['id'] == 'cbse10-mathematics-standard')
    assert '38 questions' in row['student_instructions']
    assert 'template' not in row['student_instructions'].lower()
    assert 'Internal choices' in row['student_instructions']


def test_old_placeholder_snapshot_is_repaired_for_display_and_export():
    old = 'Read the following instructions carefully. This is the CBSE10 Mathematics Standard template for 2026-27. Follow the section-wise choices and marks printed below.'
    paper = {'generation_requirements': {'blueprint': {
        'id': 'cbse10-mathematics-standard', 'student_instructions': old}}}
    template = printable_template_instructions(paper)
    assert '38 questions' in printable_instructions(old, template_instructions=template)
    assert printable_instructions('Teacher directions.', template_instructions=template) == 'Teacher directions.'


def test_custom_snapshot_instructions_are_preserved():
    paper = {'generation_requirements': {'blueprint': {
        'id': 'custom', 'student_instructions': 'Attempt any three questions.'}}}
    assert printable_template_instructions(paper) == 'Attempt any three questions.'
