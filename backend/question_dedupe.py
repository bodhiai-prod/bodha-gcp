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

3. ``_review_duplicates()`` -- the pairs the score could not call either way
   go to a model, batched into one call. This is the only tier that costs
   anything, it only ever sees the borderline band, and a paper with no
   borderline pairs never makes the call. Cosine is a LEXICAL measure: it
   catches a reword only while the rewording reuses most of the same words, so
   a model that paraphrases well produces duplicates that are semantically
   identical, lexically distinct, and invisible to tiers 1 and 2.

Nothing here raises -- a paper this cannot score, or whose judge call fails, is
left exactly as the lexical tiers found it. Scored drops never exceed
MAX_DROP_RATIO of the paper either: if the matrix claims half the questions are
duplicates, the threshold is wrong, and a half-length paper is worse than a
repetitive one. Exact duplicates are exempt from that cap -- they involve no
threshold and no judgement.
"""
from __future__ import annotations

import asyncio
import os
import re

from deps import logger, utcnow_iso
from diagram_policy import plain_text
from llm_adapter import chat_complete, parse_json_response
from prompts import QUESTION_DEDUPE_SYSTEM, question_dedupe_prompt

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

# --- tier 3: the model that reads the pairs the score could not call ---------
# false keeps both free tiers and skips the model entirely, which is exactly
# how this module behaved before the tier existed.
LLM_ENABLED = os.environ.get(
    "QUESTION_DEDUPE_LLM_ENABLED", "true"
).strip().lower() not in ("false", "0", "no")
# Blank means "reuse whatever chain TEXT_PROVIDER already serves". Set it only
# to pin the judgement to a different (usually cheaper) model -- the job is a
# yes/no over two short strings.
LLM_MODEL = os.environ.get("QUESTION_DEDUPE_LLM_MODEL", "").strip()
# Characters kept from each stem when the paper is sent for review. The judge
# is reading for what a question ASKS, which its opening clause carries; the
# full text of 200 questions would mostly be spent on restating quantities the
# judgement does not turn on.
LLM_STEM_CHARS = int(os.environ.get("QUESTION_DEDUPE_LLM_STEM_CHARS", "220"))
# Budget for one judgement call. Enforced here rather than in chat_complete so
# a slow judge can never hold a generation open longer than its own share; on
# timeout the lexical verdict stands and the paper keeps both questions.
LLM_TIMEOUT = float(os.environ.get("QUESTION_DEDUPE_LLM_TIMEOUT", "60"))
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

    Two coverage shapes reach here. `topic_balance.measure_coverage` keys
    `per_topic` by topic NAME; `section_blueprints.allocation_coverage` keys the
    same field by `allocation_id` and counts each question's allocation rather
    than its topic. Looking up only by name meant every lookup missed on a
    blueprint paper, this returned its defensive 0 for everything, and the
    tie-break quietly degraded to "the later one loses" -- no error, no log, and
    the rebalancing half of this pass simply stopped happening there.
    """
    rows = (coverage or {}).get("per_topic") or {}
    row = rows.get(q.get("allocation_id")) or rows.get(q.get("topic"))
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
        "reviewed": 0,
        "llm_dropped": 0,
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


async def _llm_judge(items: list) -> list:
    """Which questions repeat each other? Returns a list of id groups.

    Never raises and never partially applies: a failure returns [], which
    leaves the paper exactly as the lexical tiers left it. Same contract as
    latex_repair._llm_fix and question_repair._llm_complete, and for the same
    reason -- this pass must not be able to fail a generation.

    No inner retry loop. One reading is worth one attempt; a paper is not worth
    holding open to re-ask a question whose answer defaults to "keep".
    """
    chain = [LLM_MODEL] if LLM_MODEL else None
    try:
        raw = await asyncio.wait_for(
            chat_complete(
                system_message=QUESTION_DEDUPE_SYSTEM,
                user_text=question_dedupe_prompt(items),
                provider_chain=chain,
            ),
            timeout=LLM_TIMEOUT,
        )
        data = parse_json_response(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dedupe] judge call failed, keeping lexical verdict: {e}")
        return []
    known = {it["id"] for it in items}
    groups = []
    for group in (data.get("duplicates") if isinstance(data, dict) else None) or []:
        if not isinstance(group, (list, tuple)):
            continue
        # Ids the model invented are dropped rather than guessed at, and a
        # "group" of one repeats nothing.
        ids = [str(g) for g in group if str(g) in known]
        if len(ids) >= 2:
            groups.append(ids)
    return groups


