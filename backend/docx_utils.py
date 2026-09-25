"""Editable Word document rendering for generated question papers."""

import asyncio
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.exceptions import UnrecognizedImageError
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image


DOCX_POOL_MAX_WORKERS = int(os.environ.get("DOCX_POOL_MAX_WORKERS", "4"))
_DOCX_POOL = ThreadPoolExecutor(
    max_workers=DOCX_POOL_MAX_WORKERS, thread_name_prefix="docx"
)

_MATH_RE = re.compile(
    r"(?P<block>\$\$(?P<block_dollar>[\s\S]*?)\$\$|\\\[(?P<block_bracket>[\s\S]*?)\\\])"
    r"|(?P<inline>\$(?P<inline_dollar>[^$\n]+?)\$|\\\((?P<inline_paren>[^()\n]+?)\\\))"
)

_LATEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π",
    "sigma": "σ", "phi": "φ", "omega": "ω", "Delta": "Δ",
    "Gamma": "Γ", "Theta": "Θ", "Lambda": "Λ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Omega": "Ω", "infty": "∞",
    "cdot": "·", "times": "×", "pm": "±", "leq": "≤",
    "geq": "≥", "neq": "≠", "approx": "≈", "to": "→",
    "rightarrow": "→", "degree": "°", "circ": "°",
}


def _latex_group(source: str, index: int):
    if index >= len(source) or source[index] != "{":
        return source[index:index + 1], index + 1
    depth = 1
    cursor = index + 1
    while cursor < len(source) and depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[index + 1:cursor - 1], cursor


def _latex_to_ast(source: str):
    """Parse common LaTeX into a small AST suitable for native Word math."""
    nodes = []
    cursor = 0
    while cursor < len(source):
        char = source[cursor]
        if char.isspace():
            cursor += 1
            continue
        if char == "}":
            cursor += 1
            continue
        if char == "{":
            group, cursor = _latex_group(source, cursor)
            nodes.extend(_latex_to_ast(group))
            continue
        if char == "\\":
            end = cursor + 1
            while end < len(source) and source[end].isalpha():
                end += 1
            command = source[cursor + 1:end]
            if not command and end < len(source):
                nodes.append(("text", source[end]))
                cursor = end + 1
                continue
            if command in {"frac", "dfrac", "tfrac"}:
                numerator, cursor = _latex_group(source, end)
                denominator, cursor = _latex_group(source, cursor)
                nodes.append(("frac", _latex_to_ast(numerator), _latex_to_ast(denominator)))
                continue
            if command == "sqrt":
                if end < len(source) and source[end] == "[":
                    close = source.find("]", end + 1)
                    end = close + 1 if close >= 0 else end
                radicand, cursor = _latex_group(source, end)
                nodes.append(("sqrt", _latex_to_ast(radicand)))
                continue
            if command in {"text", "textrm", "mathrm", "mathbf", "mathit"}:
                content, cursor = _latex_group(source, end)
                nodes.append(("text", content))
                continue
            if command in {"left", "right", "displaystyle", "scriptstyle"}:
                cursor = end
                continue
            if command in {",", ";", ":", "!", " "}:
                cursor = end
                continue
            nodes.append(("text", _LATEX_SYMBOLS.get(command, command)))
            cursor = end
            continue
        if char in "^_":
            if nodes:
                content, next_cursor = _latex_group(source, cursor + 1)
                nodes[-1] = (
                    "sup" if char == "^" else "sub",
                    nodes[-1],
                    _latex_to_ast(content),
                )
                cursor = next_cursor
                continue
        nodes.append(("text", char))
        cursor += 1
    return nodes


def _omml_run(text: str):
    run = OxmlElement("m:r")
    text_element = OxmlElement("m:t")
    text_element.text = text
    run.append(text_element)
    return run


def _omml_arg(nodes):
    container = OxmlElement("m:e")
    for node in nodes:
        container.append(_omml_node(node))
    return container


def _omml_container(tag, nodes):
    container = OxmlElement(tag)
    for node in nodes:
        container.append(_omml_node(node))
    return container


