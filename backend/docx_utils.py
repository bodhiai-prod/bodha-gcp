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
from export_utils import format_mark, hydrate_question_types, printable_instructions
from section_blueprints import (hydrate_section_instructions, normalize_internal_choice_counts,
                                normalize_template_sections, printable_template_instructions)


DOCX_POOL_MAX_WORKERS = int(os.environ.get("DOCX_POOL_MAX_WORKERS", "4"))
_DOCX_POOL = ThreadPoolExecutor(
    max_workers=DOCX_POOL_MAX_WORKERS, thread_name_prefix="docx"
)

# Keep diagrams compact while preserving their original proportions.
DIAGRAM_MAX_SIZE_INCHES = 2.6

_MATH_RE = re.compile(
    r"(?P<block>\$\$(?P<block_dollar>[\s\S]*?)\$\$|\\\[(?P<block_bracket>[\s\S]*?)\\\])"
    r"|(?P<inline>\$(?P<inline_dollar>[^$\n]+?)\$|\\\((?P<inline_paren>[^()\n]+?)\\\))"
)
_FULL_DISPLAY_MATH_RE = re.compile(
    r"^\s*(?:\$\$(?P<dollar>[\s\S]*?)\$\$|\\\[(?P<bracket>[\s\S]*?)\\\])\s*$"
)
_LATEX_SYMBOLS = {
    # Greek letters used frequently in physics and mathematics exports.
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "ϕ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω", "infty": "∞",
    # Operators, relations, and arrows emitted by the paper-generation prompts.
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗",
    "circ": "°", "degree": "°", "propto": "∝", "perp": "⊥", "parallel": "∥",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "ll": "≪", "gg": "≫", "approx": "≈", "equiv": "≡", "sim": "∼", "cong": "≅",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "mapsto": "↦",
    "implies": "⇒", "impliedby": "⇐", "iff": "⇔", "gets": "←",
    "longrightarrow": "⟶", "longleftarrow": "⟵", "longleftrightarrow": "⟷",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "setminus": "∖", "cup": "∪", "cap": "∩",
    "emptyset": "∅", "varnothing": "∅", "partial": "∂", "nabla": "∇",
    "forall": "∀", "exists": "∃", "therefore": "∴", "because": "∵",
    "nexists": "∄", "neg": "¬", "lnot": "¬", "land": "∧", "lor": "∨",
    "rightleftharpoons": "⇌", "uparrow": "↑", "downarrow": "↓",
    "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮", "ddots": "⋱",
    "star": "⋆", "bullet": "∙", "simeq": "≃", "prime": "′", "angle": "∠",
    "measuredangle": "∡", "triangle": "△", "square": "□", "ell": "ℓ", "hbar": "ℏ",
    "imath": "ı", "jmath": "ȷ", "aleph": "ℵ", "Re": "ℜ", "Im": "ℑ", "wp": "℘",
    "surd": "√", "top": "⊤", "bot": "⊥", "vdash": "⊢", "dashv": "⊣", "models": "⊨",
    "ldotp": ".", "cdotp": "·",
    "langle": "⟨", "rangle": "⟩", "lvert": "|", "rvert": "|", "vert": "|",
    "lVert": "‖", "rVert": "‖", "Vert": "‖", "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉", "coprod": "∐", "not": "¬",
}

# Function names are not variables.  Keeping them upright avoids both a raw
# command leak and the misleading italic output LibreOffice gives to an
# ordinary OMML text run.
_UPRIGHT_OPERATORS = frozenset({
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "coth", "log", "ln", "lg", "exp", "det", "dim",
    "ker", "deg", "gcd", "hom", "arg", "Pr", "limsup", "liminf", "inf", "sup",
    "max", "min",
})

_PASS_THROUGH_STYLE_COMMANDS = frozenset({
    "text", "textrm", "mathrm", "mathnormal", "operatorname", "mathbf", "mathit",
    "mathfrak", "mathsf", "mathtt", "textbf", "textit", "textsf", "texttt",
})

# These commands change decoration rather than the underlying expression.  A
# native text fallback is preferable to exposing the command name in a DOCX;
# the underlying result remains editable and mathematically readable.
_CONTENT_WRAPPERS = frozenset({"boxed", "overbrace", "underbrace"})

_ACCENT_COMMANDS = {
    "vec": ("⃗", False), "hat": ("̂", False), "widehat": ("̂", False),
    "bar": ("¯", False), "tilde": ("̃", False), "widetilde": ("˜", True),
    "dot": ("̇", False), "ddot": ("̈", False), "mathring": ("̊", False),
    "acute": ("́", False), "grave": ("̀", False), "breve": ("̆", False),
    "check": ("̌", False), "overline": ("¯", True), "underline": ("_", False),
    "overrightarrow": ("→", True), "overleftarrow": ("←", True),
}