async def _review_duplicates(
    ordered: list,
    *,
    drop_ids: set,
    coverage: dict | None,
    protected: int,
    limit: int,
    ref: str = "",
) -> tuple:
    """Tier 3. Returns (extra_drop_ids, extra_pairs, reviewed_count).

    Reads every question the lexical tiers left standing, in one call. A pair
    of questions that share no vocabulary is invisible to a cosine score, so
    there is no cheap way to shortlist candidates first -- see the note above
    QUESTION_DEDUPE_SYSTEM for the measurements behind that.

    `limit` caps what this tier may drop, for the same reason the lexical cap
    exists: a model reading too eagerly can lose a paper exactly as fast as a
    mis-set threshold, and it costs more to find out.
    """
    survivors = [
        (idx, q) for idx, q in enumerate(ordered)
        if q.get("id") is not None and q.get("id") not in drop_ids
    ]
    if len(survivors) < MIN_CORPUS:
        return set(), [], 0

    index_of = {q["id"]: idx for idx, q in survivors}
    items = [
        {"id": str(q["id"]), "text": _normalise(q.get("question") or "")[:LLM_STEM_CHARS]}
        for _, q in survivors
    ]
    groups = await _llm_judge(items)

    extra_ids: set = set()
    extra_pairs: list = []
    for group in groups:
        # _llm_judge already drops ids that are not on the paper, so this is
        # belt and braces -- but a group naming something the lexical tiers
        # removed must narrow the group, never raise.
        group = [qid for qid in group if qid in index_of]
        if len(group) < 2:
            continue
        # Keep exactly one of each group. Protected questions (a top-up's
        # existing paper) can never be the one that goes, and past that the
        # keeper is whichever the topic quotas need most -- the same rule the
        # lexical tiers use, so the two cannot disagree about who wins.
        ranked = sorted(
            group,
            key=lambda qid: (
                index_of[qid] >= protected,
                _over_represented(ordered[index_of[qid]], coverage),
                index_of[qid],
            ),
        )
        keeper = ranked[0]
        for qid in ranked[1:]:
            if index_of[qid] < protected:
                continue  # never shrink the paper a top-up is extending
            if qid in extra_ids:
                continue
            if len(extra_ids) >= limit:
                logger.warning(
                    f"[dedupe] {ref or '-'} judge hit its {limit}-drop budget; "
                    "it is reading too eagerly, or the paper really is that "
                    "repetitive"
                )
                return extra_ids, extra_pairs, len(items)
            extra_ids.add(qid)
            extra_pairs.append({
                "kept": keeper,
                "dropped": qid,
                "score": None,
                "reason": "semantic_duplicate",
            })
    return extra_ids, extra_pairs, len(items)


def _apply(sections: list, drop_ids: set) -> None:
    for s in sections or []:
        s["questions"] = [
            q for q in (s.get("questions") or [])
            if not (isinstance(q, dict) and q.get("id") in drop_ids)
        ]


# ---------------------------------------------------------------------------
# One candidate against a whole paper
# ---------------------------------------------------------------------------
# dedupe_sections answers "which of these questions repeat each other" and
# removes the losers. Regenerating a single question asks a narrower question --
# "does THIS one repeat anything already here?" -- and must answer it without
# removing anything: a rejected candidate is simply never put on the paper.
#
# It reuses _normalise, _similarity and _classify unchanged, so a regenerated
# question is judged by exactly the same bar, with exactly the same constants,
# as a generated one.
SINGLE_LLM_TOP_K = int(os.environ.get("QUESTION_DEDUPE_SINGLE_TOP_K", "12"))
# Tighter than LLM_TIMEOUT. This runs inside a retry loop -- three attempts each
# waiting a full minute on the judge is a regeneration nobody waits for -- and a
# slow verdict here defaults to "keep", which is the safe direction.
SINGLE_LLM_TIMEOUT = float(os.environ.get("QUESTION_DEDUPE_SINGLE_TIMEOUT", "25"))
# The candidate has no id on the first attempt, and reusing the replaced
# question's id would let a model echoing that id read as a hit. A reserved
# string cannot collide with a uuid4.
CANDIDATE_ID = "__candidate__"


