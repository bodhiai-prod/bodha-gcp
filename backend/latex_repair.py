"""LaTeX checking and repair for generated question papers and answer keys.

Three layers, cheapest first:

1. ``_deterministic()`` -- regex fixes that need no model at all: alternate
   delimiters, ``\\text`` -> ``\\mathrm``, Unicode leakage, currency dollars,
   markdown, and naked commands sitting outside ``$...$``. Always runs.
2. ``validate()`` -- reports whatever is still wrong as a list of issue codes.
3. ``repair_items()`` -- sends ONLY the items that still fail to the LLM, all of
   them in one batched call, and accepts a returned fix only if it survives the
   guardrails in ``_accept()``.

The split exists for cost. A clean paper never reaches step 3, so the checking
layer is free for the large majority of generations that were already fine.

Nothing here raises. A repair that cannot be made safely leaves the original
text untouched and is counted in the ``unrepaired`` stat, because a wrong
formula is worse than an ugly one.

Non-ASCII characters are written as \\u escapes on purpose: this module is the
one place that has to reason about Unicode-vs-LaTeX, and escapes survive every
editor, terminal and diff tool the file passes through.
"""
from __future__ import annotations

import asyncio
import difflib
import os
import re

from deps import logger, utcnow_iso
from llm_adapter import chat_complete, parse_json_response
from prompts import LATEX_REPAIR_SYSTEM, latex_repair_prompt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# false = deterministic pass only. The kill switch: generation keeps working,
# it just stops making repair calls.
REPAIR_ENABLED = os.environ.get("LATEX_REPAIR_ENABLED", "true").strip().lower() not in (
    "false", "0", "no",
)
# Blank means "use whatever chain TEXT_PROVIDER already serves" -- with
# TEXT_PROVIDER=openrouter that is OPENROUTER_TEXT_MODEL_DEFAULT.
REPAIR_MODEL = os.environ.get("LATEX_REPAIR_MODEL", "").strip()
# Items per repair call. A paper with more broken items than this is chunked,
# which is fine -- it is already a pathological paper.
REPAIR_MAX_ITEMS = int(os.environ.get("LATEX_REPAIR_MAX_ITEMS", "40"))
# Budget for one repair call. Enforced here rather than in chat_complete so the
# repair pass can never hold up a generation for longer than its own share.
REPAIR_TIMEOUT = float(os.environ.get("LATEX_REPAIR_TIMEOUT", "90"))
# How different a repaired string may be from the original before we assume the
# model rewrote the content instead of fixing the markup. See _accept().
SIMILARITY_FLOOR = float(os.environ.get("LATEX_REPAIR_SIMILARITY_FLOOR", "0.75"))


# ---------------------------------------------------------------------------
# Symbol tables
# ---------------------------------------------------------------------------
# Superscript / subscript digits the models emit instead of ^{} and _{}.
_SUPERSCRIPTS = {
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2074": "4",
    "\u2075": "5", "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
    "\u207a": "+", "\u207b": "-", "\u207f": "n",
}
_SUBSCRIPTS = {
    "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
    "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9",
    "\u208a": "+", "\u208b": "-",
}