def _omml_node(node):
    kind = node[0]
    if kind == "text":
        return _omml_run(node[1])
    if kind == "frac":
        fraction = OxmlElement("m:f")
        fraction.append(OxmlElement("m:fPr"))
        fraction.append(_omml_container("m:num", node[1]))
        fraction.append(_omml_container("m:den", node[2]))
        return fraction
    if kind == "sqrt":
        radical = OxmlElement("m:rad")
        radical.append(OxmlElement("m:radPr"))
        radical.append(_omml_arg(node[1]))
        return radical
    if kind in {"sup", "sub"}:
        element = OxmlElement("m:sSup" if kind == "sup" else "m:sSub")
        element.append(OxmlElement("m:sSupPr" if kind == "sup" else "m:sSubPr"))
        element.append(_omml_arg([node[1]]))
        element.append(
            _omml_container("m:sup" if kind == "sup" else "m:sub", node[2])
        )
        return element
    return _omml_run(str(node))


def _append_native_equation(paragraph, latex: str) -> None:
    math_para = OxmlElement("m:oMathPara")
    math = OxmlElement("m:oMath")
    for node in _latex_to_ast(latex.strip()):
        math.append(_omml_node(node))
    math_para.append(math)
    paragraph._p.append(math_para)


def shutdown_docx_pool() -> None:
    _DOCX_POOL.shutdown(wait=False)


def _set_cell_or_paragraph_shading(element, fill: str) -> None:
    properties = element._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _set_cell_or_paragraph_border(element, color: str = "D9E2F3") -> None:
    properties = element._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "6")
        border.set(qn("w:space"), "4")
        border.set(qn("w:color"), color)
        borders.append(border)
    properties.append(borders)


def _set_keep(paragraph, *, keep_next=False, keep_together=False):
    paragraph.paragraph_format.keep_with_next = keep_next
    paragraph.paragraph_format.keep_together = keep_together


def _set_table_borders(table, color="FFFFFF"):
    table_properties = table._tbl.tblPr
    borders = table_properties.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table_properties.append(borders)
    for edge in ("top", "left", "bottom", "insideH", "insideV", "right"):
        border = borders.find(qn(f"w:{edge}"))
        if border is None:
            border = OxmlElement(f"w:{edge}")
            borders.append(border)
        border.set(qn("w:val"), "nil")
        border.set(qn("w:color"), color)


def _set_table_width(table, width_inches: float):
    table_properties = table._tbl.tblPr
    width = table_properties.first_child_found_in("w:tblW")
    if width is None:
        width = OxmlElement("w:tblW")
        table_properties.append(width)
    width.set(qn("w:w"), str(int(width_inches * 1440)))
    width.set(qn("w:type"), "dxa")


def _set_table_columns(table, widths):
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width_inches in widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(int(width_inches * 1440)))
        grid.append(column)
    for row in table.rows:
        for cell, width_inches in zip(row.cells, widths):
            cell.width = Inches(width_inches)
            cell_properties = cell._tc.get_or_add_tcPr()
            cell_width = cell_properties.first_child_found_in("w:tcW")
            if cell_width is None:
                cell_width = OxmlElement("w:tcW")
                cell_properties.append(cell_width)
            cell_width.set(qn("w:w"), str(int(width_inches * 1440)))
            cell_width.set(qn("w:type"), "dxa")


def _set_cell_margins(cell, top=60, start=90, bottom=60, end=90):
    properties = cell._tc.get_or_add_tcPr()
    margins = properties.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        properties.append(margins)
    for name, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        margin = margins.find(qn(f"w:{name}"))
        if margin is None:
            margin = OxmlElement(f"w:{name}")
            margins.append(margin)
        margin.set(qn("w:w"), str(value))
        margin.set(qn("w:type"), "dxa")


def _set_row_cant_split(row):
    properties = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    properties.append(cant_split)


def _add_equation_block(container, latex: str) -> None:
    paragraph = container.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.25)
    paragraph.paragraph_format.right_indent = Inches(0.25)
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_keep(paragraph, keep_together=True)
    _set_cell_or_paragraph_shading(paragraph, "F3F6FA")
    _set_cell_or_paragraph_border(paragraph)
    _append_native_equation(paragraph, latex)


