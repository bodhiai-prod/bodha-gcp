"""Per-question regeneration: the slot stays, the question changes.

The properties worth holding onto:

* the replacement's slot is copied from the question it replaces, never taken
  from the model, so a blueprint paper's allocation checks keep passing;
* a candidate that repeats the paper is rejected and re-asked for, and every
  version already shown goes into the next prompt;
* nothing is written unless a candidate is accepted.

`complete_sections` and `repair_sections` are stubbed: both make their own LLM
calls, both have their own suites, and neither decides anything this file is
about.
"""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import workers
from prompts import STEM_CHARS
from workers import (
    REGEN_HISTORY_MAX,
    REGEN_MAX_ATTEMPTS,
    RegenerationFailed,
    reconcile_solution_shell,
    regenerate_question,
    regenerate_question_background,
)


def run(coro):
    return asyncio.run(coro)


def reply(question: str, **extra) -> str:
    return json.dumps({"question": question, "needs_diagram": False, **extra})


def make_paper(**overrides) -> dict:
    p = {
        "id": "paper-1",
        "owner_id": "teacher",
        "subject": "Physics",
        "class_name": "10",
        "difficulty": "medium",
        "custom_instructions": "",
        "generation_status": "ready",
        "sections": [
            {
                "section_id": "A",
                "title": "Section A",
                "questions": [
                    {"id": "q1", "question": "State Ohm's law and give its expression.",
                     "topic": "Electricity", "type": "information", "format": "short_answer",
                     "difficulty": "easy", "marks": 2, "important": False,
                     "needs_diagram": False},
                    {"id": "q2", "question": "Explain how a convex lens forms a real image.",
                     "topic": "Light", "type": "concept", "format": "short_answer",
                     "difficulty": "medium", "marks": 3, "important": True,
                     "needs_diagram": False},
                ],
            }
        ],
    }
    p.update(overrides)
    return p


class FakeCollection:
    def __init__(self, doc=None):
        self.doc = doc
        self.updates = []
        self.inserts = []

    async def find_one(self, flt=None, *args, **kwargs):
        if self.doc is None:
            return None
        # Honour the id filter -- a "paper not found" test is meaningless
        # against a collection that hands back the same document whatever it
        # is asked for.
        wanted = (flt or {}).get("id")
        if wanted is not None and self.doc.get("id") != wanted:
            return None
        return copy.deepcopy(self.doc)

    async def update_one(self, flt, update):
        self.updates.append(update)
        self.doc.update(update.get("$set", {}))

    async def insert_one(self, doc):
        self.inserts.append(doc)


class FakeDB:
    def __init__(self, paper):
        self.papers = FakeCollection(paper)
        self.paper_edits = FakeCollection({})


