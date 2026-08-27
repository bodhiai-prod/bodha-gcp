"""Near-duplicate detection for generated question papers.

The model is told not to repeat itself and mostly obeys, but two situations
defeat the instruction entirely:

* a narrow topic list, where there are only so many ways to ask the thing;
* competitive papers, whose batches run CONCURRENTLY and so cannot see each
  other's output at all.

What comes back is rarely a byte-identical repeat -- the only dedupe that
existed (llm_adapter.parse_json_response) matches exact strings and never
fires here. It is the same question with one quantity changed: "a body of mass
2 kg" and "a body of mass 4 kg", identical in every other word.

Three layers, cheapest first:

1. ``_normalise()`` -- LaTeX flattened via diagram_policy.plain_text, lowercased.
2. ``_similarity()`` -- one TF-IDF fit over the whole paper and a single
   cosine_similarity call for the full pairwise matrix. Same two sklearn calls
   rag.py already makes, fit once per paper instead of once per query.
3. ``_classify()`` -- above the plain threshold is a near-duplicate; above the
   lower numeral threshold WITH a different set of numbers is the "2x vs 4x"
   variant. It needs its own looser bar because a multi-digit quantity is
   several tokens of difference, so a pair that is otherwise word-for-word
   identical can dip under the plain threshold on the strength of the number
   alone.

Nothing here raises and nothing calls a model -- a paper this cannot score is
left exactly as it was found. It never drops more than MAX_DROP_RATIO of the
paper either: if the matrix claims half the questions are duplicates, the
threshold is wrong, and a half-length paper is worse than a repetitive one.
"""
from __future__ import annotations

import os
import re

from deps import logger, utcnow_iso
from diagram_policy import plain_text

# latex_repair owns "what counts as the same number" -- it expands Unicode
# digits before extracting, which a second implementation here would get wrong
# the first time a superscript slipped through.
from latex_repair import _numbers as _latex_numbers

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# false = score nothing, drop nothing. The kill switch.
DEDUPE_ENABLED = os.environ.get("QUESTION_DEDUPE_ENABLED", "true").strip().lower() not in (
    "false", "0", "no",
)
# Cosine score above which two questions are the same question reworded.
THRESHOLD = float(os.environ.get("QUESTION_DEDUPE_THRESHOLD", "0.82"))
# Lower bar, applied only when the two differ in their numbers. Deliberately
# looser: a differing multi-digit quantity costs several tokens, enough to pull
# an otherwise word-for-word identical pair under the threshold above. Measured
# against real stems, genuinely different questions sharing a template ("area of
# a circle" vs "circumference of a circle") sit around 0.71, so both bars keep
# a real margin.
NUMERAL_THRESHOLD = float(os.environ.get("QUESTION_DEDUPE_NUMERAL_THRESHOLD", "0.75"))
# Safety valve, as a fraction of the paper. See the module docstring.
MAX_DROP_RATIO = float(os.environ.get("QUESTION_DEDUPE_MAX_DROP_RATIO", "0.25"))
# Below this many questions the TF-IDF vocabulary is too thin to mean anything.
MIN_CORPUS = 2
# Content words a stem needs before its cosine score is worth believing.
# "Define inertia." carries two tokens; so does "Define momentum." Their score
# is near-total overlap on a two-word vocabulary, which says nothing about
# whether they are the same question. Short stems are compared for exact
# equality instead -- see _classify.
MIN_TOKENS = int(os.environ.get("QUESTION_DEDUPE_MIN_TOKENS", "5"))
# The vectoriser's own token rule: words of two characters or more. Single
# digits are NOT tokens to it, which is worth knowing -- "mass 2 kg" and
# "mass 4 kg" vectorise identically and are caught as a plain duplicate rather
# than by the numeral rule.
_TOKEN_RE = re.compile(r"\w\w+")