_NEGATED_RELATIONS = {
    "in": "∉", "ni": "∌", "subset": "⊄", "subseteq": "⊈",
    "supset": "⊅", "supseteq": "⊉",
}

# Unicode has dedicated double-struck glyphs for the alphabet and digits.  It
# is more portable than attempting to set OMML's optional font/style settings:
# Word and LibreOffice both render these in their math fonts.  In particular it
# prevents the command name in ``\\mathbb{R}`` becoming visible document text.
_MATHBB_CHARS = {
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ")),
    **dict(zip("abcdefghijklmnopqrstuvwxyz", "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫")),
    **dict(zip("0123456789", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡")),
}

# Script letters are the portable representation of ``\\mathcal`` in native
# OMML.  As with ``\\mathbb`` above, using the Unicode mathematical alphabet
# avoids depending on a renderer-specific equation font setting.
_MATHCAL_CHARS = {
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵")),
    **dict(zip("abcdefghijklmnopqrstuvwxyz", "𝒶𝒷𝒸𝒹ℯ𝒻𝓰𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏")),
}


def _mathbb_nodes(nodes):
    """Apply LaTeX's blackboard-bold styling to a parsed node sequence."""
    styled = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            styled.append(("text", "".join(_MATHBB_CHARS.get(char, char) for char in node[1])))
        elif kind == "frac":
            styled.append((kind, _mathbb_nodes(node[1]), _mathbb_nodes(node[2])))
        elif kind == "sqrt":
            styled.append((kind, _mathbb_nodes(node[1])))
        elif kind in {"sup", "sub"}:
            styled.append((kind, _mathbb_nodes([node[1]])[0], _mathbb_nodes(node[2])))
        else:
            # Blackboard-bold is normally used for simple set names.  Preserve
            # less usual structures instead of degrading a valid equation.
            styled.append(node)
    return styled


def _mathcal_nodes(nodes):
    """Apply LaTeX's calligraphic styling without leaking ``mathcal``."""
    styled = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            styled.append(("text", "".join(_MATHCAL_CHARS.get(char, char) for char in node[1])))
        elif kind == "frac":
            styled.append((kind, _mathcal_nodes(node[1]), _mathcal_nodes(node[2])))
        elif kind == "sqrt":
            styled.append((kind, _mathcal_nodes(node[1])))
        elif kind in {"sup", "sub"}:
            styled.append((kind, _mathcal_nodes([node[1]])[0], _mathcal_nodes(node[2])))
        else:
            styled.append(node)
    return styled


def _roman_nodes(nodes):
    """Mark a parsed node sequence as upright text, e.g. SI unit symbols."""
    roman = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            roman.append(("roman", node[1]))
        elif kind in {"roman", "frac", "sqrt"}:
            if kind == "roman":
                roman.append(node)
            elif kind == "frac":
                roman.append((kind, _roman_nodes(node[1]), _roman_nodes(node[2])))
            else:
                roman.append((kind, _roman_nodes(node[1])))
        elif kind in {"sup", "sub"}:
            roman.append((kind, _roman_nodes([node[1]])[0], _roman_nodes(node[2])))
        else:
            roman.append(node)
    return roman


def _consume_scripts(source: str, index: int):
    """Consume optional LaTeX lower/upper scripts following an operator."""
    lower, upper = [], []
    while index < len(source) and source[index] in "_^":
        marker = source[index]
        content, index = _latex_group(source, index + 1)
        if marker == "_":
            lower = _latex_to_ast(content)
        else:
            upper = _latex_to_ast(content)
    return lower, upper, index

# These need Word's n-ary structure.  Emitting the command name as ordinary
# math text (the old behaviour) made Word display and spell-check ``int`` and
# then attach its limits to that text rather than to an integral sign.
_NARY_OPERATORS = {
    "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
    "sum": "∑", "prod": "∏", "bigcup": "⋃", "bigcap": "⋂",
}


def _latex_group(source: str, index: int):
    if index >= len(source) or source[index] != "{":
        # LaTex permits a command as an unbraced script operand: ``^\circ``
        # and ``_\infty`` are equivalent to ``^{\circ}`` and ``_{\infty}``.
        # Returning only the slash leaked the rest of the command as document
        # text (for example, ``180^circ C`` in exported physics papers).
        if index < len(source) and source[index] == "\\":
            cursor = index + 1
            while cursor < len(source) and source[cursor].isalpha():
                cursor += 1
            if cursor == index + 1 and cursor < len(source):
                cursor += 1
            return source[index:cursor], cursor
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


def _consume_nary_base(source: str, index: int):
    """Read the first operand following an integral/summation.

    OMML requires an n-ary operator to have an expression child.  Leaving it
    empty displays Word's square equation placeholder, so retain the first
    operand in that child and let the normal parser append the rest.
    """
    while index < len(source) and source[index].isspace():
        index += 1
    if index >= len(source):
        return [], index
    if source[index] == "{":
        content, index = _latex_group(source, index)
        return _latex_to_ast(content), index
    if source[index] != "\\":
        nodes = [("text", source[index])]
        index += 1
    else:
        end = index + 1
        while end < len(source) and source[end].isalpha():
            end += 1
        command = source[index + 1:end]
        if command in {",", ";", ":", "!", " ", "quad", "qquad"}:
            return _consume_nary_base(source, end)
        if command == "left":
            # Treat a resized delimiter pair as one operand.  Previously a
            # summation followed by ``\left( ... \right)`` owned only the
            # opening parenthesis, leaving the actual summand outside the
            # n-ary OMML element.
            if end >= len(source):
                return [], end
            opening = source[end]
            body_start = end + 1
            closing_at = source.find("\\right", body_start)
            if closing_at >= 0:
                after_right = closing_at + len("\\right")
                if after_right < len(source):
                    closing = source[after_right]
                    body = opening + source[body_start:closing_at] + closing
                    cursor = after_right + 1
                    # Scripts following the closing delimiter belong to the
                    # summed/integrated expression, not to the operator.
                    if cursor < len(source) and source[cursor] in "_^":
                        marker = source[cursor]
                        script, cursor = _latex_group(source, cursor + 1)
                        parsed = _latex_to_ast(body)
                        parsed[-1] = (
                            "sup" if marker == "^" else "sub",
                            parsed[-1], _latex_to_ast(script),
                        )
                        return parsed, cursor
                    return _latex_to_ast(body), cursor
            return _consume_nary_base(source, end)
        if command in {"frac", "dfrac", "tfrac"}:
            numerator, cursor = _latex_group(source, end)
            denominator, index = _latex_group(source, cursor)
            nodes = [("frac", _latex_to_ast(numerator), _latex_to_ast(denominator))]
        elif command == "sqrt":
            if end < len(source) and source[end] == "[":
                close = source.find("]", end + 1)
                end = close + 1 if close >= 0 else end
            radicand, index = _latex_group(source, end)
            nodes = [("sqrt", _latex_to_ast(radicand))]
        elif command in _PASS_THROUGH_STYLE_COMMANDS:
            content, index = _latex_group(source, end)
            nodes = _roman_nodes(_latex_to_ast(content))
        elif command == "mathbb":
            content, index = _latex_group(source, end)
            nodes = _mathbb_nodes(_latex_to_ast(content))
        elif command == "mathcal":
            content, index = _latex_group(source, end)
            nodes = _mathcal_nodes(_latex_to_ast(content))
        elif command in _CONTENT_WRAPPERS:
            content, index = _latex_group(source, end)
            nodes = _latex_to_ast(content)
        elif command in {
            "left", "right", "displaystyle", "textstyle", "scriptstyle",
            "scriptscriptstyle", "limits", "nolimits", "quad", "qquad",
        }:
            # These commands decorate the expression that follows; they are
            # never an operand of an integral/summation themselves.
            return _consume_nary_base(source, end)
        else:
            nodes = [("text", _LATEX_SYMBOLS.get(command, command))]
            index = end
    while index < len(source) and source[index] in "_^":
        marker = source[index]
        content, index = _latex_group(source, index + 1)
        nodes[-1] = (
            "sup" if marker == "^" else "sub", nodes[-1], _latex_to_ast(content)
        )
    return nodes, index


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
            if source.startswith("\\\\", cursor):
                nodes.append(("text", "; "))
                cursor += 2
                continue
            end = cursor + 1
            while end < len(source) and source[end].isalpha():
                end += 1
            command = source[cursor + 1:end]
            if not command and end < len(source):
                # LaTeX spacing escapes such as ``\,`` and ``\!`` are not
                # literal punctuation in the equation.  Preserve the common
                # positive spaces so SI units such as ``kg\,m^{-3}`` do not
                # collapse into ``kgm`` in LibreOffice.
                if source[end] in {",", ";", ":", "!", " "}:
                    spacing = {",": " ", ";": " ", ":": " ", " ": " "}
                    if source[end] != "!":
                        nodes.append(("text", spacing[source[end]]))
                    cursor = end + 1
                    continue
                nodes.append(("text", source[end]))
                cursor = end + 1
                continue
            if command == "not":
                # Generated content commonly spells a negated relation as
                # ``\\not\\in`` instead of the equivalent ``\\notin``.
                # Emit one native relation symbol rather than two adjacent
                # glyphs (¬∈), which looks like a rendering leak in Word.
                if end < len(source) and source[end] == "\\":
                    relation_end = end + 1
                    while relation_end < len(source) and source[relation_end].isalpha():
                        relation_end += 1
                    relation = source[end + 1:relation_end]
                    if relation in _NEGATED_RELATIONS:
                        nodes.append(("text", _NEGATED_RELATIONS[relation]))
                        cursor = relation_end
                        continue
                if end < len(source) and source[end] == "=":
                    nodes.append(("text", "≠"))
                    cursor = end + 1
                    continue
                nodes.append(("text", "¬"))
                cursor = end
                continue
            if command in {"frac", "dfrac", "tfrac"}:
                numerator, cursor = _latex_group(source, end)
                denominator, cursor = _latex_group(source, cursor)
                nodes.append(("frac", _latex_to_ast(numerator), _latex_to_ast(denominator)))
                continue
            if command in _NARY_OPERATORS:
                cursor = end
                # LaTeX permits either order for limits, so consume both
                # before continuing with the expression after the operator.
                lower, upper, cursor = _consume_scripts(source, cursor)
                base, cursor = _consume_nary_base(source, cursor)
                nodes.append(("nary", _NARY_OPERATORS[command], lower, upper, base))
                continue
            if command == "lim":
                lower, upper, cursor = _consume_scripts(source, end)
                limit = ("lim", lower)
                nodes.append(("sup", limit, upper) if upper else limit)
                continue
            if command == "begin":
                environment, cursor = _latex_group(source, end)
                closing = f"\\end{{{environment}}}"
                close_at = source.find(closing, cursor)
                if environment in {"matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix"} and close_at >= 0:
                    rows = []
                    for row in source[cursor:close_at].split("\\\\"):
                        rows.append([_latex_to_ast(cell) for cell in row.split("&")])
                    nodes.append(("matrix", environment, rows))
                    cursor = close_at + len(closing)
                    continue
                if environment in {"cases", "array", "aligned"}:
                    nodes.append(("text", "{ " if environment == "cases" else ""))
                continue
            if command == "end":
                _environment, cursor = _latex_group(source, end)
                continue
            if command == "sqrt":
                if end < len(source) and source[end] == "[":
                    close = source.find("]", end + 1)
                    end = close + 1 if close >= 0 else end
                radicand, cursor = _latex_group(source, end)
                nodes.append(("sqrt", _latex_to_ast(radicand)))
                continue
            if command in _ACCENT_COMMANDS:
                content, cursor = _latex_group(source, end)
                accent, stretch = _ACCENT_COMMANDS[command]
                nodes.append(("accent", accent, _latex_to_ast(content), stretch))
                continue
            if command in _PASS_THROUGH_STYLE_COMMANDS:
                content, cursor = _latex_group(source, end)
                nodes.extend(_roman_nodes(_latex_to_ast(content)))
                continue
            if command == "mathbb":
                content, cursor = _latex_group(source, end)
                nodes.extend(_mathbb_nodes(_latex_to_ast(content)))
                continue
            if command == "mathcal":
                content, cursor = _latex_group(source, end)
                nodes.extend(_mathcal_nodes(_latex_to_ast(content)))
                continue
            if command in _CONTENT_WRAPPERS:
                content, cursor = _latex_group(source, end)
                nodes.extend(_latex_to_ast(content))
                continue
            if command in {
                "left", "right", "displaystyle", "textstyle", "scriptstyle",
                "scriptscriptstyle", "limits", "nolimits", "quad", "qquad",
            }:
                cursor = end
                continue
            if command in {",", ";", ":", "!", " "}:
                cursor = end
                continue
            if command in _UPRIGHT_OPERATORS:
                nodes.append(("roman", command))
                cursor = end
                continue
            nodes.append(("text", _LATEX_SYMBOLS.get(command, command)))
            cursor = end
            continue
        if char == "&":
            nodes.append(("text", "  "))
            cursor += 1
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


def _omml_run(text: str, *, upright: bool = False):
    run = OxmlElement("m:r")
    if upright:
        properties = OxmlElement("m:rPr")
        style = OxmlElement("m:sty")
        style.set(qn("m:val"), "p")
        properties.append(style)
        run.append(properties)
    text_element = OxmlElement("m:t")
    text_element.text = text
    run.append(text_element)
    return run


def _omml_arg(nodes):
    container = OxmlElement("m:e")
    # LibreOffice renders an empty OMML argument as a visible square
    # placeholder.  Incomplete model output (for example ``x^{}`` or a
    # trailing ``\\int``) should not turn into a box in the downloaded DOCX.
    # A word joiner gives OOXML a real, zero-width math run, preserving the
    # structure without adding visible spacing or a placeholder glyph.
    for node in nodes or [("text", "\u2060")]:
        container.append(_omml_node(node))
    return container


def _omml_container(tag, nodes):
    container = OxmlElement(tag)
    # Numerators, denominators, scripts, and n-ary limits use this helper.
    # They have the same LibreOffice empty-argument placeholder behaviour as
    # ``m:e`` above, so keep every required OMML child non-empty.
    for node in nodes or [("text", "\u2060")]:
        container.append(_omml_node(node))
    return container


def _omml_node(node):
    kind = node[0]
    if kind == "text":
        return _omml_run(node[1])
    if kind == "roman":
        return _omml_run(node[1], upright=True)
    if kind == "frac":
        fraction = OxmlElement("m:f")
        fraction.append(OxmlElement("m:fPr"))
        fraction.append(_omml_container("m:num", node[1]))
        fraction.append(_omml_container("m:den", node[2]))
        return fraction
    if kind == "sqrt":
        radical = OxmlElement("m:rad")
        properties = OxmlElement("m:radPr")
        degree_hidden = OxmlElement("m:degHide")
        degree_hidden.set(qn("m:val"), "1")
        properties.append(degree_hidden)
        radical.append(properties)
        radical.append(OxmlElement("m:deg"))
        radical.append(_omml_arg(node[1]))
        return radical
    if kind == "lim":
        # m:limLow is the native OOXML limit construct.  m:lim is only its
        # lower-limit child, so emitting it as the outer element made
        # LibreOffice silently discard ``\\lim_{...}``.
        element = OxmlElement("m:limLow")
        element.append(OxmlElement("m:limLowPr"))
        element.append(_omml_arg([("roman", "lim")]))
        element.append(_omml_container("m:lim", node[1]))
        return element
    if kind == "accent":
        accent, contents, stretch = node[1], node[2], node[3]
        if stretch:
            element = OxmlElement("m:groupChr")
            properties = OxmlElement("m:groupChrPr")
            character = OxmlElement("m:chr")
            character.set(qn("m:val"), accent)
            position = OxmlElement("m:pos")
            position.set(qn("m:val"), "top")
            properties.extend([character, position])
            element.append(properties)
            element.append(_omml_arg(contents))
            return element
        element = OxmlElement("m:acc")
        properties = OxmlElement("m:accPr")
        character = OxmlElement("m:chr")
        character.set(qn("m:val"), accent)
        properties.append(character)
        element.append(properties)
        element.append(_omml_arg(contents))
        return element
    if kind == "nary":
        element = OxmlElement("m:nary")
        properties = OxmlElement("m:naryPr")
        character = OxmlElement("m:chr")
        character.set(qn("m:val"), node[1])
        limit_location = OxmlElement("m:limLoc")
        limit_location.set(qn("m:val"), "subSup")
        properties.extend([character, limit_location])
        # OMML requires sub/sup child elements even for an unbounded integral.
        # Without these flags Word and LibreOffice draw small empty equation
        # placeholders above and below the operator.
        if not node[2]:
            sub_hide = OxmlElement("m:subHide")
            sub_hide.set(qn("m:val"), "1")
            properties.append(sub_hide)
        if not node[3]:
            sup_hide = OxmlElement("m:supHide")
            sup_hide.set(qn("m:val"), "1")
            properties.append(sup_hide)
        element.append(properties)
        element.append(_omml_container("m:sub", node[2]))
        element.append(_omml_container("m:sup", node[3]))
        element.append(_omml_arg(node[4]))
        return element
    if kind in {"sup", "sub"}:
        element = OxmlElement("m:sSup" if kind == "sup" else "m:sSub")
        element.append(OxmlElement("m:sSupPr" if kind == "sup" else "m:sSubPr"))
        element.append(_omml_arg([node[1]]))
        element.append(
            _omml_container("m:sup" if kind == "sup" else "m:sub", node[2])
        )
        return element
    if kind == "matrix":
        delimiters = {
            "matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
            "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"),
        }
        matrix = OxmlElement("m:m")
        for row in node[2]:
            matrix_row = OxmlElement("m:mr")
            for cell in row:
                matrix_cell = OxmlElement("m:e")
                for child in cell:
                    matrix_cell.append(_omml_node(child))
                matrix_row.append(matrix_cell)
            matrix.append(matrix_row)
        left, right = delimiters[node[1]]
        if not left:
            return matrix
        delimiter = OxmlElement("m:d")
        props = OxmlElement("m:dPr")
        begin = OxmlElement("m:begChr")
        begin.set(qn("m:val"), left)
        finish = OxmlElement("m:endChr")
        finish.set(qn("m:val"), right)
        props.extend([begin, finish])
        delimiter.append(props)
        delimiter.append(_omml_arg([("raw_omml", matrix)]))
        return delimiter
    if kind == "raw_omml":
        return node[1]
    return _omml_run(str(node))


def _append_native_equation(paragraph, latex: str, *, display: bool = False) -> None:
    math = OxmlElement("m:oMath")
    for node in _latex_to_ast(latex.strip()):
        math.append(_omml_node(node))
    # ``m:oMathPara`` is a block-level OOXML element and is invalid inside a
    # ``w:p``.  A centered Word paragraph containing ``m:oMath`` is both valid
    # OOXML and renders consistently in Word and LibreOffice.
    paragraph._p.append(math)


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


def _remove_paragraph_borders(element) -> None:
    """Remove template/theme title rules that vary between Word versions."""
    properties = element.get_or_add_pPr()
    borders = properties.find(qn("w:pBdr"))
    if borders is not None:
        properties.remove(borders)


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


def _set_visible_table_borders(table, color="000000"):
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
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "6")
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