# Everything else that has a LaTeX spelling. Inverts the one-way table in
# pdf_utils._strip_latex_for_fallback -- that one goes LaTeX -> Unicode for the
# ReportLab fallback, this one goes back the other way so KaTeX can render it.
# Trailing spaces are deliberate: "\\times3" is legal but "\\theta x" must not
# become "\\thetax".
_UNICODE_MATH = {
    "\u00b0": "^\\circ ",
    "\u00d7": "\\times ",
    "\u00b7": "\\cdot ",
    "\u00b1": "\\pm ",
    "\u2212": "-",
    "\u2248": "\\approx ",
    "\u2260": "\\neq ",
    "\u2264": "\\leq ",
    "\u2265": "\\geq ",
    "\u2192": "\\to ",
    "\u221e": "\\infty ",
    "\u221a": "\\sqrt ",
    "\u2211": "\\sum ",
    "\u220f": "\\prod ",
    "\u222b": "\\int ",
    "\u2202": "\\partial ",
    "\u0394": "\\Delta ",
    "\u03a9": "\\Omega ",
    "\u03a3": "\\Sigma ",
    "\u03a6": "\\Phi ",
    "\u0398": "\\Theta ",
    "\u039b": "\\Lambda ",
    "\u03a0": "\\Pi ",
    "\u0393": "\\Gamma ",
    "\u03b1": "\\alpha ",
    "\u03b2": "\\beta ",
    "\u03b3": "\\gamma ",
    "\u03b4": "\\delta ",
    "\u03b5": "\\epsilon ",
    "\u03b8": "\\theta ",
    "\u03bb": "\\lambda ",
    "\u03bc": "\\mu ",
    "\u03c0": "\\pi ",
    "\u03c1": "\\rho ",
    "\u03c3": "\\sigma ",
    "\u03c4": "\\tau ",
    "\u03c6": "\\phi ",
    "\u03c9": "\\omega ",
}

_LEAK_CHARS = "".join(
    sorted((set(_SUPERSCRIPTS) | set(_SUBSCRIPTS) | set(_UNICODE_MATH)) - {"\u2212"})
)
_LEAK_RE = re.compile("[" + re.escape(_LEAK_CHARS) + "]")
_SUP_RUN_RE = re.compile("[" + re.escape("".join(_SUPERSCRIPTS)) + "]+")
_SUB_RUN_RE = re.compile("[" + re.escape("".join(_SUBSCRIPTS)) + "]+")

# A backslash command plus however many braced arguments follow it. Used both
# to detect math that escaped its delimiters and to wrap it back up.
_NAKED_RE = re.compile(r"\\[a-zA-Z]+(?:\s*\{[^{}]*\})*")
_COMMAND_RE = re.compile(r"\\([a-zA-Z]+)")

# A super/subscripted symbol left sitting in prose: "mv^2", "x_1", "r^{n+1}".
# The base is capped at three characters and both ends are anchored on
# non-word boundaries so ordinary snake_case ("question_id", "diagram_status")
# cannot match -- that false positive would corrupt real prose, which is far
# worse than leaving one caret unrendered.
_NAKED_SUPSUB_RE = re.compile(
    r"(?<![\\$\w])"
    r"([A-Za-z0-9]{1,3}(?:\^\{[^{}]*\}|\^[A-Za-z0-9]|_\{[^{}]*\}|_[A-Za-z0-9])+)"
    r"(?![A-Za-z0-9_])"
)
# What is left of that pattern after the fix above has run -- a braced script in
# prose is unambiguously escaped math, whatever shape the base was.
_PROSE_SCRIPT_RE = re.compile(r"\^\{|_\{")

# Commands whose braced arguments are mandatory. A missing one is the single
# most common cause of a KaTeX ParseError in practice.
_ARITY_2 = frozenset({
    "frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom",
    "overset", "underset", "stackrel",
})
_ARITY_1 = frozenset({
    "sqrt", "vec", "hat", "bar", "overline", "underline", "widehat",
    "widetilde", "tilde", "dot", "ddot", "boxed", "text", "textrm", "textbf",
    "textit", "mathrm", "mathbf", "mathit", "mathcal", "mathbb", "mathfrak",
    "mathsf", "mathtt", "operatorname",
})

