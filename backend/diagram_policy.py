"""Deterministic gate deciding which questions may carry a figure.

The prompt asks the model to flag only questions that genuinely need a diagram
(see prompts.DIAGRAM_POLICY), but the weaker wording that preceded it was
routinely ignored, so the rules that actually matter are enforced here instead.

Two failure modes this guards against:

1. The figure IS the task. "Draw the ray diagram and find the image distance",
   or any linear-programming question where plotting the feasible region is the
   work — handing the student the picture deletes the question. No prompt
   wording is worth trusting for this; a question that tells the student to draw
   never gets a figure, full stop.

2. The figure gives away the answer. The model likes to write the value being
   asked for into the spec ("angle APB = 60 degrees") and the image model
   faithfully prints it. Measurements are therefore whitelisted: a number is
   allowed onto the figure only if it appears in `given_values`, and anything
   else is rewritten to "?" before the image prompt is built.

apply_diagram_policy() is the single entry point; workers.py and
competitive_routes.py both run a paper's sections through it before any image is
requested.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Ceiling on figures per paper. Lives here rather than in the prompt because the
# prompt can only ask; this is the count that actually reaches the image
# provider, and it also bounds the per-paper burst workers.py fires.
MAX_DIAGRAMS_PER_PAPER = 5

# Questions whose instruction to the student IS to produce the drawing. The
# negative lookahead spares the idiomatic senses of "draw" that have nothing to
# do with figures ("draw a conclusion", "draw a comparison"). "graph" is absent
# from the verb list on purpose — it is far more often a noun ("the graph
# shows..."), and its imperative sense is already covered by "plot".
_DRAW_TASK = re.compile(
    r"\b(?:draw|sketch|construct|plot|shade|trace)\s+"
    r"(?!(?:a\s+|an\s+|the\s+)?"
    r"(?:conclusion|inference|comparison|analogy|parallel|attention|distinction))",
    re.IGNORECASE,
)

# Topics where the whole exercise is building the diagram yourself. LPP is the
# case that prompted this: the feasible region is the answer, so a supplied
# graph reduces a 6-mark question to reading a corner off a picture. The bare
# abbreviation is listed because papers routinely say "Solve the LPP:" and never
# spell it out.
_SELF_DRAWN_TOPIC = re.compile(
    r"\b(?:LPP|linear\s+programm\w*|feasible\s+region|objective\s+function|"
    r"corner[-\s]point|graphical\s+method)\b",
    re.IGNORECASE,
)

# Units that mark a number as a measurement rather than a count. Only numbers
# adjacent to one of these (or sitting just after an "=") are treated as
# leakable values — otherwise "3 resistors in series" would be scrubbed as if
# the 3 were an answer.
_UNIT = (
    r"(?:cm|mm|km|m|kg|g|mg|s|ms|min|h|N|J|W|V|A|C|F|T|Hz|Pa|K|mol|ml|l|L|"
    r"ohm|ohms|degree|degrees|deg|rad|%|°)"
)
# A number carrying a unit: "5 cm", "30 degrees". Requiring the unit is what
# keeps "3 resistors in series" and "parts 1-4" out — an earlier version also
# accepted a bare number at the end of a string and duly flagged those as
# answers.
_MEASUREMENT = re.compile(
    rf"(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)(?![A-Za-z0-9_])(?=\s*(?:{_UNIT})\b)",
    re.IGNORECASE,
)
# A number assigned to something — "x = 12" — which is a value even with no
# unit attached. The leading whitespace is captured so a substitution can put it
# back.
_ASSIGNED = re.compile(r"(?<==)(\s*)(\d+(?:\.\d+)?)(?![A-Za-z0-9_])")

_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?![A-Za-z0-9_])")


def _has_value(text: str) -> bool:
    """True when `text` states a measurement, as opposed to merely containing a
    digit. "angle APB = 60 degrees" does; "names of parts 1-4" does not."""
    return bool(_MEASUREMENT.search(text) or _ASSIGNED.search(text))


# The question prompt requires every expression to be wrapped in LaTeX, so
# specs arrive as '$\angle APB=56^\circ$'. That has to be flattened before
# anything else touches it, for two reasons: an image model prints the raw
# markup on the figure, and '56^\circ' does not look like a measurement to
# _MEASUREMENT, so a stray value would sail past the scrub.
_LATEX_WORDS = {
    r"\angle": "angle",
    r"\triangle": "triangle",
    r"\parallel": "parallel to",
    r"\perp": "perpendicular to",
    r"\times": "x",
    r"\cdot": ".",
    r"\theta": "theta",
    r"\alpha": "alpha",
    r"\beta": "beta",
    r"\pi": "pi",
    r"\omega": "omega",
    r"\lambda": "lambda",
    r"\mu": "mu",
    r"\Delta": "delta",
}


def plain_text(s: str) -> str:
    """Flatten LaTeX to the plain text an image model can actually letter."""
    if not s:
        return ""
    s = s.replace("$", "")
    # Spacing commands first. '5\,\mathrm{cm}' must end up as '5 cm', not
    # '5,cm' — the latter reads as a bare number to _MEASUREMENT and would let
    # an undeclared value through the scrub.
    s = re.sub(r"\\(?:[,;:!]|q?quad)", " ", s)
    s = re.sub(r"\^\{?\\circ\}?", " degrees", s)
    s = re.sub(r"\\(?:mathrm|text|mathbf|mathit)\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"\1/\2", s)
    for tex, word in _LATEX_WORDS.items():
        s = re.sub(re.escape(tex) + r"(?![A-Za-z])", word, s)
    s = re.sub(r"\\([A-Za-z]+)", r"\1", s)  # any command left: keep its name
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return " ".join(s.split())


def _as_list(value: Any) -> list[str]:
    """Coerce a spec field to a clean list of strings.

    The model returns these as a list most of the time and as a comma-joined
    string the rest of the time; both are accepted rather than rejecting an
    otherwise-good spec over shape.
    """
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = [str(v) for v in value]
    else:
        return []
    return [p for p in (plain_text(x) for x in parts) if p]


def _subject_of(entry: str) -> str:
    """The thing an entry is about, with any value stripped off.

    "angle APB - mark as x"  -> "angle apb"
    "OA = 5 cm"              -> "oa"

    Used to catch a spec that lists the same quantity as both given and unknown,
    which means the answer is sitting in given_values.
    """
    head = re.split(r"[=:\-–—(]", entry, maxsplit=1)[0]
    return " ".join(head.lower().split())


def _allowed_numbers(given_values: list[str]) -> set[str]:
    """Every numeric literal the figure is permitted to print."""
    out: set[str] = set()
    for g in given_values:
        out.update(_NUMBER.findall(g))
    return out


def normalise_spec(q: dict) -> dict | None:
    """Pull a usable diagram spec off a question, or None if there isn't one.

    Papers generated before this change carry only `diagram_description`. Those
    are wrapped into a spec with an empty givens list rather than rejected — a
    legacy paper keeps its figure, it just gets no measurement whitelist (there
    is nothing to whitelist against).
    """
    spec = q.get("diagram_spec")
    if isinstance(spec, dict) and (spec.get("figure") or spec.get("labels")):
        out = {
            "figure": plain_text(str(spec.get("figure") or "")),
            "labels": _as_list(spec.get("labels")),
            "given_values": _as_list(spec.get("given_values")),
            "unknowns": _as_list(spec.get("unknowns")),
            "why_required": str(spec.get("why_required") or "").strip(),
        }
        # Carried through so a second pass over an already-processed paper sees
        # the same spec the first one did. apply_diagram_policy runs twice for
        # every paper (once at generation, once before the images are paid
        # for); without this, a legacy spec written back by the first pass would
        # fail the second on its empty why_required and lose its figure.
        if spec.get("legacy"):
            out["legacy"] = True
        return out
    legacy = (q.get("diagram_description") or "").strip()
    if legacy:
        return {
            "figure": legacy,
            "labels": [],
            "given_values": [],
            "unknowns": [],
            "why_required": "",
            "legacy": True,
        }
    return None


def vet_question_diagram(q: dict) -> tuple[bool, str]:
    """(allowed, reason) for one question's figure.

    `reason` is only meaningful when allowed is False; it is logged so we can
    see what the model keeps trying to draw.
    """
    text = (q.get("question") or "")
    if _DRAW_TASK.search(text):
        return False, "question asks the student to produce the drawing"

    spec = normalise_spec(q)
    if spec is None:
        return False, "needs_diagram set but no diagram spec supplied"

    # The self-drawn-topic check also reads the figure text, because that is
    # where the model gives itself away: an LPP question phrased "Solve the
    # LPP: maximise Z..." names neither the region nor the graph, but its spec
    # asks for "a graph showing the feasible region shaded". The draw-verb
    # check above deliberately does NOT read the figure text — a spec that
    # opens "draw a circle with centre O" is a perfectly good figure.
    if _SELF_DRAWN_TOPIC.search(f"{text}\n{spec['figure']}"):
        return False, "topic requires the student to construct the figure"

    if spec.get("legacy"):
        # Pre-existing paper: the question-text checks above are all we can
        # apply, and they passed.
        return True, ""

    if not spec["why_required"]:
        return False, "spec does not justify why the figure is required"
    if not spec["labels"] and not spec["given_values"]:
        return False, "figure carries no labels and no data — decorative"

    given_subjects = {_subject_of(g) for g in spec["given_values"] if _subject_of(g)}
    for u in spec["unknowns"]:
        if _has_value(u):
            return False, f"unknown carries a value, which is the answer: {u!r}"
        subj = _subject_of(u)
        if subj and subj in given_subjects:
            return False, f"unknown {subj!r} is also listed as a given value"

    return True, ""


def scrub_spec(spec: dict) -> dict:
    """Rewrite any measurement not backed by `given_values` as '?'.

    A stray value in the free-text `figure` line is the last way an answer can
    reach the image prompt after vetting. Replacing it rather than deleting the
    fragment keeps the figure readable and says the right thing: "chord AB = ?
    cm" is exactly the unknown the student is meant to find.

    Counts and identifiers are untouched — only numbers adjacent to a unit or
    following an "=" are considered measurements (see _MEASUREMENT).
    """
    if spec.get("legacy"):
        return spec
    allowed = _allowed_numbers(spec["given_values"])

    def _sub(text: str) -> str:
        text = _MEASUREMENT.sub(
            lambda m: m.group(1) if m.group(1) in allowed else "?", text
        )
        return _ASSIGNED.sub(
            lambda m: m.group(1) + (m.group(2) if m.group(2) in allowed else "?"),
            text,
        )

    out = dict(spec)
    out["figure"] = _sub(spec["figure"])
    out["labels"] = [_sub(x) for x in spec["labels"]]
    return out


def spec_to_description(spec: dict) -> str:
    """Flatten a spec back into the prose `diagram_description` field.

    Kept so stored papers, the PDF renderers and anything still calling
    generate_diagram() with a string keep working unchanged.
    """
    if spec.get("legacy"):
        return spec["figure"]
    parts = [spec["figure"].rstrip(". ")]
    if spec["labels"]:
        parts.append("Labelled: " + ", ".join(spec["labels"]))
    if spec["given_values"]:
        parts.append("Given: " + "; ".join(spec["given_values"]))
    if spec["unknowns"]:
        parts.append("Unknown (symbol only): " + "; ".join(spec["unknowns"]))
    return ". ".join(p for p in parts if p) + "."


def apply_diagram_policy(
    sections: list,
    *,
    max_diagrams: int = MAX_DIAGRAMS_PER_PAPER,
    ref: str = "",
) -> int:
    """Vet, scrub and cap every flagged question in a paper, in place.

    Returns the number of questions that kept their figure. Questions that fail
    lose `needs_diagram` and both diagram fields, so nothing downstream can
    resurrect them.
    """
    kept = 0
    for s in sections:
        for q in s.get("questions", []):
            if not q.get("needs_diagram"):
                continue

            allowed, reason = vet_question_diagram(q)
            if allowed and kept >= max_diagrams:
                allowed, reason = False, f"paper already has {max_diagrams} figures"

            if not allowed:
                logger.info(
                    "Diagram rejected%s: %s | %s",
                    f" [{ref}]" if ref else "",
                    reason,
                    (q.get("question") or "")[:80],
                )
                q["needs_diagram"] = False
                q.pop("diagram_spec", None)
                q.pop("diagram_description", None)
                continue

            spec = scrub_spec(normalise_spec(q))
            q["diagram_spec"] = spec
            q["diagram_description"] = spec_to_description(spec)
            kept += 1
    return kept