def _append_rich_text(paragraph, container, text: str):
    """Append text and math to a paragraph; display math gets its own block."""
    matches = list(_MATH_RE.finditer(text or ""))
    if not matches:
        paragraph.add_run(text or "")
        return paragraph

    cursor = 0
    for match in matches:
        if match.start() > cursor:
            if paragraph is None:
                paragraph = container.add_paragraph()
            paragraph.add_run(text[cursor:match.start()])
        latex = (
            match.group("block_dollar")
            or match.group("block_bracket")
            or match.group("inline_dollar")
            or match.group("inline_paren")
            or ""
        )
        if match.group("block"):
            _add_equation_block(container, latex)
            paragraph = None
        else:
            if paragraph is None:
                paragraph = container.add_paragraph()
            _append_native_equation(paragraph, latex)
        cursor = match.end()
    if cursor < len(text):
        if paragraph is None:
            paragraph = container.add_paragraph()
        paragraph.add_run(text[cursor:])
    return paragraph


def _add_text_with_newlines(container, text: str, *, style: Optional[str] = None) -> None:
    lines = str(text or "").splitlines() or [""]
    for line in lines:
        paragraph = container.add_paragraph(style=style)
        _append_rich_text(paragraph, container, line)


def _add_diagram(container, image_bytes: Optional[bytes], available: bool) -> None:
    if image_bytes and available:
        try:
            paragraph = container.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.left_indent = Inches(0.35)
            paragraph.paragraph_format.space_before = Pt(2)
            paragraph.paragraph_format.space_after = Pt(6)
            _set_keep(paragraph, keep_together=True)
            _set_cell_or_paragraph_border(paragraph, "BFBFBF")
            with Image.open(io.BytesIO(image_bytes)) as image:
                width_px, height_px = image.size
            # Keep figures compact enough to sit naturally with the question
            # while retaining their source aspect ratio and legibility.
            max_width = Inches(4.25)
            max_height = Inches(3.75)
            width = max_width
            height = width * height_px / max(width_px, 1)
            if height > max_height:
                height = max_height
                width = height * width_px / max(height_px, 1)
            paragraph.add_run().add_picture(
                io.BytesIO(image_bytes), width=width, height=height
            )
            return
        except (UnrecognizedImageError, ValueError, OSError):
            pass
    placeholder = container.add_paragraph()
    placeholder.paragraph_format.left_indent = Inches(0.25)
    placeholder.paragraph_format.space_after = Pt(6)
    _set_keep(placeholder, keep_together=True)
    _set_cell_or_paragraph_shading(placeholder, "FFF4E5")
    _set_cell_or_paragraph_border(placeholder, "E6A23C")
    placeholder.add_run("[Diagram unavailable]").italic = True