# KaTeX's supported-function set, trimmed to what an exam paper can plausibly
# use. Generous on purpose: a false positive here costs a needless LLM call,
# so the list errs towards allowing rather than flagging.
_KATEX_WORDS = """
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi
psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
frac dfrac tfrac cfrac binom dbinom tbinom over atop choose
sqrt root sum prod coprod int iint iiint oint lim limsup liminf inf sup max min
sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth
log ln lg exp det dim ker deg gcd hom arg Pr
cdot cdots ldots dots vdots ddots times div pm mp ast star circ bullet
leq geq neq ne le ge ll gg approx equiv sim simeq cong propto perp parallel
subset supset subseteq supseteq in notin ni cup cap setminus emptyset varnothing
forall exists nexists neg lnot land lor implies iff to gets mapsto
rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow
longrightarrow longleftarrow rightleftharpoons uparrow downarrow
infty partial nabla prime degree angle measuredangle triangle square
vec hat bar tilde dot ddot overline underline widehat widetilde overrightarrow
overleftarrow underbrace overbrace boxed
mathrm mathbf mathit mathcal mathbb mathfrak mathsf mathtt mathnormal
text textrm textbf textit textsf texttt operatorname
left right big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr
langle rangle lvert rvert lVert rVert vert Vert lfloor rfloor lceil rceil
quad qquad space hspace phantom hphantom vphantom
begin end nonumber label
displaystyle textstyle scriptstyle limits nolimits
color textcolor colorbox fcolorbox
substack stackrel underset overset
matrix pmatrix bmatrix vmatrix Vmatrix smallmatrix array cases aligned gathered
split align gather equation
ell hbar imath jmath aleph Re Im wp
mathring acute grave breve check
not surd top bot vdash dashv models
because therefore ldotp cdotp
"""
KATEX_COMMANDS = frozenset(_KATEX_WORDS.split())


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def _find_delim(text: str, start: int, delim: str) -> int:
    """Index of the next unescaped `delim` at or after `start`, or -1."""
    j, n = start, len(text)
    while j < n:
        if text[j] == "\\" and j + 1 < n:
            j += 2
            continue
        if text.startswith(delim, j):
            return j
        j += 1
    return -1


def _scan(text: str) -> tuple[list, bool]:
    """Split `text` into ("text"|"inline"|"block", value) segments.

    Mirrors MathText.splitMath in the frontend so the backend's idea of what is
    math matches what KaTeX will actually be handed -- with one addition: this
    one honours backslash escapes, so an escaped dollar is currency rather than
    a delimiter.

    The bool is True when a `$` or `$$` was left open. The frontend renders that
    tail as literal text, which is exactly the "raw LaTeX on the page" symptom.
    """
    segs: list = []
    unterminated = False
    buf: list = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(text[i:i + 2])
            i += 2
            continue
        if ch == "$":
            block = text.startswith("$$", i)
            width = 2 if block else 1
            close = _find_delim(text, i + width, "$$" if block else "$")
            if close == -1:
                unterminated = True
                buf.append(text[i:])
                i = n
                break
            if buf:
                segs.append(("text", "".join(buf)))
                buf = []
            segs.append(("block" if block else "inline", text[i + width:close]))
            i = close + width
            continue
        buf.append(ch)
        i += 1
    if buf:
        segs.append(("text", "".join(buf)))
    return segs, unterminated


def _read_group(s: str, i: int) -> int:
    """Index just past the balanced `{...}` starting at `i`, or -1."""
    if i >= len(s) or s[i] != "{":
        return -1
    depth, j, n = 0, i, len(s)
    while j < n:
        if s[j] == "\\" and j + 1 < n:
            j += 2
            continue
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _braces_balanced(s: str) -> bool:
    depth, i, n = 0, 0, len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def _bad_arity(s: str) -> bool:
    """True when a command that requires braced arguments is missing one."""
    for m in _COMMAND_RE.finditer(s):
        name = m.group(1)
        need = 2 if name in _ARITY_2 else (1 if name in _ARITY_1 else 0)
        if not need:
            continue
        j = m.end()
        # \sqrt takes an optional [n] index before its argument.
        if name == "sqrt" and j < len(s) and s[j] == "[":
            close = s.find("]", j)
            if close == -1:
                return True
            j = close + 1
        for _ in range(need):
            while j < len(s) and s[j] in " \t":
                j += 1
            j = _read_group(s, j)
            if j == -1:
                return True
    return False


