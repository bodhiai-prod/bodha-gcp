from io import BytesIO
from zipfile import ZipFile

from PIL import Image

from docx_utils import render_paper_docx, render_solution_docx
from export_utils import DEFAULT_INSTRUCTIONS, canonical_question_type, format_mark, hydrate_question_types, printable_instructions


def _paper():
    return {
        "title": "Physics Test",
        "school_name": "St. John's",
        "subject": "Physics",
        "class_name": "12",
        "total_marks": 10,
        "duration_minutes": 30,
        "instructions": "Use $g=9.8$ where needed.",
        "sections": [
            {
                "title": "Section A",
                "questions": [
                    {
                        "id": "q1",
                        "question": "Solve $$\\frac{x^2 + 1}{\\sqrt{2}} = 0$$.",
                        "marks": 5,
                        "type": "concept",
                        "format": "mcq",
                        "difficulty": "medium",
                        "options": ["One", "Two"],
                        "diagram_path": "diagram.png",
                    }
                ],
            }
        ],
    }


def _png_bytes():
    image = Image.new("RGB", (32, 24), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_docx_preserves_structure_as_native_word_equations():
    data = render_paper_docx(_paper(), diagram_loader=lambda *_: None)

    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "Physics Test" in document_xml
    assert "Section A" in document_xml
    assert "<m:oMath>" in document_xml
    assert "<m:sSup>" in document_xml
    assert "<m:f>" in document_xml
    assert "<m:num>" in document_xml
    assert "<m:den>" in document_xml
    assert "<m:rad>" in document_xml
    assert "Equation (LaTeX)" not in document_xml
    assert "x" in document_xml
    assert document_xml.count("Q1.") == 1
    assert document_xml.count("[5]") == 1
    assert "application" not in document_xml
    assert "long answer" not in document_xml
    assert "hard" not in document_xml
    assert "Diagram unavailable" in document_xml
    assert document_xml.count("Solve ") == 1
    assert "<m:oMathPara>" not in document_xml


def test_docx_renders_latex_matrices_as_native_word_matrices():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"For $A=\begin{pmatrix}2 & 1 \\ 1 & 1\end{pmatrix}$, find $\det A$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "<m:m>" in document_xml
    assert document_xml.count("<m:mr>") == 2
    assert "<m:d>" in document_xml
    assert "<m:begChr m:val=\"(\"" in document_xml


def test_docx_renders_integrals_and_summations_as_native_nary_math():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"Evaluate $\int_0^1 x\,dx$ and $$\sum_{k=1}^{n} k.$$"
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert document_xml.count("<m:nary>") == 2
    assert '<m:chr m:val="∫"' in document_xml
    assert '<m:chr m:val="∑"' in document_xml
    assert "<m:oMathPara>" not in document_xml


def test_docx_renders_blackboard_bold_and_common_set_commands_without_raw_latex():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"For $f:\mathbb{R}\to\mathbb{R}$, find $\mathbb{R}\setminus\{-2,2\}$ "
        r"and $1\mapsto a$; evaluate $\int \left(2x+3x^{-2}\right)dx$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "mathbb" not in document_xml
    assert "setminus" not in document_xml
    assert "mapsto" not in document_xml
    assert "&gt;left&lt;" not in document_xml
    assert "ℝ" in document_xml
    assert "∖" in document_xml
    assert "↦" in document_xml
    assert "<m:nary>" in document_xml
    assert '<m:subHide m:val="1"' in document_xml
    assert '<m:supHide m:val="1"' in document_xml


def test_docx_renders_template_section_instruction_before_question_number():
    paper = _paper()
    paper["generation_requirements"] = {"blueprint": {
        "id": "cbse12",
        "sections": [{"id": "A"}],
    }}
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Read the following instructions very carefully" in document_xml
    assert document_xml.index("This section comprises 20 multiple choice questions") < document_xml.index("Q1.")


def test_docx_embeds_available_diagrams():
    data = render_paper_docx(_paper(), diagram_loader=lambda *_: _png_bytes())

    with ZipFile(BytesIO(data)) as archive:
        media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert len(media_files) == 1
    assert 'wp:extent cx="3886200" cy="2914650"' in document_xml
    with ZipFile(BytesIO(data)) as archive:
        image = Image.open(BytesIO(archive.read(media_files[0])))
        assert image.mode == "L"


def test_docx_ignores_invalid_header_image():
    paper = {**_paper(), "header_image_path": "header.png"}
    data = render_paper_docx(paper, branding_loader=lambda *_: b"not an image")

    assert data.startswith(b"PK")


def test_docx_keeps_placeholder_for_pending_diagram():
    paper = _paper()
    question = paper["sections"][0]["questions"][0]
    question.pop("diagram_path")
    question["needs_diagram"] = True
    question["diagram_status"] = "pending"

    data = render_paper_docx(paper)

    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "Diagram unavailable" in document_xml


def test_marks_instructions_and_cases_are_normalized():
    paper = _paper()
    paper["total_marks"] = 10.0
    paper["custom_instructions"] = ""
    paper["instructions"] = "Ensure proper LaTeX formatting."
    question = paper["sections"][0]["questions"][0]
    question["marks"] = 5.0
    question["question"] = (
        r"Study $$f(n)=\begin{cases}\frac{n+1}{2}, & n \text{ odd} \\ "
        r"\frac{n}{2}, & n \text{ even}\end{cases}$$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert DEFAULT_INSTRUCTIONS in document_xml
    assert "[5]" in document_xml
    assert "[5.0]" not in document_xml
    assert "Total Marks: 10.0" not in document_xml
    assert r"\begin" not in document_xml
    assert r"\end" not in document_xml
    assert ">cases<" not in document_xml


def test_solution_docx_contains_answers_and_monochrome_heading():
    paper = _paper()
    paper["solution"] = {
        "sections": [{"title": "Section A", "answers": [
            {"question_id": "q1", "answer": "The answer is $2$."},
        ]}]
    }
    data = render_solution_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Physics Test" in document_xml
    assert "ANSWER KEY" in document_xml
    assert "The answer is " in document_xml
    assert "002FA7" not in document_xml
    # Answer text stays in the same question table rather than beginning at
    # the document margin in a separate paragraph.  The document now has a
    # separate header rule table, so locate the question block by its label.
    question_table_start = document_xml.rfind("<w:tbl>", 0, document_xml.index("Answer"))
    question_table_end = document_xml.index("</w:tbl>", question_table_start)
    question_table_xml = document_xml[question_table_start:question_table_end]
    assert "Answer" in question_table_xml
    assert '<w:jc w:val="center"' in question_table_xml
    assert '<w:vAlign w:val="top"' in question_table_xml


def test_export_text_helpers():
    assert format_mark(2.0) == "2"
    assert format_mark(2.5) == "2.5"
    assert printable_instructions("Ensure proper LaTeX formatting.") == DEFAULT_INSTRUCTIONS
    assert printable_instructions(
        "Show working. Ensure proper LaTeX formatting.",
        custom_instructions="Show working.",
    ) == DEFAULT_INSTRUCTIONS
    assert printable_instructions("", template_instructions="Board instruction.") == "Board instruction."
    assert canonical_question_type("problem") == "concept"
    assert canonical_question_type("numerical") == "application"
    legacy = {"sections": [{"questions": [{"type": "problem"}]}]}
    hydrate_question_types(legacy)
    assert legacy["sections"][0]["questions"][0]["type"] == "concept"