def _normalise(text: str) -> str:
    """Flatten LaTeX to comparable plain text.

    Uses diagram_policy.plain_text rather than rag._normalise. rag's version
    deletes every LaTeX command outright, which collapses "find $\\alpha$" and
    "find $\\beta$" into the same string -- it is built for matching a topic
    against a corpus, where losing a symbol is harmless. plain_text maps them to
    "alpha" and "beta" and turns \\frac{a}{b} into a/b, so symbols that ARE the
    difference between two questions survive to be compared.
    """
    return plain_text(text or "").lower()


def _stems(questions: list) -> list:
    return [_normalise(q.get("question") or "") for q in questions]


def _similarity(stems: list):
    """Full pairwise cosine matrix, or None if it cannot be computed."""
    try:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as e:  # noqa: BLE001
        logger.warning(f"[dedupe] scikit-learn unavailable, skipping: {e}")
        return None
    try:
        # use_idf=False on purpose. With IDF the score between two questions
        # depends on what ELSE is on the paper -- the same duplicated pair
        # scored 0.70 on a two-question paper and 0.72 on a five-question one,
        # so any fixed threshold silently means something different per paper.
        # Without it the vector for each question depends only on that question,
        # and the thresholds above mean one thing everywhere.
        vec = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), min_df=1, use_idf=False
        )
        mat = vec.fit_transform(stems)
        sims = cosine_similarity(mat)
        np.fill_diagonal(sims, 0.0)
        return sims
    except ValueError as e:  # all-stopword / empty vocabulary
        logger.info(f"[dedupe] corpus not vectorisable, skipping: {e}")
        return None


def _classify(score: float, a_text: str, b_text: str) -> str | None:
    """Why (or whether) these two count as the same question.

    A stem too short to carry MIN_TOKENS content words is judged on exact
    equality alone. Cosine over a two-word vocabulary reports near-total
    overlap for any two questions built from the same two words, which is a
    statement about the vocabulary rather than about the questions -- and
    acting on it deletes a teacher's work on no evidence.
    """
    if a_text == b_text:
        return "exact_duplicate"
    if min(len(_TOKEN_RE.findall(a_text)), len(_TOKEN_RE.findall(b_text))) < MIN_TOKENS:
        return None
    if score >= THRESHOLD:
        return "near_duplicate"
    if score >= NUMERAL_THRESHOLD and _latex_numbers(a_text) != _latex_numbers(b_text):
        return "numeral_variant"
    return None


def _over_represented(q: dict, coverage: dict | None) -> int:
    """How far this question's topic is above its quota. 0 when unknown.

    Used only to choose WHICH of a duplicate pair to drop. Thinning the topic
    that already has too many questions is free -- the pair has to lose one
    either way -- and it is what makes deduping and rebalancing a single
    operation rather than two that fight each other.
    """
    row = ((coverage or {}).get("per_topic") or {}).get(q.get("topic"))
    if not isinstance(row, dict):
        return 0
    try:
        return int(row.get("actual", 0)) - int(row.get("target", 0))
    except (TypeError, ValueError):
        return 0


def empty_stats(checked: int = 0) -> dict:
    """The `dedupe_check` shape, for papers that never ran the pass."""
    return {
        "checked": checked,
        "dropped": 0,
        "capped": False,
        "reasons": {},
        "pairs": [],
        "at": utcnow_iso(),
    }