# ---------------------------------------------------------------------------
# Deterministic repair
# ---------------------------------------------------------------------------
def _alt_delims(s: str) -> str:
    r"""\(...\) and \[...\] -> $...$ and $$...$$.

    The prompts only ever ask for dollar delimiters and nothing downstream
    recognises the others, so a model reaching for LaTeX habit produces text
    that renders as literal backslashes. This is the single highest-yield fix.
    """
    s = re.sub(r"\\\[", "$$", s)
    s = re.sub(r"\\\]", "$$", s)
    s = re.sub(r"\\\(", "$", s)
    s = re.sub(r"\\\)", "$", s)
    return s


# Inner text of an inline segment that is really the gap between two currency
# signs: a number that ends at a word boundary, then ordinary prose with no
# math characters in it. "$10 to $20" scans as inline math "10 to ".
_CURRENCY_GAP_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?\b[^\\^_{}$+=*/<>-]*$")
_PURE_NUMBER_RE = re.compile(r"^[\d,.\s]+$")


def _is_currency_gap(inner: str) -> bool:
    return (
        len(inner) < 60
        and bool(_CURRENCY_GAP_RE.match(inner))
        and bool(re.search(r"[A-Za-z]", inner))
    )


def _expand_unicode_digits(s: str) -> str:
    """Superscript/subscript characters to their plain digits.

    Only used for comparison (see _numbers), never for output.
    """
    for table in (_SUPERSCRIPTS, _SUBSCRIPTS):
        for uni, plain in table.items():
            s = s.replace(uni, plain)
    return s


def _fix_math(s: str) -> str:
    """Repairs applied inside a `$...$` segment."""
    s = re.sub(r"\\text(?:rm|bf|it|sf|tt|normal)?\{([^{}]*)\}", r"\\mathrm{\1}", s)
    s = re.sub(r"\\operatorname\*?\{([^{}]*)\}", r"\\mathrm{\1}", s)
    s = s.replace("\\degree", "^\\circ ")
    s = _SUP_RUN_RE.sub(
        lambda m: "^{" + "".join(_SUPERSCRIPTS[c] for c in m.group(0)) + "}", s
    )
    s = _SUB_RUN_RE.sub(
        lambda m: "_{" + "".join(_SUBSCRIPTS[c] for c in m.group(0)) + "}", s
    )
    for uni, tex in _UNICODE_MATH.items():
        s = s.replace(uni, tex)
    # A trailing row separator with no row after it is a KaTeX error.
    s = re.sub(r"\\\\\s*$", "", s)
    return s


def _fix_prose(s: str) -> str:
    """Repairs applied outside any `$...$` segment.

    Order matters: naked commands are wrapped BEFORE Unicode symbols are
    converted, otherwise the ``\\circ`` this function just emitted would be
    re-wrapped by the naked-command pass and end up double-delimited.
    """
    # A bare dollar reaching this function is by definition not a delimiter --
    # the scanner would have consumed it if it had a partner. Followed by a
    # digit it is a price, and escaping it here is what stops a single "$12.50"
    # in a word problem from reading as an unterminated math span.
    s = re.sub(r"(?<!\\)\$(?=\d)", r"\\$", s)
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", s)
    s = re.sub(r"__([^_\n]+)__", r"\1", s)
    s = _NAKED_RE.sub(lambda m: "$" + m.group(0) + "$", s)
    s = _NAKED_SUPSUB_RE.sub(lambda m: "$" + m.group(1) + "$", s)
    s = _SUP_RUN_RE.sub(
        lambda m: "$^{" + "".join(_SUPERSCRIPTS[c] for c in m.group(0)) + "}$", s
    )
    s = _SUB_RUN_RE.sub(
        lambda m: "$_{" + "".join(_SUBSCRIPTS[c] for c in m.group(0)) + "}$", s
    )
    for uni, tex in _UNICODE_MATH.items():
        if uni == "\u2212":
            s = s.replace(uni, "-")
        else:
            s = s.replace(uni, "$" + tex.strip() + "$")
    return s


