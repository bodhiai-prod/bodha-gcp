import unittest

from normal_paper_contract import (
    NormalContractError,
    compile_normal_plan,
    materialize_normal_sections,
    merge_normal_sections,
    normal_slot_batches,
    normalize_normal_sections,
    review_status_from_misses,
    validate_normal_plan,
)
from prompts import normal_slots_prompt


def fixture(total=6):
    spec = {
        "total_questions": total,
        "type_counts": {"information": 2, "concept": 2, "application": 2},
        "format_counts": {"mcq": 2, "short_answer": 2, "long_answer": 2},
        "topic_difficulty": {"Algebra": "easy", "Geometry": "hard"},
        "diagram_count": 1,
    }
    topics = [
        {"name": "Algebra", "weight": 5, "topic_id": "ta", "textbook_id": "book"},
        {"name": "Geometry", "weight": 5, "topic_id": "tg", "textbook_id": "book"},
    ]
    return spec, topics, compile_normal_plan(spec, topics, 18)


class NormalPaperContractTests(unittest.TestCase):
    def test_compiler_owns_exact_structure_and_marks(self):
        _spec, _topics, plan = fixture()
        self.assertEqual(plan["contract_version"], 1)
        self.assertEqual(len(plan["slots"]), 6)
        self.assertEqual(sum(slot["marks"] for slot in plan["slots"]), 18)
        self.assertEqual(sum(plan["topic_counts"].values()), 6)
        self.assertEqual(
            [row["title"] for row in plan["sections"]],
            ["Section A - Information Based", "Section B - Concept Based", "Section C - Application Based"],
        )

    def test_section_letters_are_contiguous_when_a_cognitive_type_is_absent(self):
        spec = {
            "total_questions": 4,
            "type_counts": {"information": 0, "concept": 2, "application": 2},
            "format_counts": {"short_answer": 4},
            "section_difficulty": {"Section A": "easy", "Section B": "hard"},
        }
        plan = compile_normal_plan(spec, [{"name": "Biology", "weight": 5}], 8)
        self.assertEqual([section["title"] for section in plan["sections"]], [
            "Section A - Concept Based", "Section B - Application Based",
        ])
        self.assertEqual([slot["difficulty"] for slot in plan["slots"]], ["easy", "easy", "hard", "hard"])

    def test_provider_metadata_and_sections_have_no_authority(self):
        _spec, _topics, plan = fixture()
        payload = {"sections": [{"title": "Section B - Invented", "questions": [
            {"slot_id": slot["slot_id"], "question": f"Question {slot['slot_id']}",
             "topic": "Wrong", "type": "application", "marks": 99}
            for slot in reversed(plan["slots"])
        ]}]}
        sections, missing, unknown = materialize_normal_sections(payload, plan)
        self.assertEqual(missing, [])
        self.assertEqual(unknown, [])
        self.assertEqual([section["title"] for section in sections], [row["title"] for row in plan["sections"]])
        questions = [question for section in sections for question in section["questions"]]
        self.assertEqual([question["slot_id"] for question in questions], [slot["slot_id"] for slot in plan["slots"]])
        self.assertEqual(validate_normal_plan(sections, plan), [])

    def test_reassembly_preserves_runtime_fields_and_restamps_structure(self):
        _spec, _topics, plan = fixture()
        slot = plan["slots"][0]
        sections = [{"title": "Provider title", "questions": [{
            "id": "stable-id",
            "slot_id": slot["slot_id"],
            "section_id": "wrong",
            "question": "Edited by teacher",
            "topic": "wrong",
            "marks": 999,
            "important": True,
            "diagram_path": "questions/stable-id.png",
        }]}]
        merged = merge_normal_sections(plan, sections)
        question = merged[0]["questions"][0]
        self.assertEqual(question["id"], "stable-id")
        self.assertEqual(question["question"], "Edited by teacher")
        self.assertTrue(question["important"])
        self.assertEqual(question["topic"], slot["topic"])
        self.assertEqual(question["marks"], slot["marks"])

    def test_manual_unallocated_question_is_never_dropped_on_read(self):
        _spec, _topics, plan = fixture()
        sections = [{"title": "Teacher section", "questions": [{
            "id": "manual", "question": "Manual question", "type": "concept",
        }]}]
        normalized, changed = normalize_normal_sections(sections, plan)
        self.assertFalse(changed)
        self.assertEqual(normalized, sections)

    def test_provider_call_ceilings(self):
        for count, expected_calls in ((16, 1), (25, 1), (50, 3), (100, 5)):
            slots = [{"slot_id": f"s-{index}"} for index in range(count)]
            batches = normal_slot_batches(slots)
            self.assertEqual(len(batches), expected_calls)
            self.assertEqual(sum(map(len, batches)), count)
            self.assertTrue(all(len(batch) <= (25 if count <= 25 else 20) for batch in batches))

    def test_impossible_topic_coverage_fails_before_generation(self):
        spec = {"total_questions": 1, "type_counts": {"concept": 1}, "format_counts": {"mcq": 1}}
        with self.assertRaisesRegex(NormalContractError, "2 topics are selected for 1 questions"):
            compile_normal_plan(spec, [{"name": "A"}, {"name": "B"}], 1)

    def test_format_topic_allocations_own_the_exact_slot_pairing_and_difficulty(self):
        spec = {
            "total_questions": 4,
            "type_counts": {"information": 2, "concept": 2},
            "format_counts": {"mcq": 2, "long_answer": 2},
            "format_topic_allocations": [
                {"format": "mcq", "name": "Algebra", "textbook_id": "book", "topic_id": "a", "count": 2, "difficulty": "easy"},
                {"format": "long_answer", "name": "Geometry", "textbook_id": "book", "topic_id": "g", "count": 2, "difficulty": "hard"},
            ],
        }
        plan = compile_normal_plan(spec, [], 10)
        self.assertEqual([(slot["format"], slot["topic"]) for slot in plan["slots"]], [
            ("long_answer", "Geometry"), ("long_answer", "Geometry"),
            ("mcq", "Algebra"), ("mcq", "Algebra"),
        ])
        self.assertEqual([slot["difficulty"] for slot in plan["slots"]], ["hard", "hard", "easy", "easy"])

    def test_incomplete_format_topic_allocations_fail_before_generation(self):
        spec = {
            "total_questions": 2, "type_counts": {"concept": 2},
            "format_counts": {"mcq": 2},
            "format_topic_allocations": [{"format": "mcq", "name": "Algebra", "count": 1}],
        }
        with self.assertRaisesRegex(NormalContractError, "do not match"):
            compile_normal_plan(spec, [], 2)

    def test_format_topic_diagram_minimum_is_required_and_respects_exact_total(self):
        spec = {
            "total_questions": 3, "type_counts": {"concept": 3},
            "format_counts": {"mcq": 3}, "diagram_count": 2,
            "format_topic_allocations": [
                {"format": "mcq", "name": "Algebra", "textbook_id": "book", "topic_id": "a", "count": 2, "diagrams": True},
                {"format": "mcq", "name": "Geometry", "textbook_id": "book", "topic_id": "g", "count": 1, "diagrams": False},
            ],
        }
        plan = compile_normal_plan(spec, [], 6)
        self.assertEqual(sum(slot["diagram_mode"] == "required" for slot in plan["slots"]), 2)
        self.assertEqual(plan["slots"][0]["diagram_mode"], "required")
        with self.assertRaisesRegex(NormalContractError, "at least 1 diagrams"):
            compile_normal_plan({**spec, "diagram_count": 0}, [], 6)

    def test_legacy_duplicate_sections_regroup_by_cognitive_type(self):
        legacy = [
            {"title": "Section B - Concept Based", "questions": [{"id": "one", "type": "concept"}]},
            {"title": "Section B - Conceptual Understanding", "questions": [{"id": "two", "type": "conceptual"}]},
            {"title": "Section C - Application", "questions": [{"id": "three", "type": "application"}]},
        ]
        sections, changed = normalize_normal_sections(legacy)
        self.assertTrue(changed)
        self.assertEqual([section["title"] for section in sections], ["Section A - Concept Based", "Section B - Application Based"])
        self.assertEqual([question["id"] for question in sections[0]["questions"]], ["one", "two"])

    def test_prompt_is_flat_and_slot_keyed(self):
        _spec, _topics, plan = fixture()
        prompt = normal_slots_prompt(
            subject="Maths", klass="10", slots=plan["slots"], context_excerpt="source"
        )
        self.assertIn('"slot_id":"normal-001"', prompt)
        self.assertIn('{"questions":[', prompt)
        self.assertNotIn('"sections":[', prompt)

    def test_legacy_warnings_are_grouped(self):
        status = review_status_from_misses([
            "asked for 1 questions on Algebra, got 0",
            "asked for 1 questions on Geometry, got 0",
            "asked for 50 total marks, got 49",
        ])
        self.assertEqual(status["status"], "needs_review")
        self.assertIn("2 topic issues", status["summary"])
        self.assertIn("1 marks issue", status["summary"])


if __name__ == "__main__":
    unittest.main()