async def is_duplicate(
    candidate: dict | str,
    existing: list,
    *,
    topic: str = "",
    ref: str = "",
    use_llm: bool | None = None,
) -> tuple[bool, str | None]:
    """Does `candidate` repeat anything in `existing`?

    Returns (verdict, reason), where reason is a _classify reason,
    "semantic_duplicate", or None.

    Never raises, and NEVER returns True on a failure -- a timeout, a missing
    sklearn, an unvectorisable corpus all mean "keep". Same contract as
    _llm_judge, and the safe direction: the cost of wrongly keeping is one
    similar question, the cost of wrongly rejecting is a regeneration that can
    never succeed.

    Why this shortlists for the model when _review_duplicates deliberately does
    not: that tier sweeps an unknown set pairwise, where ANY pair might be the
    semantic twin, so a shortlist would be a guess about which. Here there is
    one candidate and one question, and cosine RANKS a same-topic question above
    an unrelated one long before it can CLASSIFY it -- ranking is all a
    shortlist needs. The topic union below closes the rest of the gap with
    stored data rather than a score.
    """
    if not DEDUPE_ENABLED:
        return False, None

    text = candidate.get("question", "") if isinstance(candidate, dict) else candidate
    cand = _normalise(text or "")
    rows = [q for q in existing or [] if isinstance(q, dict)]
    ex_stems = _stems(rows)
    if not cand.strip() or not [s for s in ex_stems if s.strip()]:
        return False, None

    # Before vectorising, because an exact repeat needs no score and the batch
    # path can only reach _classify's equality check once _similarity has
    # succeeded -- so an unvectorisable corpus lets a verbatim repeat through
    # there. Here it cannot.
    if cand in ex_stems:
        return True, "exact_duplicate"

    # Row 0 is the candidate against every existing question; the rest of the
    # matrix is the paper against itself and is not this call's business.
    sims = None
    if len(ex_stems) + 1 >= MIN_CORPUS:
        sims = _similarity([cand] + ex_stems)
    if sims is not None:
        for j, stem in enumerate(ex_stems):
            reason = _classify(float(sims[0][j + 1]), cand, stem)
            if reason:
                logger.info(
                    f"[dedupe] {ref or '-'} single verdict=duplicate "
                    f"reason={reason} score={float(sims[0][j + 1]):.3f}"
                )
                return True, reason

    # Read at call time, never as a default argument -- the test suite disables
    # the tier by monkeypatching the module global.
    if not (LLM_ENABLED if use_llm is None else use_llm):
        return False, None

    shortlist = _shortlist(rows, ex_stems, sims, topic)
    if not shortlist:
        return False, None
    items = [{"id": CANDIDATE_ID, "text": cand[:LLM_STEM_CHARS]}] + [
        {"id": str(q.get("id")), "text": _normalise(q.get("question") or "")[:LLM_STEM_CHARS]}
        for q in shortlist
        if q.get("id")
    ]
    if len(items) < MIN_CORPUS:
        return False, None
    try:
        groups = await asyncio.wait_for(_llm_judge(items), timeout=SINGLE_LLM_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dedupe] {ref or '-'} single judge unavailable, keeping: {e}")
        return False, None

    # Groups that do not name the candidate describe duplicates the paper
    # already had. Real, but not this call's job and not something a
    # regeneration should act on.
    if any(CANDIDATE_ID in g and len(g) >= 2 for g in groups):
        logger.info(
            f"[dedupe] {ref or '-'} single verdict=duplicate "
            f"reason=semantic_duplicate shortlist={len(items) - 1}"
        )
        return True, "semantic_duplicate"
    return False, None