def _deterministic(text: str) -> str:
    if not text or not text.strip():
        return text
    s = _alt_delims(text)
    segs, _ = _scan(s)
    out: list = []
    for kind, val in segs:
        if kind == "text":
            out.append(_fix_prose(val))
        elif kind == "block":
            out.append("$$" + _fix_math(val) + "$$")
        elif _PURE_NUMBER_RE.match(val):
            # "$60$" carries no maths; rendering it as text keeps a stray
            # delimiter from swallowing the rest of the sentence.
            out.append(val)
        elif _is_currency_gap(val):
            out.append("\\$" + val + "\\$")
        else:
            out.append("$" + _fix_math(val) + "$")
    return "".join(out)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate(text: str) -> list:
    """Issue codes for `text`. An empty list means KaTeX should render it."""
    if not text or not text.strip():
        return []
    issues: set = set()
    segs, unterminated = _scan(text)
    if unterminated:
        issues.add("unbalanced_delims")
    for kind, val in segs:
        if kind == "text":
            if _NAKED_RE.search(val) or _PROSE_SCRIPT_RE.search(val):
                issues.add("raw_latex_outside_math")
            if _LEAK_RE.search(val):
                issues.add("unicode_math_leak")
            continue
        if not val.strip():
            issues.add("empty_math")
            continue
        if not _braces_balanced(val):
            issues.add("unbalanced_braces")
        if _bad_arity(val):
            issues.add("bad_arity")
        for name in _COMMAND_RE.findall(val):
            if name not in KATEX_COMMANDS:
                issues.add("unknown_command")
                break
        if _LEAK_RE.search(val):
            issues.add("unicode_math_leak")
    return sorted(issues)


def repair_text(s: str) -> tuple:
    """Deterministic repair only. Returns (fixed, issues still outstanding).

    Safe to call from anywhere -- no I/O, no model, no exceptions.
    """
    fixed = _deterministic(s)
    return fixed, validate(fixed)


# ---------------------------------------------------------------------------
# Guardrails on model output
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_NORM_STRIP_RE = re.compile(r"(\\[\[\]()])|[$\s{}]")


def _numbers(s: str) -> list:
    return sorted(_NUMBER_RE.findall(_expand_unicode_digits(s)))


def _norm(s: str) -> str:
    """Drop the things a legitimate repair is allowed to change."""
    return _NORM_STRIP_RE.sub("", s)


def _accept(original: str, fixed: str) -> tuple:
    """Whether a model-returned repair may replace the original.

    A repair model that quietly rewrites a physics answer does more damage than
    the broken formula it was asked to fix, so a fix has to prove three things:
    it kept every number, it actually improved the validator's verdict, and it
    did not stray far from the original text.
    """
    if not fixed or not fixed.strip():
        return False, "empty"
    if _numbers(original) != _numbers(fixed):
        return False, "numbers_changed"
    before, after = validate(original), validate(fixed)
    if len(after) >= len(before):
        return False, "not_improved"
    ratio = difflib.SequenceMatcher(None, _norm(original), _norm(fixed)).ratio()
    if ratio < SIMILARITY_FLOOR:
        return False, "too_different (%.2f)" % ratio
    return True, ""


