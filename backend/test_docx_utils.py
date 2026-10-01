from io import BytesIO
import re
from zipfile import ZipFile

from PIL import Image

from docx_utils import render_paper_docx, render_solution_docx
from export_utils import canonical_question_type, format_mark, hydrate_question_types, printable_instructions


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


def _png_bytes(width=32, height=24):
    image = Image.new("RGB", (width, height), "white")
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


def test_docx_renders_calligraphic_symbols_and_implication_arrows_without_raw_latex():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"For $P(R)=\frac{\mathcal{E}^2R}{(R+r)^2}$, "
        r"$R=r\implies P_{\max}=\frac{\mathcal{E}^2}{4r}$ and "
        r"$R\iff R-r=0$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "mathcal" not in document_xml
    assert "implies" not in document_xml
    assert "iff" not in document_xml
    assert "ℰ" in document_xml
    assert "⇒" in document_xml
    assert "⇔" in document_xml


def test_docx_math_edge_case_fixture_has_no_leaked_latex_commands():
    """Cover the native-math commands commonly emitted in physics solutions."""
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"$\hbar\omega=\mathcal{E}$; $R=r\implies P_{\max}=\frac{\mathcal{E}^2}{4r}$; "
        r"$A\iff B$, $\sin\theta+\cos\theta$, $\limsup_{n\to\infty}a_n$, "
        r"$\overline{AB}+\dot{x}+\ddot{y}+\widetilde{F}$, "
        r"$\mathfrak{g}+\mathsf{v}+\mathtt{T}+\textsf{unit}$, "
        r"$\hbar\ell\Re z\Im z\rightleftharpoons\aleph$, $\boxed{E=mc^2}$, "
        r"$\langle x\rangle\not\in\lfloor 0,1\rfloor$, $\coprod_n A_n$, and $\nexists x\in\square$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    for leaked in (
        "mathcal", "implies", "iff", "overline", "widetilde",
        "mathfrak", "mathsf", "mathtt", "textsf", "hbar", "rightleftharpoons", "nexists",
        "boxed", "langle", "rangle", "lfloor", "rfloor", "coprod",
    ):
        assert f"<m:t>{leaked}</m:t>" not in document_xml
    for symbol in ("ℰ", "⇒", "⇔", "ℏ", "ℓ", "ℜ", "ℑ", "⇌", "∄", "□", "⟨", "⌊", "∐", "∉"):
        assert symbol in document_xml
    assert document_xml.count("<m:acc>") >= 2
    assert "<m:groupChr>" in document_xml
    assert document_xml.count('<m:sty m:val="p"') >= 3


def test_docx_renders_physics_symbols_and_upright_si_units():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"At $180^\circ\mathrm{C}$, $\rho=0.90\,\mathrm{kg\,m^{-3}}$ and "
        r"$P=\epsilon\sigma A(T^4-T_s^4)$ with $v_{\mathrm{rms}}\propto\sqrt{T}$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    for command in ("circ", "rho", "epsilon", "propto", "mathrm"):
        assert command not in document_xml
    for symbol in ("°", "ρ", "ε", "∝"):
        assert symbol in document_xml
    assert " " in document_xml
    # m:sty p is OMML's upright/plain style, used here for C, kg, m, and rms.
    assert '<m:sty m:val="p"' in document_xml


def test_docx_renders_limits_and_vector_accents_as_native_omml():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"Evaluate $\lim_{n\to\infty}\sum_{i=1}^{n}\left(\frac{i}{n}\right)^2$; "
        r"if $\vec{p}=\hat{i}+2\hat{j}$, then $\overrightarrow{PQ}=Q-P$."
    )
    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "&gt;lim&lt;" not in document_xml
    assert "overrightarrow" not in document_xml
    assert "&gt;vec&lt;" not in document_xml
    assert "&gt;hat&lt;" not in document_xml
    assert "<m:limLow>" in document_xml
    assert "<m:groupChr>" in document_xml
    assert document_xml.count("<m:acc>") >= 3


def test_docx_uses_invisible_operands_instead_of_libreoffice_square_placeholders():
    paper = _paper()
    paper["sections"][0]["questions"][0]["question"] = (
        r"Evaluate $\int$, $x^{}$, $y^$, $\frac{}{x}$, and $\sqrt{}$."
    )

    data = render_paper_docx(paper)
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    # A word joiner is deliberately invisible but prevents LibreOffice from
    # rendering required, empty OMML arguments as square equation boxes.
    assert "\u2060" in document_xml
    assert document_xml.count("<m:nary>") == 1
    assert "<m:sSup>" in document_xml
    assert "<m:f>" in document_xml
    assert "<m:rad>" in document_xml


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
    assert 'wp:extent cx="2377440" cy="1783080"' in document_xml
    with ZipFile(BytesIO(data)) as archive:
        image = Image.open(BytesIO(archive.read(media_files[0])))
        assert image.mode == "L"


def test_docx_diagrams_fit_compact_bounds_without_frames_or_distortion():
    max_extent = int(2.6 * 914400)
    for width, height in ((100, 100), (400, 100), (100, 400)):
        data = render_paper_docx(
            _paper(), diagram_loader=lambda *_: _png_bytes(width, height)
        )
        with ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")

        match = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"', document_xml)
        assert match
        rendered_width, rendered_height = map(int, match.groups())
        assert rendered_width <= max_extent
        assert rendered_height <= max_extent
        assert rendered_width * height == rendered_height * width

        diagram_start = document_xml.rfind("<w:p>", 0, match.start())
        diagram_end = document_xml.find("</w:p>", match.end())
        assert "<w:pBdr>" not in document_xml[diagram_start:diagram_end]


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
    placeholder_start = document_xml.rfind("<w:p>", 0, document_xml.index("Diagram unavailable"))
    placeholder_end = document_xml.find("</w:p>", document_xml.index("Diagram unavailable"))
    assert "<w:pBdr>" not in document_xml[placeholder_start:placeholder_end]


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
    # A normal paper has no board directions; retain its saved instructions.
    assert "Ensure proper LaTeX formatting." in document_xml
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
    assert printable_instructions("Ensure proper LaTeX formatting.") == "Ensure proper LaTeX formatting."
    assert printable_instructions(
        "Show working. Ensure proper LaTeX formatting.",
        custom_instructions="Show working.",
    ) == "Show working. Ensure proper LaTeX formatting."
    assert printable_instructions("", template_instructions="Board instruction.") == "Board instruction."
    assert printable_instructions("Teacher-edited instruction.", template_instructions="Board instruction.", instructions_edited=True) == "Teacher-edited instruction."
    assert canonical_question_type("problem") == "concept"
    assert canonical_question_type("numerical") == "application"
    legacy = {"sections": [{"questions": [{"type": "problem"}]}]}
    hydrate_question_types(legacy)
    assert legacy["sections"][0]["questions"][0]["type"] == "concept"