def find_duplicates(
    questions: list, coverage: dict | None = None, *, protected: int = 0
) -> tuple:
    """Decide which questions to drop. Returns (drop_indices, pairs).

    `protected` marks a prefix of `questions` that may never be dropped -- the
    top-up paths pass the paper's existing questions there, so a new question
    that repeats an old one loses, never the other way round.

    Comparison is greedy and transitive-safe: once an index is marked for
    dropping it is neither compared against again nor able to evict anything
    else, so a run of three similar questions leaves one, not zero.
    """
    stems = _stems(questions)
    if len([s for s in stems if s.strip()]) < MIN_CORPUS:
        return set(), []
    sims = _similarity(stems)
    if sims is None:
        return set(), []

    dropped: set = set()
    pairs: list = []
    n = len(questions)
    for i in range(n):
        if i in dropped:
            continue
        for j in range(i + 1, n):
            if j in dropped:
                continue
            # Both already on the paper. Whatever the score says, neither is
            # ours to remove -- a top-up must never shrink the paper it is
            # meant to extend.
            if j < protected:
                continue
            score = float(sims[i][j])
            reason = _classify(score, stems[i], stems[j])
            if reason is None:
                continue
            # Keep whichever question's topic needs it more; the later one
            # loses ties, so a paper read top to bottom keeps its opening.
            if i < protected:
                loser = j
            elif _over_represented(questions[i], coverage) > _over_represented(
                questions[j], coverage
            ):
                loser = i
            else:
                loser = j
            dropped.add(loser)
            pairs.append({
                "kept": questions[j if loser == i else i].get("id"),
                "dropped": questions[loser].get("id"),
                "score": round(score, 3),
                "reason": reason,
            })
            if loser == i:
                break  # i is gone; stop comparing against it
    return dropped, pairs


def _apply(sections: list, drop_ids: set) -> None:
    for s in sections or []:
        s["questions"] = [
            q for q in (s.get("questions") or [])
            if not (isinstance(q, dict) and q.get("id") in drop_ids)
        ]


def dedupe_sections(
    sections: list,
    *,
    ref: str = "",
    coverage: dict | None = None,
    against: list | None = None,
) -> dict:
    """Remove near-duplicate questions from `sections`, in place.

    `against` holds sections whose questions must be compared with but never
    dropped -- the top-up paths pass the paper's existing content, so a top-up
    cannot re-ask what the paper already has.

    Call this AFTER the completeness pass (so ids exist) and BEFORE
    latex_repair.repair_sections, so no repair call is spent on a question that
    is about to be thrown away.
    """
    fresh = [
        q for s in sections or [] for q in (s.get("questions") or [])
        if isinstance(q, dict)
    ]
    existing = [
        q for s in against or [] for q in (s.get("questions") or [])
        if isinstance(q, dict)
    ]
    stats = empty_stats(len(fresh))
    if not fresh or not DEDUPE_ENABLED:
        return stats

    ordered = existing + fresh
    drop_idx, pairs = find_duplicates(ordered, coverage, protected=len(existing))

    # The valve is measured against the questions actually at risk, not the
    # combined corpus, so a small top-up against a large paper is not given a
    # generous allowance it never needed.
    #
    # Always at least one. The valve exists to stop a bad threshold gutting a
    # paper, not to stop small papers being deduped at all -- and a plain
    # int() of the ratio is zero for anything under four questions, which
    # silently disabled the whole feature on short papers and top-ups.
    limit = max(1, int(len(fresh) * MAX_DROP_RATIO))
    ranked = sorted(pairs, key=lambda p: -p["score"])
    if len(drop_idx) > limit:
        stats["capped"] = True
        logger.warning(
            f"[dedupe] {ref or '-'} {len(drop_idx)} of {len(fresh)} flagged, "
            f"capping at {limit} -- threshold is probably wrong"
        )
        ranked = ranked[:limit]

    drop_ids = {p["dropped"] for p in ranked if p["dropped"]}
    _apply(sections, drop_ids)

    stats["dropped"] = len(drop_ids)
    stats["pairs"] = ranked[:20]
    reasons: dict = {}
    for p in ranked:
        if p["dropped"] in drop_ids:
            reasons[p["reason"]] = reasons.get(p["reason"], 0) + 1
    stats["reasons"] = reasons
    if drop_ids:
        logger.info(
            f"[dedupe] {ref or '-'} checked={stats['checked']} "
            f"dropped={stats['dropped']} "
            f"reasons={','.join(f'{k}={v}' for k, v in sorted(reasons.items()))}"
        )
    return stats
