from io import BytesIO
from zipfile import ZipFile

from PIL import Image

from docx_utils import render_paper_docx


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
    assert "<m:oMathPara>" in document_xml
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


def test_docx_embeds_available_diagrams():
    data = render_paper_docx(_paper(), diagram_loader=lambda *_: _png_bytes())

    with ZipFile(BytesIO(data)) as archive:
        media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert len(media_files) == 1
    assert 'wp:extent cx="3886200" cy="2914650"' in document_xml


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
