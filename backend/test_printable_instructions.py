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
    assert printable_instructions('Teacher directions.', template_instructions=template, instructions_edited=True) == 'Teacher directions.'


def test_custom_snapshot_instructions_are_preserved():
    paper = {'generation_requirements': {'blueprint': {
        'id': 'custom', 'student_instructions': 'Attempt any three questions.'}}}
    assert printable_template_instructions(paper) == 'Attempt any three questions.'


def test_every_active_preset_has_numbered_directions_and_explicit_prompt():
    for row in catalog():
        if row.get('legacy'):
            continue
        lines = row['student_instructions'].splitlines()
        assert len(lines) >= 3, row['id']
        assert all(line.startswith(f'{i}. ') for i, line in enumerate(lines, 1)), row['id']
        assert row['student_instructions'] in row['template'], row['id']
        assert 'SECTION STRUCTURE' in row['template'], row['id']
        assert 'server-owned' not in row['template'], row['id']
        assert len(row['template']) < 20000, row['id']
        for section in row['sections']:
            assert section['title'] in row['template'], row['id']
            assert section['section_instructions'], row['id']


def test_official_instructions_replace_both_saved_and_snapshot_summaries():
    for row in catalog():
        if row.get('legacy'):
            continue
        paper = {'generation_requirements': {'blueprint': {
            'id': row['id'], 'student_instructions': 'Follow section directions.'}}}
        template = printable_template_instructions(paper)
        assert template == row['student_instructions']
        assert printable_instructions('Answer all questions.', template_instructions=template) == template
        assert printable_instructions('Teacher edit.', template_instructions=template,
                                      instructions_edited=True) == 'Teacher edit.'


def test_subject_specific_rules_are_not_shared_across_boards():
    rows = {row['id']: row for row in catalog()}
    assert 'three in C' in rows['cbse12-mathematics']['student_instructions']
    assert 'two questions apiece in B, C and D' in rows['cbse10-mathematics-standard']['student_instructions']
    assert 'logarithm tables' in rows['cbse12-chemistry']['student_instructions']
    assert 'simple scientific calculator' in rows['isc12-physics']['student_instructions']
    assert 'Calculators must not be used' in rows['cbse12-physics']['student_instructions']
    assert '39 questions' in rows['cbse10-science']['student_instructions']
    assert 'any four' in rows['icse10-mathematics']['student_instructions']
    assert 'either from Section B or Section C' in rows['isc12-mathematics']['student_instructions']


def test_normal_paper_keeps_its_generated_instructions():
    assert printable_instructions('Show your calculations.') == 'Show your calculations.'


def test_pdf_and_docx_use_full_instructions_and_preserve_explicit_edits():
    from copy import deepcopy
    from io import BytesIO
    from zipfile import ZipFile
    from xml.etree import ElementTree
    from pypdf import PdfReader
    from docx_utils import render_paper_docx
    from pdf_utils import render_paper_pdf

    for blueprint_id in ('isc12-mathematics', 'cbse12-mathematics', 'icse10-mathematics'):
        paper = {'title': 'Instruction regression', 'subject': 'Maths', 'class_name': '12',
                 'total_marks': 80, 'duration_minutes': 180, 'sections': [],
                 'instructions': 'MODEL SUMMARY SHOULD NOT BE PRINTED',
                 'generation_requirements': {'blueprint': {'id': blueprint_id,
                      'student_instructions': 'STALE SNAPSHOT SHOULD NOT BE PRINTED'}}}
        expected = printable_template_instructions(paper)
        for edited in (False, True):
            paper['instructions_edited'] = edited
            if edited:
                paper['instructions'] = 'Explicit teacher directions.'
            docx = render_paper_docx(deepcopy(paper))
            with ZipFile(BytesIO(docx)) as archive:
                root = ElementTree.fromstring(archive.read('word/document.xml'))
                text = ' '.join(root.itertext())
            pdf = PdfReader(BytesIO(render_paper_pdf(deepcopy(paper))))
            pdf_text = ' '.join(page.extract_text() for page in pdf.pages)
            for output in (text, pdf_text):
                normalized = ' '.join(output.split())
                assert 'MODEL SUMMARY' not in normalized
                assert 'STALE SNAPSHOT' not in normalized
                if edited:
                    assert 'Explicit teacher directions.' in normalized
                else:
                    for line in expected.splitlines():
                        assert line in normalized, (blueprint_id, line)
