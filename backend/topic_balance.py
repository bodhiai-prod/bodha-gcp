"""Measuring whether a generated paper actually honoured its topic weights.

`prompts.resolve_shares` tells the model how many questions each topic must
get. Until this module existed, nothing ever checked. The quota was rendered
into the prompt as prose and the result was trusted, which is why a paper with
one heavy topic and three light ones could come back almost entirely about the
heavy one and still look successful.

Two jobs, both free and deterministic:

1. ``canonical_topic()`` -- map the label the model wrote onto the topic the
   teacher actually asked for. Nothing validates `q["topic"]` anywhere in the
   pipeline, so a model that writes "newtons laws" for a requested "Newton's
   Laws of Motion" produces a question that is correctly on-topic and
   uncountable. Counting has to come after canonicalising or it measures
   spelling, not coverage.
2. ``measure_coverage()`` -- target vs actual per topic, stored on the paper as
   `topic_coverage` and used by the top-up worker to ask for what is missing by
   name.

No I/O, no model, no exceptions.
"""
from __future__ import annotations

import difflib
import os
import re

from deps import utcnow_iso
from prompts import resolve_shares

# How close a model-written label must be to a requested topic before we accept
# it as that topic. Below this the question is counted as `unmatched` rather
# than credited to the wrong topic -- a miscount is worse than an unknown.
MATCH_FLOOR = float(os.environ.get("TOPIC_MATCH_FLOOR", "0.72"))

_APOSTROPHE_RE = re.compile(r"['‘’]")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")
# Dropped before token containment so "of" and "the" cannot make two unrelated
# topics look like one is a subset of the other.
_STOP = frozenset(
    "a an and at by for from in into of on or the to with".split()
)


def _norm(s: str) -> str:
    """Casefold, drop punctuation, collapse whitespace.

    Apostrophes are DELETED rather than turned into spaces. "Newton's Laws"
    must normalise to "newtons laws" so it matches a model that wrote "newtons
    laws"; turning the apostrophe into a space yields "newton s laws", whose
    stray token defeats both containment and the difflib ratio.
    """
    s = _APOSTROPHE_RE.sub("", (s or "").strip().lower())
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", s)).strip()


def _tokens(norm: str) -> set:
    return {t for t in norm.split() if t not in _STOP}


def canonical_topic(label: str, topic_names: list) -> str | None:
    """Return the requested topic `label` refers to, or None if it matches none.

    Tried cheapest first: exact, normalised-exact, token containment, then a
    difflib ratio. Containment earns its own tier because the topic list mixes
    chapter names with subtopic names -- the frontend flattens both into one
    pickable list -- so "Laws of Motion" against a requested "Newton's Laws of
    Motion" is a real and frequent case that scores only ~0.7 on raw ratio.
    """
    if not label or not topic_names:
        return None
    label = str(label).strip()
    if label in topic_names:
        return label

    target = _norm(label)
    if not target:
        return None

    normalised = {name: _norm(name) for name in topic_names}
    for name, norm in normalised.items():
        if norm == target:
            return name

    target_tokens = _tokens(target)
    best_contained = None
    best_overlap = 0
    for name, norm in normalised.items():
        tokens = _tokens(norm)
        if not tokens or not target_tokens:
            continue
        if tokens <= target_tokens or target_tokens <= tokens:
            overlap = len(tokens & target_tokens)
            if overlap > best_overlap:
                best_overlap, best_contained = overlap, name
    if best_contained:
        return best_contained

    best_name, best_ratio = None, 0.0
    for name, norm in normalised.items():
        ratio = difflib.SequenceMatcher(None, target, norm).ratio()
        if ratio > best_ratio:
            best_ratio, best_name = ratio, name
    return best_name if best_ratio >= MATCH_FLOOR else None


def canonicalise_topics(sections: list, topic_names: list) -> int:
    """Rewrite each question's `topic` to the requested spelling, in place.

    Returns the number of questions whose label matched nothing. Beyond making
    the counts below possible, this is what stops two spellings of one topic
    becoming two rows in a student's mock-test breakdown (student_routes groups
    by the raw value).
    """
    unmatched = 0
    for s in sections or []:
        for q in s.get("questions") or []:
            if not isinstance(q, dict):
                continue
            match = canonical_topic(q.get("topic"), topic_names)
            if match is None:
                unmatched += 1
                continue
            q["topic"] = match
    return unmatched


def measure_coverage(sections: list, topics_weighted: list, target_q: int) -> dict:
    """Target vs actual questions per topic. Canonicalises labels as it goes.

    The stats dict is written straight onto the Mongo document as
    `topic_coverage` -- the collection is schemaless and everything is written
    with $set, so no migration is needed.
    """
    names = [
        str(t.get("name", "")).strip()
        for t in topics_weighted or []
        if str(t.get("name", "")).strip()
    ]
    stats = {
        "per_topic": {},
        "unmatched": 0,
        "under": [],
        "over": [],
        "at": utcnow_iso(),
    }
    if not names:
        return stats

    stats["unmatched"] = canonicalise_topics(sections, names)

    actual = {name: 0 for name in names}
    for s in sections or []:
        for q in s.get("questions") or []:
            if isinstance(q, dict) and q.get("topic") in actual:
                actual[q["topic"]] += 1

    shares = resolve_shares(topics_weighted, target_q)
    for name in names:
        want, got = int(shares.get(name, 0)), actual[name]
        stats["per_topic"][name] = {"target": want, "actual": got}
        if got < want:
            stats["under"].append(name)
        elif got > want:
            stats["over"].append(name)
    return stats


def topic_shortfall(coverage: dict) -> dict:
    """{topic: how many more it needs}, for the topics that came up short.

    Fed to qgen_prompt as `topic_targets` so a refill asks for the topics that
    are actually missing instead of being told to "spread them around".
    """
    out: dict = {}
    for name, row in ((coverage or {}).get("per_topic") or {}).items():
        # Read back off a schemaless Mongo document, so nothing about the shape
        # is guaranteed. A malformed row must not take the refill down with it.
        if not isinstance(row, dict):
            continue
        try:
            gap = int(row.get("target", 0)) - int(row.get("actual", 0))
        except (TypeError, ValueError):
            continue
        if gap > 0:
            out[name] = gap
    return out