def _shortlist(rows: list, ex_stems: list, sims, topic: str) -> list:
    """The questions worth showing the judge: nearest by score, plus same-topic.

    Same-topic questions are unioned in rather than trusted to the score,
    because a well-paraphrased duplicate can share almost no vocabulary with its
    twin -- which is the whole reason this tier exists. The topic is exact
    stored data, so it finds those where cosine cannot.
    """
    order: list = []
    if sims is not None:
        ranked = sorted(
            (j for j in range(len(rows)) if float(sims[0][j + 1]) > 0.0),
            key=lambda j: -float(sims[0][j + 1]),
        )
        order = ranked[:SINGLE_LLM_TOP_K]
    seen = set(order)
    if topic:
        for j, q in enumerate(rows):
            if j not in seen and str(q.get("topic") or "") == topic:
                order.append(j)
                seen.add(j)
    if not order:  # no scores and no topic match -- fall back to the paper's own order
        order = list(range(len(rows)))
    return [rows[j] for j in order[:SINGLE_LLM_TOP_K]]


async def dedupe_sections(
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
    # What the valve is actually protecting against is a mis-set THRESHOLD --
    # a score low enough to call unrelated questions duplicates would otherwise
    # gut a paper. An exact duplicate involves no threshold and no judgement:
    # the two stems are the same string after normalisation. Rationing those
    # meant a large paper shipped verbatim repeats, which is the one outcome
    # nobody would choose, so they are now dropped unconditionally and only the
    # scored pairs are rationed.
    #
    # This is what stopped the cap from binding on large papers. At 25 questions
    # the 25% allowance was 6 and was rarely reached; at 80 it is 20, and a
    # narrow topic list crosses that easily -- everything past it stayed on the
    # paper with nothing but a `capped` flag to say so.
    limit = max(1, int(len(fresh) * MAX_DROP_RATIO))
    ranked = sorted(pairs, key=lambda p: -p["score"])
    exact = [p for p in ranked if p["reason"] == "exact_duplicate"]
    scored = [p for p in ranked if p["reason"] != "exact_duplicate"]
    if len(scored) > limit:
        stats["capped"] = True
        logger.warning(
            f"[dedupe] {ref or '-'} {len(scored)} scored duplicates of "
            f"{len(fresh)} fresh, capping at {limit} -- threshold is "
            f"probably wrong ({len(exact)} exact duplicates dropped regardless)"
        )
        scored = scored[:limit]
    ranked = exact + scored

    drop_ids = {p["dropped"] for p in ranked if p["dropped"]}

    # Tier 3 reads what the free tiers left standing. Runs before _apply so the
    # whole pass is still one edit to the paper, and gets its own drop budget
    # rather than sharing the lexical one -- the two fail in different ways, and
    # a shared allowance would let either mask the other.
    if LLM_ENABLED:
        extra_ids, extra_pairs, reviewed = await _review_duplicates(
            ordered,
            drop_ids=drop_ids,
            coverage=coverage,
            protected=len(existing),
            limit=limit,
            ref=ref,
        )
        stats["reviewed"] = reviewed
        stats["llm_dropped"] = len(extra_ids)
        drop_ids |= extra_ids
        ranked = ranked + extra_pairs

    _apply(sections, drop_ids)

    stats["dropped"] = len(drop_ids)
    stats["pairs"] = ranked[:20]
    reasons: dict = {}
    for p in ranked:
        if p["dropped"] in drop_ids:
            reasons[p["reason"]] = reasons.get(p["reason"], 0) + 1
    stats["reasons"] = reasons
    if drop_ids or stats["reviewed"]:
        logger.info(
            f"[dedupe] {ref or '-'} checked={stats['checked']} "
            f"dropped={stats['dropped']} "
            f"reviewed={stats['reviewed']} llm={stats['llm_dropped']} "
            f"reasons={','.join(f'{k}={v}' for k, v in sorted(reasons.items()))}"
        )
    return stats