class RegenBase(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patches = [
            patch.object(workers, "complete_sections", AsyncMock(return_value={})),
            patch.object(workers, "repair_sections", AsyncMock(return_value={})),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def scripted(self, *replies):
        """A chat_complete that hands back `replies` in order, recording prompts."""
        async def _chat(system_message=None, user_text=None, **kwargs):
            self.calls.append(user_text)
            return replies[min(len(self.calls) - 1, len(replies) - 1)]
        return _chat


class SlotTests(RegenBase):
    def test_the_model_never_gets_to_change_the_slot(self):
        p = make_paper()
        rogue = json.dumps({
            "question": "A lens of focal length 20 cm forms an image of an object 30 cm away.",
            "topic": "Thermodynamics", "type": "application", "format": "mcq",
            "marks": 99, "difficulty": "hard", "id": "invented",
            "allocation_id": "nonsense", "needs_diagram": False,
        })
        with patch.object(workers, "chat_complete", self.scripted(rogue)):
            out = run(regenerate_question(p, "q2"))
        q = out["question"]
        old = p["sections"][0]["questions"][1]
        for field in ("id", "topic", "type", "format", "difficulty", "marks"):
            self.assertEqual(q[field], old[field], field)
        self.assertNotIn("allocation_id", q)
        self.assertNotEqual(q["question"], old["question"])

    def test_a_blueprint_paper_still_satisfies_its_allocation_contract(self):
        from section_blueprints import allocation_misses
        from test_section_blueprints import fixture

        _, _, _, spec, sections = fixture()
        p = make_paper(sections=copy.deepcopy(sections), subject="Physics", class_name="12")
        target = sections[0]["questions"][0]["id"]
        rogue = json.dumps({
            "question": "Describe how the turns ratio of a transformer sets its output voltage.",
            "marks": 7, "format": "mcq", "topic": "Something Else", "needs_diagram": False,
        })
        with patch.object(workers, "chat_complete", self.scripted(rogue)):
            out = run(regenerate_question(p, target))
        self.assertEqual(allocation_misses(out["sections"], spec), [])

    def test_the_star_and_the_position_survive(self):
        p = make_paper()
        with patch.object(workers, "chat_complete", self.scripted(reply("A brand new question about lenses."))):
            out = run(regenerate_question(p, "q2"))
        questions = out["sections"][0]["questions"]
        self.assertEqual([q["id"] for q in questions], ["q1", "q2"])
        self.assertTrue(questions[1]["important"])

    def test_a_wrapped_reply_is_still_usable(self):
        # The prompt asks for a bare object; models sometimes send the paper
        # shape they have seen a thousand times. Losing a good question to a
        # wrapper would just spend another attempt.
        wrapped = json.dumps({"sections": [{"questions": [
            {"question": "A perfectly good question arriving inside a wrapper."}
        ]}]})
        with patch.object(workers, "chat_complete", self.scripted(wrapped)):
            out = run(regenerate_question(make_paper(), "q1"))
        self.assertEqual(
            out["question"]["question"],
            "A perfectly good question arriving inside a wrapper.",
        )


class DuplicateTests(RegenBase):
    def test_a_repeat_is_rejected_and_re_asked_for(self):
        p = make_paper()
        repeat = reply(p["sections"][0]["questions"][0]["question"])
        fresh = reply("A circuit draws 2 A through a 10 ohm resistor for five minutes.")
        with patch.object(workers, "chat_complete", self.scripted(repeat, fresh)):
            out = run(regenerate_question(p, "q2"))
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("A circuit draws 2 A", out["question"]["question"])

    def test_the_second_prompt_carries_what_the_first_got_wrong(self):
        p = make_paper()
        rejected_text = p["sections"][0]["questions"][0]["question"]
        with patch.object(
            workers, "chat_complete",
            self.scripted(reply(rejected_text), reply("Something genuinely different about lenses.")),
        ):
            run(regenerate_question(p, "q2"))
        second = self.calls[1]
        self.assertIn("ALREADY REJECTED FOR THIS SLOT", second)
        self.assertIn(rejected_text[:60], second)
        self.assertIn("PREVIOUS ATTEMPT WAS REJECTED", second)

    def test_every_other_question_is_in_the_avoid_list(self):
        p = make_paper()
        with patch.object(workers, "chat_complete", self.scripted(reply("A new question."))):
            run(regenerate_question(p, "q2"))
        prompt = self.calls[0]
        self.assertIn("EVERY OTHER QUESTION ALREADY ON THIS PAPER", prompt)
        self.assertIn("State Ohm's law", prompt)
        # The question being replaced belongs in the rejected block, not among
        # the neighbours it is leaving.
        self.assertIn("ALREADY REJECTED FOR THIS SLOT", prompt)
        self.assertIn("Explain how a convex lens", prompt)

    def test_giving_up_changes_nothing(self):
        p = make_paper()
        before = copy.deepcopy(p)
        repeat = reply(p["sections"][0]["questions"][0]["question"])
        with patch.object(workers, "chat_complete", self.scripted(repeat)):
            with self.assertRaises(RegenerationFailed):
                run(regenerate_question(p, "q2"))
        self.assertEqual(len(self.calls), REGEN_MAX_ATTEMPTS)
        self.assertEqual(p, before)


class HistoryTests(RegenBase):
    def test_history_accumulates_newest_last_and_is_capped(self):
        p = make_paper()
        p["sections"][0]["questions"][1]["regen_history"] = [f"Old version {i}" for i in range(7)]
        with patch.object(workers, "chat_complete", self.scripted(reply("A brand new question."))):
            out = run(regenerate_question(p, "q2"))
        history = out["question"]["regen_history"]
        self.assertEqual(len(history), REGEN_HISTORY_MAX)
        self.assertNotIn("Old version 0", history)
        self.assertEqual(history[-1], "Explain how a convex lens forms a real image.")
        self.assertTrue(all(len(h) <= STEM_CHARS for h in history))

    def test_a_candidate_rejected_this_run_is_remembered_for_the_next_click(self):
        # Otherwise click two happily returns the question click one already
        # threw away, which is the same duplicate seen from a different angle.
        p = make_paper()
        dud = "State Ohm's law and give its expression."
        with patch.object(workers, "chat_complete", self.scripted(
            reply(dud), reply("A circuit draws 2 A through a 10 ohm resistor.")
        )):
            out = run(regenerate_question(p, "q2"))
        history = out["question"]["regen_history"]
        self.assertIn(dud, history)
        self.assertIn("Explain how a convex lens forms a real image.", history)

    def test_the_whole_history_is_fed_back_to_the_model(self):
        p = make_paper()
        p["sections"][0]["questions"][1]["regen_history"] = ["A version shown two clicks ago."]
        with patch.object(workers, "chat_complete", self.scripted(reply("A brand new question."))):
            run(regenerate_question(p, "q2"))
        self.assertIn("A version shown two clicks ago.", self.calls[0])


class DiagramTests(RegenBase):
    def test_a_slot_with_no_figure_cannot_grow_one(self):
        seen = {}
        real = workers.apply_diagram_policy

        def spy(sections, *, ref="", max_diagrams=0):
            seen["max"] = max_diagrams
            return real(sections, ref=ref, max_diagrams=max_diagrams)

        with patch.object(workers, "apply_diagram_policy", spy), \
                patch.object(workers, "chat_complete", self.scripted(
                    reply("A new question.", needs_diagram=True,
                          diagram_spec={"figure": "a circuit", "why_required": "x"}))):
            out = run(regenerate_question(make_paper(), "q1"))
        self.assertEqual(seen["max"], 0)
        self.assertFalse(out["question"]["needs_diagram"])
        self.assertEqual(out["question"]["diagram_status"], "none")
        self.assertFalse(out["diagram_pending"])

    def test_a_stale_image_is_dropped_so_a_new_one_can_render(self):
        # generate_diagrams_for_paper skips any question that already has a
        # path, so leaving it would pin the old picture to the new question.
        p = make_paper()
        p["sections"][0]["questions"][0].update({
            "needs_diagram": True,
            "diagram_path": "app/diagrams/teacher/paper-1/q1.png",
            "diagram_status": "ready",
        })
        fresh = reply(
            "In the given figure, find the current through the 5 ohm branch.",
            needs_diagram=True,
            diagram_spec={"figure": "a two-branch circuit", "labels": ["5 ohm", "10 ohm"],
                          "given_values": ["12 V"], "unknowns": ["I"],
                          "why_required": "the topology cannot be written out"},
        )
        with patch.object(workers, "chat_complete", self.scripted(fresh)):
            out = run(regenerate_question(p, "q1"))
        self.assertNotIn("diagram_path", out["question"])
        self.assertEqual(out["question"]["diagram_status"], "pending")
        self.assertTrue(out["diagram_pending"])


class SolutionTests(unittest.TestCase):
    def test_only_the_rewritten_answer_is_blanked_and_it_is_refillable(self):
        p = make_paper()
        p["solution"] = {
            "status": "ready", "completed": 2, "total": 2, "is_stale": False,
            "sections": [{"title": "Section A", "answers": [
                {"question_id": "q1", "answer": "V = IR."},
                {"question_id": "q2", "answer": "Rays converge beyond the focus."},
            ]}],
        }
        solution = workers._invalidate_solution(p, "q2", targeted=True)
        answers = {a["question_id"]: a["answer"] for a in solution["sections"][0]["answers"]}
        self.assertEqual(answers["q1"], "V = IR.")
        self.assertEqual(answers["q2"], "")
        self.assertTrue(solution["is_stale"])
        self.assertEqual(solution["status"], "partial")
        self.assertEqual(solution["completed"], 1)
        # Which is exactly what /solution/generate-missing looks for.
        _, missing = reconcile_solution_shell(p, solution)
        self.assertEqual(missing, ["q2"])

    def test_a_live_solution_job_is_only_told_the_key_is_stale(self):
        # That job rewrites the whole document and would put the old answer
        # straight back over a blank.
        p = make_paper()
        p["solution"] = {"status": "generating", "completed": 1, "total": 2, "sections": [
            {"title": "Section A", "answers": [{"question_id": "q2", "answer": "Old answer."}]}
        ]}
        solution = workers._invalidate_solution(p, "q2", targeted=False)
        self.assertTrue(solution["is_stale"])
        self.assertEqual(solution["sections"][0]["answers"][0]["answer"], "Old answer.")

    def test_a_paper_with_no_answer_key_is_left_alone(self):
        self.assertIsNone(workers._invalidate_solution(make_paper(), "q1", targeted=True))


class BackgroundTests(RegenBase):
    def setUp(self):
        super().setUp()
        self.db = FakeDB(make_paper())
        for p in (
            patch.object(workers, "db", self.db),
            patch.object(workers, "generate_diagrams_background", AsyncMock()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_success_writes_once_and_records_the_rejection(self):
        with patch.object(workers, "chat_complete", self.scripted(reply("A brand new question about lenses."))):
            run(regenerate_question_background("paper-1", "teacher", "q2"))
        writes = self.db.papers.updates
        self.assertEqual(len(writes), 1)
        sets = writes[0]["$set"]
        self.assertEqual(sets["regen"]["status"], "ready")
        self.assertEqual(sets["regen"]["question_id"], "q2")
        self.assertIn("A brand new question", sets["sections"][0]["questions"][1]["question"])

        edit = self.db.paper_edits.inserts[0]
        self.assertEqual(edit["edit_type"], "regenerate_question")
        self.assertEqual(edit["original"], "Explain how a convex lens forms a real image.")
        self.assertIn("A brand new question", edit["revised"])

    def test_failure_touches_nothing_but_the_status(self):
        before = copy.deepcopy(self.db.papers.doc)
        repeat = reply(before["sections"][0]["questions"][0]["question"])
        with patch.object(workers, "chat_complete", self.scripted(repeat)):
            run(regenerate_question_background("paper-1", "teacher", "q2"))
        self.assertEqual(len(self.db.papers.updates), 1)
        sets = self.db.papers.updates[0]["$set"]
        self.assertEqual(list(sets), ["regen"])
        self.assertEqual(sets["regen"]["status"], "failed")
        self.assertEqual(self.db.papers.doc["sections"], before["sections"])
        self.assertEqual(self.db.paper_edits.inserts, [])

    def test_a_provider_outage_is_reported_not_raised(self):
        async def boom(**kwargs):
            raise RuntimeError("provider down")

        with patch.object(workers, "chat_complete", boom):
            run(regenerate_question_background("paper-1", "teacher", "q2"))
        self.assertEqual(self.db.papers.updates[0]["$set"]["regen"]["status"], "failed")


class FeedbackHintTests(unittest.TestCase):
    def test_a_regeneration_teaches_the_next_paper(self):
        rows = [{
            "edit_type": "regenerate_question",
            "original": "The question the teacher rejected.",
            "revised": "The one they kept.",
        }]

        class Cursor:
            def sort(self, *a):
                return self

            async def to_list(self, n):
                return rows

        class Edits:
            def find(self, *a, **k):
                return Cursor()

        with patch.object(workers, "db", type("D", (), {"paper_edits": Edits()})()):
            hints = run(workers.load_paper_feedback_hints("teacher", "Physics", "10"))
        self.assertIn("REGENERATED away from", hints)
        self.assertIn("The question the teacher rejected.", hints)
        self.assertIn("The one they kept.", hints)


class RouteGuardTests(unittest.TestCase):
    """Every refusal the endpoint can hand back, and the one acceptance.

    These mirror lib/regenerate.canRegenerate on the front end; a disabled
    button and a rejected request must never disagree about why.
    """

    def setUp(self):
        import server
        self.server = server
        self.db = FakeDB(make_paper())
        self.spawned = []

        def record(coro):
            self.spawned.append(coro)
            coro.close()  # nothing here runs the job; just don't leak the task

        for p in (
            patch.object(server, "db", self.db),
            patch.object(server, "_spawn", record),
            patch.object(server, "_paper_generation_inputs", AsyncMock(return_value=("", ""))),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.user = {"id": "teacher", "role": "teacher"}

    def call(self, paper_id="paper-1", question_id="q2"):
        from fastapi import HTTPException
        try:
            return None, run(self.server.regenerate_question_endpoint(
                paper_id, question_id, user=self.user
            ))
        except HTTPException as e:
            return e, None

    def test_a_ready_paper_queues_the_job_and_claims_the_lock(self):
        err, out = self.call()
        self.assertIsNone(err)
        self.assertEqual(out["status"], "generating")
        self.assertEqual(out["question_id"], "q2")
        self.assertEqual(self.db.papers.updates[0]["$set"]["regen"]["question_id"], "q2")
        self.assertEqual(len(self.spawned), 1)

    def test_refusals(self):
        cases = [
            ({}, 404, "paper-missing", "q2"),
            ({"owner_id": "someone-else"}, 403, "paper-1", "q2"),
            ({"is_competitive": True}, 400, "paper-1", "q2"),
            ({}, 404, "paper-1", "no-such-question"),
            ({"generation_status": "pending"}, 400, "paper-1", "q2"),
            ({"topup": {"status": "generating", "started_at": self.server.utcnow_iso()}}, 409, "paper-1", "q2"),
            ({"regen": {"status": "generating", "question_id": "q1",
                        "started_at": self.server.utcnow_iso()}}, 409, "paper-1", "q2"),
        ]
        for overrides, code, paper_id, qid in cases:
            with self.subTest(code=code, overrides=sorted(overrides)):
                self.db.papers.doc = make_paper(**overrides)
                self.spawned.clear()
                err, _ = self.call(paper_id, qid)
                self.assertIsNotNone(err, f"expected {code}")
                self.assertEqual(err.status_code, code)
                self.assertEqual(self.spawned, [])

    def test_a_stale_lock_does_not_wedge_the_question(self):
        # In-process tasks do not survive a restart, so a `generating` stamp
        # older than REGEN_LOCK_SECONDS is a dead job, not a live one.
        from datetime import datetime, timedelta, timezone
        stale = (datetime.now(timezone.utc) - timedelta(seconds=self.server.REGEN_LOCK_SECONDS + 60)).isoformat()
        self.db.papers.doc = make_paper(
            regen={"status": "generating", "question_id": "q1", "started_at": stale}
        )
        err, out = self.call()
        self.assertIsNone(err)
        self.assertEqual(out["status"], "generating")

    def test_a_live_answer_key_job_is_passed_through_not_blocked(self):
        self.db.papers.doc = make_paper(
            solution={"status": "generating", "started_at": self.server.utcnow_iso(), "sections": []}
        )
        err, _ = self.call()
        self.assertIsNone(err)
        self.assertEqual(len(self.spawned), 1)


if __name__ == "__main__":
    unittest.main()