# ---------------------------------------------------------------------------
# LLM repair pass
# ---------------------------------------------------------------------------
async def _llm_fix(items: list) -> dict:
    """One batched repair call. Returns id -> proposed text; {} on any failure.

    Reuses chat_complete so retries, model fallback and token accounting are
    inherited rather than reimplemented.
    """
    chain = [REPAIR_MODEL] if REPAIR_MODEL else None
    try:
        raw = await asyncio.wait_for(
            chat_complete(
                system_message=LATEX_REPAIR_SYSTEM,
                user_text=latex_repair_prompt(items),
                provider_chain=chain,
            ),
            timeout=REPAIR_TIMEOUT,
        )
        data = parse_json_response(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[latex] repair call failed, keeping originals: {e}")
        return {}
    out: dict = {}
    for fix in (data.get("fixes") if isinstance(data, dict) else None) or []:
        if not isinstance(fix, dict):
            continue
        fid, txt = fix.get("id"), fix.get("text")
        if fid is not None and isinstance(txt, str):
            out[str(fid)] = txt
    return out


async def repair_items(items: dict, *, ref: str = "") -> tuple:
    """Check and repair a flat id -> text map.

    Returns (repaired map, stats). The stats dict is written straight onto the
    Mongo document as `latex_check` -- no migration needed, the collection is
    schemaless and everything is written with $set.
    """
    stats = {
        "checked": len(items),
        "auto_fixed": 0,
        "llm_fixed": 0,
        "unrepaired": 0,
        "issues": [],
        "at": utcnow_iso(),
    }
    if not items:
        return {}, stats

    fixed: dict = {}
    broken: list = []
    seen_issues: set = set()

    for key, text in items.items():
        if not isinstance(text, str):
            fixed[key] = text
            continue
        candidate, issues = repair_text(text)
        if candidate != text:
            stats["auto_fixed"] += 1
        fixed[key] = candidate
        if issues:
            seen_issues.update(issues)
            broken.append({"id": key, "text": candidate, "issues": issues})

    if broken and REPAIR_ENABLED:
        for start in range(0, len(broken), REPAIR_MAX_ITEMS):
            chunk = broken[start:start + REPAIR_MAX_ITEMS]
            proposals = await _llm_fix(chunk)
            for item in chunk:
                proposed = proposals.get(item["id"])
                if proposed is None:
                    continue
                ok, why = _accept(item["text"], proposed)
                if ok:
                    fixed[item["id"]] = proposed
                    item["repaired"] = True
                    stats["llm_fixed"] += 1
                else:
                    logger.info(
                        f"[latex] {ref or '-'} rejected repair for {item['id']}: {why}"
                    )

    stats["unrepaired"] = sum(1 for item in broken if not item.get("repaired"))
    stats["issues"] = sorted(seen_issues)
    if broken:
        logger.info(
            f"[latex] {ref or '-'} checked={stats['checked']} "
            f"auto={stats['auto_fixed']} llm={stats['llm_fixed']} "
            f"unrepaired={stats['unrepaired']} issues={','.join(stats['issues'])}"
        )
    return fixed, stats


# ---------------------------------------------------------------------------
# Call sites
# ---------------------------------------------------------------------------
_Q_KEY = "::q"
_OPT_KEY = "::o"


async def repair_sections(sections: list, *, ref: str = "") -> dict:
    """Check and repair every question and MCQ option in place.

    Call this BEFORE apply_diagram_policy so diagram_policy.plain_text() gets
    already-clean LaTeX, and before the $set that flips generation_status to
    "ready" so a teacher never sees the broken version.
    """
    items: dict = {}
    for s in sections or []:
        for q in s.get("questions", []) or []:
            qid = q.get("id")
            if not qid:
                continue
            if isinstance(q.get("question"), str):
                items[f"{qid}{_Q_KEY}"] = q["question"]
            for idx, opt in enumerate(q.get("options") or []):
                if isinstance(opt, str):
                    items[f"{qid}{_OPT_KEY}{idx}"] = opt

    fixed, stats = await repair_items(items, ref=ref)

    for s in sections or []:
        for q in s.get("questions", []) or []:
            qid = q.get("id")
            if not qid:
                continue
            key = f"{qid}{_Q_KEY}"
            if key in fixed:
                q["question"] = fixed[key]
            opts = q.get("options")
            if isinstance(opts, list):
                for idx in range(len(opts)):
                    okey = f"{qid}{_OPT_KEY}{idx}"
                    if okey in fixed:
                        opts[idx] = fixed[okey]
    return stats


async def repair_answers(answers: dict, *, ref: str = "") -> dict:
    """Check and repair a question_id -> answer map in place."""
    items = {k: v for k, v in (answers or {}).items() if isinstance(v, str)}
    fixed, stats = await repair_items(items, ref=ref)
    answers.update(fixed)
    return stats
