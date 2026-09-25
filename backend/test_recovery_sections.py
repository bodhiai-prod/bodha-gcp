import json
import unittest

from llm_adapter import parse_json_response
from workers import restore_recovered_sections


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


if __name__ == "__main__":
    unittest.main()
