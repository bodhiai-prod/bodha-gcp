"""The single-candidate duplicate check used by question regeneration.

`dedupe_sections` asks "which of these repeat each other" and removes losers;
`is_duplicate` asks "does THIS one repeat anything" and removes nothing. These
cover the second, including the two properties the regeneration path depends on:
it never rejects on a failure, and it never sends the whole paper to the judge.
"""
import asyncio
import unittest
from unittest.mock import patch

import question_dedupe
from question_dedupe import CANDIDATE_ID, SINGLE_LLM_TOP_K, is_duplicate


def run(coro):
    return asyncio.run(coro)


def paper(n: int = 3) -> list:
    stems = [
        "A body of mass 2 kg moves with a velocity of 3 m/s. Calculate its kinetic energy.",
        "State Ohm's law and write down its mathematical expression clearly.",
        "Explain how a convex lens forms a real image of a distant object.",
    ]
    return [
        {"id": f"q{i}", "question": stems[i % len(stems)], "topic": "Physics"}
        for i in range(n)
    ]


class LexicalTests(unittest.TestCase):
    def test_verbatim_repeat_is_an_exact_duplicate(self):
        existing = paper()
        verdict, reason = run(
            is_duplicate({"question": existing[1]["question"]}, existing)
        )
        self.assertTrue(verdict)
        self.assertEqual(reason, "exact_duplicate")

    def test_one_changed_quantity_is_still_the_same_question(self):
        existing = paper()
        swapped = existing[0]["question"].replace("2 kg", "4 kg")
        verdict, reason = run(is_duplicate({"question": swapped}, existing))
        self.assertTrue(verdict)
        self.assertIn(reason, ("near_duplicate", "numeral_variant"))

    def test_a_genuinely_new_question_on_a_covered_topic_is_kept(self):
        verdict, reason = run(is_duplicate(
            {"question": "A resistor of 10 ohm carries 2 A for 5 minutes. Find the heat produced."},
            paper(),
        ))
        self.assertFalse(verdict)
        self.assertIsNone(reason)

    def test_short_stems_are_judged_on_equality_alone(self):
        # Two content words vectorise as near-total overlap, which says something
        # about the vocabulary rather than about the questions. MIN_TOKENS.
        verdict, _ = run(is_duplicate(
            {"question": "Define inertia."},
            [{"id": "a", "question": "Define momentum."}],
        ))
        self.assertFalse(verdict)

    def test_empty_corpus_and_empty_candidate_are_not_duplicates(self):
        self.assertEqual(run(is_duplicate({"question": "Anything at all here."}, [])), (False, None))
        self.assertEqual(run(is_duplicate({"question": ""}, paper())), (False, None))

    def test_exact_match_survives_an_unvectorisable_corpus(self):
        # The batch path can only reach the equality check once _similarity has
        # succeeded. This one checks first, so a verbatim repeat is caught even
        # when sklearn cannot score the corpus at all.
        existing = paper()
        with patch.object(question_dedupe, "_similarity", return_value=None):
            verdict, reason = run(
                is_duplicate({"question": existing[0]["question"]}, existing)
            )
        self.assertEqual((verdict, reason), (True, "exact_duplicate"))

    def test_the_kill_switch_stops_everything(self):
        existing = paper()
        with patch.object(question_dedupe, "DEDUPE_ENABLED", False):
            self.assertEqual(
                run(is_duplicate({"question": existing[0]["question"]}, existing)),
                (False, None),
            )


class SemanticTierTests(unittest.TestCase):
    """The judge tier, which conftest disables by default."""

    def setUp(self):
        self.seen = []

    def judge(self, groups):
        async def _judge(items):
            self.seen.append(items)
            return groups(items) if callable(groups) else groups
        return _judge

    def test_the_judge_reads_a_shortlist_not_the_whole_paper(self):
        # 80 questions in, at most SINGLE_LLM_TOP_K + the candidate out. Sending
        # all 80 per click (and per retry) is what this tier exists to avoid.
        existing = [
            {"id": f"q{i}", "question": f"Question number {i} about topic {i} in some detail.",
             "topic": f"Topic {i}"}
            for i in range(80)
        ]
        with patch.object(question_dedupe, "_llm_judge", self.judge([])):
            verdict, _ = run(is_duplicate(
                {"question": "Something entirely unrelated to every other item."},
                existing, topic="Topic 3", use_llm=True,
            ))
        self.assertFalse(verdict)
        self.assertEqual(len(self.seen), 1)
        self.assertLessEqual(len(self.seen[0]), SINGLE_LLM_TOP_K + 1)
        self.assertEqual(self.seen[0][0]["id"], CANDIDATE_ID)

    def test_a_group_naming_the_candidate_rejects_it(self):
        existing = paper()
        with patch.object(
            question_dedupe, "_llm_judge",
            self.judge(lambda items: [[CANDIDATE_ID, items[1]["id"]]]),
        ):
            verdict, reason = run(is_duplicate(
                {"question": "An unrelated question about photosynthesis in green plants."},
                existing, use_llm=True,
            ))
        self.assertEqual((verdict, reason), (True, "semantic_duplicate"))

    def test_groups_without_the_candidate_are_not_its_problem(self):
        # Two EXISTING questions repeating each other is real, but removing a
        # fresh candidate over it would be punishing the wrong question.
        existing = paper()
        with patch.object(
            question_dedupe, "_llm_judge",
            self.judge(lambda items: [[items[1]["id"], items[2]["id"]]]),
        ):
            verdict, _ = run(is_duplicate(
                {"question": "An unrelated question about photosynthesis in green plants."},
                existing, use_llm=True,
            ))
        self.assertFalse(verdict)

    def test_a_failing_or_slow_judge_keeps_the_question(self):
        async def boom(items):
            raise RuntimeError("provider down")

        async def slow(items):
            await asyncio.sleep(5)
            return [[CANDIDATE_ID, "q0"]]

        cand = {"question": "An unrelated question about photosynthesis in green plants."}
        with patch.object(question_dedupe, "_llm_judge", boom):
            self.assertEqual(run(is_duplicate(cand, paper(), use_llm=True)), (False, None))
        with patch.object(question_dedupe, "_llm_judge", slow), \
                patch.object(question_dedupe, "SINGLE_LLM_TIMEOUT", 0.05):
            self.assertEqual(run(is_duplicate(cand, paper(), use_llm=True)), (False, None))

    def test_the_tier_is_off_unless_asked_for(self):
        # conftest sets LLM_ENABLED False; is_duplicate must read it at call time.
        with patch.object(question_dedupe, "_llm_judge", self.judge([[CANDIDATE_ID, "q0"]])):
            verdict, _ = run(is_duplicate(
                {"question": "An unrelated question about photosynthesis in green plants."},
                paper(),
            ))
        self.assertFalse(verdict)
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