def _add_equation_block(container, latex: str):
    paragraph = container.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.25)
    paragraph.paragraph_format.right_indent = Inches(0.25)
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_keep(paragraph, keep_together=True)
    _append_native_equation(paragraph, latex, display=True)
    return paragraph


def _append_rich_text(paragraph, container, text: str):
    """Append text and math to a paragraph; display math gets its own block."""
    matches = list(_MATH_RE.finditer(text or ""))
    if not matches:
        paragraph.add_run(text or "")
        return paragraph

    cursor = 0
    last_block = None
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
            last_block = _add_equation_block(container, latex)
            paragraph = None
        else:
            if paragraph is None:
                paragraph = container.add_paragraph()
            _append_native_equation(paragraph, latex, display=False)
        cursor = match.end()
    if cursor < len(text):
        tail = text[cursor:]
        if paragraph is None and last_block is not None and tail.strip() in {".", ",", ";", ":", "?", "!"}:
            last_block.add_run(tail)
            paragraph = last_block
        elif paragraph is None:
            paragraph = container.add_paragraph()
            paragraph.add_run(tail)
        else:
            paragraph.add_run(tail)
    return paragraph


def _add_text_with_newlines(container, text: str, *, style: Optional[str] = None) -> None:
    lines = str(text or "").splitlines() or [""]
    for line in lines:
        paragraph = container.add_paragraph(style=style)
        _append_rich_text(paragraph, container, line)