def render_paper_docx(
    paper: dict,
    diagram_loader: Optional[Callable[[str, str], Optional[bytes]]] = None,
    branding_loader: Optional[Callable[[str], Optional[bytes]]] = None,
) -> bytes:
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)

    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.08

    header_path = paper.get("header_image_path")
    if header_path and branding_loader:
        header_bytes = branding_loader(header_path)
        if header_bytes:
            try:
                paragraph = document.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.add_run().add_picture(
                    io.BytesIO(header_bytes), width=Inches(1.0)
                )
            except (UnrecognizedImageError, ValueError, OSError):
                pass

    for key, size in (("school_name", 15), ("title", 20)):
        value = (paper.get(key) or "").strip()
        if value:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(value)
            run.bold = True
            run.font.size = Pt(size)

    tagline = (paper.get("branding_tagline") or "").strip()
    if tagline:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run(tagline).italic = True

    metadata = (
        f"Class: {paper.get('class_name', '')}    "
        f"Subject: {paper.get('subject', '')}    "
        f"Total Marks: {paper.get('total_marks', '')}    "
        f"Duration: {paper.get('duration_minutes', '')} min"
    )
    metadata_paragraph = document.add_paragraph()
    metadata_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    metadata_paragraph.paragraph_format.space_after = Pt(10)
    metadata_paragraph.add_run(metadata)

    instructions = paper.get("instructions")
    if instructions:
        heading = document.add_paragraph()
        heading.paragraph_format.space_before = Pt(4)
        heading.paragraph_format.space_after = Pt(0)
        heading.add_run("Instructions").bold = True
        _set_cell_or_paragraph_shading(heading, "F3F6FA")
        _set_cell_or_paragraph_border(heading)
        body = document.add_paragraph()
        body.paragraph_format.left_indent = Inches(0.12)
        body.paragraph_format.right_indent = Inches(0.12)
        body.paragraph_format.space_after = Pt(10)
        _set_cell_or_paragraph_shading(body, "F3F6FA")
        _set_cell_or_paragraph_border(body)
        _append_rich_text(body, document, instructions)

    sections = paper.get("sections") or []
    if not sections:
        sections = [{"title": "Questions", "questions": paper.get("questions", [])}]

    labels = ["(a)", "(b)", "(c)", "(d)"]
    question_number = 1
    for section_data in sections:
        heading = document.add_paragraph()
        heading.paragraph_format.space_before = Pt(12)
        heading.paragraph_format.space_after = Pt(5)
        _set_keep(heading, keep_next=True, keep_together=True)
        section_run = heading.add_run(section_data.get("title", "Section"))
        section_run.bold = True
        section_run.font.name = "Arial"
        section_run.font.size = Pt(13)
        section_run.font.color.rgb = RGBColor(0, 47, 167)
        for question in section_data.get("questions", []):
            question_table = document.add_table(rows=1, cols=2)
            question_table.alignment = WD_TABLE_ALIGNMENT.LEFT
            question_table.autofit = False
            _set_table_width(question_table, 6.65)
            _set_table_columns(question_table, (6.05, 0.6))
            _set_table_borders(question_table)
            question_cell = question_table.cell(0, 0)
            marks_cell = question_table.cell(0, 1)
            _set_cell_margins(question_cell, start=0, end=90)
            _set_cell_margins(marks_cell, start=0, end=0)
            question_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            marks_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP

            question_paragraph = question_cell.paragraphs[0]
            question_paragraph.paragraph_format.space_before = Pt(5)
            question_paragraph.paragraph_format.space_after = Pt(3)
            _set_keep(question_paragraph, keep_together=True)
            prefix = f"Q{question_number}. "
            question_paragraph.add_run(prefix).bold = True
            marks_paragraph = marks_cell.paragraphs[0]
            marks_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            marks_paragraph.paragraph_format.space_before = Pt(5)
            marks_run = marks_paragraph.add_run(f"[{question.get('marks', 0)}]")
            marks_run.bold = True
            _append_rich_text(
                question_paragraph,
                question_cell,
                f" {question.get('question', '')}",
            )

            if (question.get("format") or "").lower() == "mcq" and question.get("options"):
                for index, option in enumerate(question["options"][:4]):
                    option_paragraph = question_cell.add_paragraph()
                    option_paragraph.paragraph_format.left_indent = Inches(0.35)
                    option_paragraph.paragraph_format.space_after = Pt(1)
                    _set_keep(option_paragraph, keep_together=True)
                    option_paragraph.add_run(
                        f"{labels[index] if index < len(labels) else f'({index + 1})'} "
                    ).bold = True
                    _append_rich_text(option_paragraph, question_cell, str(option))

            # Keep the diagram block in the editable export even when image
            # generation is pending or storage has not returned an asset yet.
            # This prevents a missing figure from silently changing the paper.
            if (
                question.get("diagram_path")
                or question.get("needs_diagram")
                or question.get("diagram_status") in {"pending", "failed"}
            ):
                image_bytes = (
                    diagram_loader(question.get("id"), question["diagram_path"])
                    if diagram_loader and question.get("diagram_path")
                    else None
                )
                _add_diagram(question_cell, image_bytes, bool(image_bytes))
            question_number += 1

    footer = (paper.get("branding_footer") or "").strip()
    for document_section in document.sections:
        footer_paragraph = document_section.footer.paragraphs[0]
        footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer_paragraph.paragraph_format.space_before = Pt(4)
        if footer:
            footer_paragraph.add_run(footer)
        if footer:
            footer_paragraph.add_run("  ·  ")
        footer_paragraph.add_run("Page ")
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "PAGE")
        footer_paragraph._p.append(field)

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


async def render_paper_docx_async(
    paper: dict,
    diagram_loader: Optional[Callable[[str, str], Optional[bytes]]] = None,
    branding_loader: Optional[Callable[[str], Optional[bytes]]] = None,
) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _DOCX_POOL, render_paper_docx, paper, diagram_loader, branding_loader
    )
