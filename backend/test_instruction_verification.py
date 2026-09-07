"""Unit tests isolate parsing/verification from database and LLM clients."""
import importlib.util
import logging
from pathlib import Path
import types
import unittest
from unittest.mock import patch


def load_verifier():
    deps = types.ModuleType("deps")
    deps.logger = logging.getLogger("test")
    deps.utcnow_iso = lambda: "test"
    llm = types.ModuleType("llm_adapter")
    llm.chat_complete = None
    llm.parse_json_response = None
    module_spec = importlib.util.spec_from_file_location("isolated_instruction_spec", Path(__file__).with_name("instruction_spec.py"))
    module = importlib.util.module_from_spec(module_spec)
    with patch.dict("sys.modules", {"deps": deps, "llm_adapter": llm}):
        module_spec.loader.exec_module(module)
    return module


class VerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_verifier()

    def test_question_override_beats_section_and_topic(self):
        spec = {"section_difficulty":{"Section A":"hard"},
                "topic_difficulty":{"Motion":"medium"},
                "question_overrides":[{"n":1,"difficulty":"easy"}]}
        sections = [{"title":"Section A (10 marks)", "questions":[
            {"topic":"Motion","difficulty":"easy"}, {"topic":"Motion","difficulty":"hard"}]}]
        self.assertTrue(self.module.verify(sections, spec)["honoured"])

    def test_format_override_beats_topic(self):
        spec = {"format_difficulty":{"mcq":"hard"},"topic_difficulty":{"Motion":"easy"}}
        sections = [{"questions":[{"topic":"Motion","format":"mcq","difficulty":"hard"}]}]
        self.assertTrue(self.module.verify(sections, spec)["honoured"])
        sections[0]["questions"][0]["difficulty"] = "easy"
        self.assertFalse(self.module.verify(sections, spec)["honoured"])

    def test_missing_requested_section_is_reported(self):
        self.assertFalse(self.module.verify([], {"section_difficulty":{"Section B":"hard"}})["honoured"])

    def test_new_parser_fields_and_zero_diagrams_survive_normalisation(self):
        result = self.module.normalise({"difficulty":"hard", "diagram_count":0,
            "topic_difficulty":{"Motion":"easy"}, "format_difficulty":{"mcq":"medium"}})
        self.assertEqual(result["diagram_count"], 0)
        self.assertEqual(result["difficulty"], "hard")
        self.assertEqual(result["format_difficulty"], {"mcq":"medium"})

    def test_bad_marks_are_a_reported_mismatch_not_a_crash(self):
        self.assertFalse(self.module.verify([{"questions":[{"marks":"two"}]}],
                                           {"total_questions":1,"total_marks":2})["honoured"])


if __name__ == "__main__":
    unittest.main()