def _grayscale_image_bytes(image_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        grayscale = image.convert("L")
        output = io.BytesIO()
        grayscale.save(output, format="PNG")
        return output.getvalue()


def _add_diagram(container, image_bytes: Optional[bytes], available: bool) -> None:
    if image_bytes and available:
        try:
            paragraph = container.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.left_indent = Inches(0.35)
            paragraph.paragraph_format.space_before = Pt(2)
            paragraph.paragraph_format.space_after = Pt(6)
            _set_keep(paragraph, keep_together=True)
            image_bytes = _grayscale_image_bytes(image_bytes)
            with Image.open(io.BytesIO(image_bytes)) as image:
                width_px, height_px = image.size
            # Keep figures compact enough to sit naturally with the question
            # while retaining their source aspect ratio and legibility.
            max_width = Inches(DIAGRAM_MAX_SIZE_INCHES)
            max_height = Inches(DIAGRAM_MAX_SIZE_INCHES)
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
    placeholder.add_run("[Diagram unavailable]").italic = True


def render_paper_docx(
    paper: dict,
    diagram_loader: Optional[Callable[[str, str], Optional[bytes]]] = None,
    branding_loader: Optional[Callable[[str], Optional[bytes]]] = None,
    *,
    include_solutions: bool = False,
) -> bytes:
    normalized, misses = normalize_template_sections(paper.get("sections") or [], paper.get("generation_requirements") or {})
    if not misses:
        paper["sections"] = normalized
    normalize_internal_choice_counts(paper.get("sections") or [])
    hydrate_section_instructions(paper)
    hydrate_question_types(paper)
    document = Document()
    section = document.sections[0]
    # CBSE and most Indian school paper templates use A4.  Explicit page
    # dimensions avoid Word inheriting US Letter from the python-docx default.
    section.page_width = Inches(8.27)
    section.page_height = Inches(11.69)
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)
    section.left_margin = Inches(0.6)
    section.right_margin = Inches(0.6)

    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.08

    # Keep the built-in Word style names useful for teachers who continue
    # editing a downloaded paper instead of relying on direct formatting.
    document.styles["Title"].font.name = "Arial"
    document.styles["Title"].font.size = Pt(18)
    document.styles["Title"].font.color.rgb = RGBColor(0, 0, 0)
    _remove_paragraph_borders(document.styles["Title"]._element)
    document.styles["Heading 1"].font.name = "Arial"
    document.styles["Heading 1"].font.size = Pt(13)
    document.styles["Heading 1"].font.color.rgb = RGBColor(0, 0, 0)

    header_path = paper.get("header_image_path")
    if header_path and branding_loader:
        header_bytes = branding_loader(header_path)
        if header_bytes:
            try:
                header_bytes = _grayscale_image_bytes(header_bytes)
                paragraph = document.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.add_run().add_picture(
                    io.BytesIO(header_bytes), width=Inches(1.0)
                )
            except (UnrecognizedImageError, ValueError, OSError):
                pass

    for key, size in (("school_name", 15), ("title", 18)):
        value = (paper.get(key) or "").strip()
        if value:
            paragraph = document.add_paragraph(style="Title" if key == "title" else None)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(value)
            run.bold = True
            run.font.size = Pt(size)

    if include_solutions:
        answer_key_label = document.add_paragraph()
        answer_key_label.alignment = WD_ALIGN_PARAGRAPH.CENTER
        answer_key_label.paragraph_format.space_after = Pt(2)
        answer_key_label.add_run("ANSWER KEY").bold = True

    tagline = (paper.get("branding_tagline") or "").strip()
    if tagline:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run(tagline).italic = True

    metadata = (
        f"Class: {paper.get('class_name', '')}    "
        f"Subject: {paper.get('subject', '')}    "
        f"Total Marks: {format_mark(paper.get('total_marks', ''))}    "
        f"Duration: {paper.get('duration_minutes', '')} min"
    )
    metadata_paragraph = document.add_paragraph()
    metadata_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    metadata_paragraph.paragraph_format.space_after = Pt(10)
    metadata_paragraph.add_run(metadata)

    # Keep a quiet visual separation below the title block without the heavy
    # black bar that made the opening page feel cramped.
    separator = document.add_paragraph()
    separator.paragraph_format.space_after = Pt(5)
    properties = separator._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom_border = OxmlElement("w:bottom")
    bottom_border.set(qn("w:val"), "single")
    bottom_border.set(qn("w:sz"), "4")
    bottom_border.set(qn("w:space"), "1")
    bottom_border.set(qn("w:color"), "BFBFBF")
    borders.append(bottom_border)
    properties.append(borders)

    instructions = printable_instructions(
        paper.get("instructions"),
        template_instructions=printable_template_instructions(paper),
        instructions_edited=bool(paper.get("instructions_edited")),
        custom_instructions=paper.get("custom_instructions") or "",
    )
    if not include_solutions:
        instruction_table = document.add_table(rows=1, cols=1)
        instruction_table.alignment = WD_TABLE_ALIGNMENT.LEFT
        instruction_table.autofit = False
        _set_table_width(instruction_table, 7.05)
        _set_table_columns(instruction_table, (7.05,))
        _set_visible_table_borders(instruction_table)
        instruction_cell = instruction_table.cell(0, 0)
        _set_cell_margins(instruction_cell, top=90, start=120, bottom=90, end=120)
        heading = instruction_cell.paragraphs[0]
        heading.paragraph_format.space_after = Pt(3)
        heading.add_run("Instructions").bold = True
        body = instruction_cell.add_paragraph()
        body.paragraph_format.space_after = Pt(0)
        _append_rich_text(body, instruction_cell, instructions)

    answer_by_question = {}
    if include_solutions:
        for solution_section in (paper.get("solution") or {}).get("sections") or []:
            for answer in solution_section.get("answers") or []:
                answer_by_question[answer.get("question_id")] = answer.get("answer") or ""

    sections = paper.get("sections") or []
    if not sections:
        sections = [{"title": "Questions", "questions": paper.get("questions", [])}]

    labels = ["(a)", "(b)", "(c)", "(d)"]
    question_number = 1
    for section_data in sections:
        heading = document.add_paragraph(style="Heading 1")
        heading.paragraph_format.space_before = Pt(12)
        heading.paragraph_format.space_after = Pt(5)
        _set_keep(heading, keep_next=True, keep_together=True)
        section_run = heading.add_run(section_data.get("title", "Section"))
        section_run.bold = True
        section_run.font.name = "Arial"
        section_run.font.size = Pt(13)
        section_run.font.color.rgb = RGBColor(0, 0, 0)
        section_instructions = (section_data.get("section_instructions") or "").strip()
        if section_instructions and not include_solutions:
            lead = document.add_paragraph()
            lead.paragraph_format.space_after = Pt(4)
            lead.paragraph_format.left_indent = Inches(0.12)
            lead.add_run(section_instructions).italic = True
        printed_parent = None
        for question in section_data.get("questions", []):
            parent_id = question.get("parent_id")
            first_part = bool(parent_id and parent_id != printed_parent)
            if first_part:
                if printed_parent is not None:
                    question_number += 1
                printed_parent = parent_id
                if question.get("shared_stimulus"):
                    parent_stimulus = document.add_paragraph()
                    parent_stimulus.paragraph_format.left_indent = Inches(0.12)
                    parent_stimulus.paragraph_format.space_after = Pt(3)
                    parent_stimulus.add_run(f"Q{question_number}. ").bold = True
                    parent_stimulus.add_run(question["shared_stimulus"])
                if question.get("parent_lead_in"):
                    parent_lead = document.add_paragraph()
                    parent_lead.paragraph_format.left_indent = Inches(0.12)
                    parent_lead.paragraph_format.space_after = Pt(3)
                    parent_lead.add_run(question["parent_lead_in"]).italic = True
            elif not parent_id and printed_parent is not None:
                question_number += 1
                printed_parent = None
            question_table = document.add_table(rows=1, cols=2)
            question_table.alignment = WD_TABLE_ALIGNMENT.LEFT
            question_table.autofit = False
            _set_table_width(question_table, 7.07)
            _set_table_columns(question_table, (6.40, 0.67))
            _set_table_borders(question_table)
            # A question, its choices, and its answer-key explanation must
            # stay on one page.  Splitting the row can leave a display equation
            # clipped at the foot of a page in LibreOffice.
            _set_row_cant_split(question_table.rows[0])
            question_cell = question_table.cell(0, 0)
            marks_cell = question_table.cell(0, 1)
            _set_cell_margins(question_cell, start=0, end=90)
            _set_cell_margins(marks_cell, start=30, end=30)
            question_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            marks_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP

            question_paragraph = question_cell.paragraphs[0]
            question_paragraph.paragraph_format.space_before = Pt(5)
            question_paragraph.paragraph_format.space_after = Pt(3)
            _set_keep(question_paragraph, keep_together=True)
            prefix = ((f"{question.get('part_label', '')} " if question.get("shared_stimulus") else f"Q{question_number}. {question.get('part_label', '')} ") if first_part
                      else (f"{question.get('part_label', '')} " if parent_id else f"Q{question_number}. "))
            question_paragraph.add_run(prefix).bold = True
            marks_paragraph = marks_cell.paragraphs[0]
            marks_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            marks_paragraph.paragraph_format.space_before = Pt(5)
            marks_run = marks_paragraph.add_run(f"[{format_mark(question.get('marks', 0))}]")
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
            if include_solutions:
                # Keep each answer in the same full-width question block.  The
                # previous standalone paragraphs started at document margins,
                # while the question lived in a narrow table, producing the
                # visibly jagged answer key shown in LibreOffice and Word.
                answer_heading = question_cell.add_paragraph()
                answer_heading.paragraph_format.left_indent = Inches(0.15)
                answer_heading.paragraph_format.space_before = Pt(5)
                answer_heading.paragraph_format.space_after = Pt(2)
                _set_keep(answer_heading, keep_together=True)
                answer_heading.add_run("Answer").bold = True
                answer_paragraph = question_cell.add_paragraph()
                answer_paragraph.paragraph_format.left_indent = Inches(0.08)
                answer_paragraph.paragraph_format.right_indent = Inches(0.08)
                answer_paragraph.paragraph_format.space_after = Pt(8)
                _set_cell_or_paragraph_shading(answer_paragraph, "F2F2F2")
                _set_cell_or_paragraph_border(answer_paragraph, "BFBFBF")
                answer_text = answer_by_question.get(question.get("id")) or "(no answer)"
                full_display = _FULL_DISPLAY_MATH_RE.match(answer_text)
                if full_display:
                    # Keep a full display equation editable inside the shaded
                    # answer block instead of adding a paragraph that can
                    # separate from its answer label at a page boundary.
                    answer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    _append_native_equation(
                        answer_paragraph,
                        full_display.group("dollar") or full_display.group("bracket") or "",
                        display=True,
                    )
                else:
                    _append_rich_text(answer_paragraph, question_cell, answer_text)
            if not parent_id:
                question_number += 1
        if printed_parent is not None:
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


def render_solution_docx(
    paper: dict,
    diagram_loader: Optional[Callable[[str, str], Optional[bytes]]] = None,
    branding_loader: Optional[Callable[[str], Optional[bytes]]] = None,
) -> bytes:
    return render_paper_docx(
        paper,
        diagram_loader=diagram_loader,
        branding_loader=branding_loader,
        include_solutions=True,
    )


async def render_solution_docx_async(
    paper: dict,
    diagram_loader: Optional[Callable[[str, str], Optional[bytes]]] = None,
    branding_loader: Optional[Callable[[str], Optional[bytes]]] = None,
) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _DOCX_POOL, render_solution_docx, paper, diagram_loader, branding_loader
    )
