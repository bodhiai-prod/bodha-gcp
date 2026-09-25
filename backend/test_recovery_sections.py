import json
import unittest

from llm_adapter import parse_json_response
from workers import align_blueprint_sections, reconcile_section_plan, restore_recovered_sections


class RecoverySectionTests(unittest.TestCase):
    def test_truncated_response_retains_each_section_identity(self):
        response = '''{
          "instructions": "Answer all.",
          "sections": [
            {"section_id":"A", "title":"Section A", "questions": [
              {"question":"A1", "marks":1}
            ]},
            {"section_id":"B", "title":"Section B", "questions": [
              {"question":"B1", "marks":2},
              {"question":"B2"
        '''
        parsed = parse_json_response(response)

        self.assertTrue(parsed["_recovered_from_truncation"])
        self.assertEqual(
            [(s.get("section_id"), s["title"], [q["question"] for q in s["questions"]]) for s in parsed["sections"]],
            [("A", "Section A", ["A1"]), ("B", "Section B", ["B1"])],
        )

    def test_legacy_recovery_section_is_repaired_from_allocation_identity(self):
        spec = {
            "section_allocations": [
                {"allocation_id": "a", "section_id": "A"},
                {"allocation_id": "b", "section_id": "B"},
            ],
            "blueprint": {"sections": [
                {"id": "A", "title": "Part A"},
                {"id": "B", "title": "Part B"},
            ]},
        }
        sections, changed = restore_recovered_sections([
            {"title": "Recovered Questions", "questions": [
                {"id": "one", "allocation_id": "a"},
                {"id": "two", "allocation_id": "b"},
            ]}
        ], spec)

        self.assertTrue(changed)
        self.assertEqual(
            [(s["section_id"], s["title"], [q["id"] for q in s["questions"]]) for s in sections],
            [("A", "Part A", ["one"]), ("B", "Part B", ["two"])],
        )

    def test_topup_heading_is_merged_into_its_real_blueprint_section(self):
        spec = {
            "section_allocations": [{"allocation_id": "a", "section_id": "A"}],
            "blueprint": {"sections": [{"id": "A", "title": "Section A - Information"}]},
        }
        sections, changed = align_blueprint_sections([
            {"section_id": "A", "title": "Section A - Information", "questions": [{"id": "old"}]},
            {"title": "Section A - Top-Up Questions", "questions": [{"id": "new", "allocation_id": "a"}]},
        ], spec)

        self.assertTrue(changed)
        self.assertEqual(len(sections), 1)
        self.assertEqual([question["id"] for question in sections[0]["questions"]], ["old", "new"])

    def test_existing_non_blueprint_topup_uses_current_section_name_and_order(self):
        sections, plan, changed = reconcile_section_plan([
            {"title": "Section A - Information Based", "questions": [{"id": "one"}]},
            {"title": "Section B - Concept Based", "questions": [{"id": "two"}]},
            {"title": "Section A - Top-Up Questions", "questions": [{"id": "three"}]},
            {"title": "Section A", "questions": [{"id": "four"}]},
        ])

        self.assertTrue(changed)
        self.assertEqual([row["title"] for row in plan], [
            "Section A - Information Based", "Section B - Concept Based", "Section A",
        ])
        self.assertEqual([section["title"] for section in sections], [
            "Section A - Information Based", "Section B - Concept Based", "Section A",
        ])
        self.assertEqual([question["id"] for question in sections[-1]["questions"]], ["four", "three"])
        self.assertTrue(all(question.get("section_id") for section in sections for question in section["questions"]))

        second_sections, second_plan, changed_again = reconcile_section_plan(sections, plan)
        self.assertFalse(changed_again)
        self.assertEqual(second_sections, sections)
        self.assertEqual(second_plan, plan)


if __name__ == "__main__":
    unittest.main()
