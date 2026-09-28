import unittest

from generation_requirements import apportion, compile_requirements, remaining_requirements, check_ui_requirements
from prompts import qgen_prompt


class RequirementsTests(unittest.TestCase):
    def compile(self, **overrides):
        args = dict(total_marks=50, total_questions=13,
                    distribution={"information": 40, "concept": 40, "application": 20},
                    format_distribution={"mcq": 40, "long_answer": 40, "short_answer": 20, "true_false": 0},
                    topics=[{"name": "Mechanics", "difficulty": "easy"},
                            {"name": "Mechanics > Forces", "difficulty": "hard"}], difficulty="medium")
        args.update(overrides)
        return compile_requirements(**args)

    def prompt(self, spec, **overrides):
        args = dict(subject="Physics", klass="10", topics_weighted=[{"name": "Mechanics", "weight": 5}],
                    difficulty="medium", distribution={"information":40,"concept":40,"application":20},
                    total_marks=50, duration=60, context_excerpt="", spec=spec)
        args.update(overrides)
        return qgen_prompt(**args)

    def test_apportion_always_sums_and_excludes_zero(self):
        for total in range(1, 201):
            for mix in [{"a":40,"b":40,"c":20,"d":0}, {"a":1,"b":98,"c":1,"d":0}]:
                counts = apportion(mix, total)
                self.assertEqual(sum(counts.values()), total)
                self.assertEqual(counts["d"], 0)

    def test_ui_counts_are_exact_and_both_mixes_agree(self):
        spec = self.compile()
        self.assertEqual(sum(spec["format_counts"].values()), 13)
        self.assertEqual(sum(spec["type_counts"].values()), 13)
        prompt = self.prompt(spec)
        self.assertIn("Produce EXACTLY 13 questions", prompt)
        self.assertIn("EXACTLY 5 questions with format='mcq'", prompt)
        self.assertNotIn("~5", prompt)
        self.assertNotIn("1-2 marks for information", prompt)

    def test_unrelated_prose_preserves_settings(self):
        self.assertEqual(self.compile()["format_counts"],
                         self.compile(instruction_spec={"unparsed":["Use SI units"]})["format_counts"])

    def test_explicit_counts_replace_ui_formats(self):
        spec = self.compile(format_counts={"mcq":10,"long_answer":3},
                            instruction_spec={"format_counts":{"mcq":2,"long_answer":2}})
        self.assertEqual(spec["total_questions"], 4)
        self.assertEqual(spec["format_counts"], {"mcq":2,"long_answer":2})
        self.assertEqual(sum(spec["type_counts"].values()), 4)

    def test_total_override_recomputes_mix(self):
        spec = self.compile(format_counts={"mcq":10,"long_answer":3}, instruction_spec={"total_questions":20})
        self.assertEqual(spec["total_questions"], 20)
        self.assertEqual(sum(spec["format_counts"].values()), 20)

    def test_difficulty_overrides_resolve_to_canonical_topics(self):
        spec = self.compile(instruction_spec={"difficulty":"medium","topic_difficulty":{"Forces":"easy"}})
        self.assertEqual(spec["topic_difficulty"], {"Mechanics":"medium","Mechanics > Forces":"easy"})

    def test_blueprint_owns_structure_not_ui_percentages(self):
        spec = self.compile(has_blueprint=True, blueprint_spec={"total_questions":38})
        self.assertEqual(spec["total_questions"], 38)
        self.assertNotIn("type_counts", spec)
        self.assertNotIn("format_counts", spec)
        prompt = self.prompt(spec, section_blueprint="38 questions, 80 marks")
        self.assertNotIn("Total marks: 50", prompt)
        self.assertNotIn("so the paper still totals 50", prompt)

    def test_normal_paper_schema_does_not_request_template_identities(self):
        prompt = self.prompt(self.compile())
        self.assertNotIn('"section_id": "A"', prompt)
        self.assertNotIn('"allocation_id": "exact contract id"', prompt)
        self.assertNotIn('"textbook_id": "exact contract id"', prompt)

    def test_template_schema_requests_only_the_identities_it_can_validate(self):
        spec = dict(self.compile(has_blueprint=True), blueprint={"sections": [{"id": "A"}]})
        prompt = self.prompt(spec, section_blueprint="Section A: one question")
        self.assertIn('"section_id": "A"', prompt)
        self.assertNotIn('"allocation_id": "exact contract id"', prompt)

        allocated = dict(spec, section_allocations=[{"allocation_id": "a"}], blueprint={"sections": []})
        prompt = self.prompt(allocated, section_blueprint="Section A: one question")
        self.assertIn('"allocation_id": "exact contract id"', prompt)

    def test_topup_uses_only_gaps_and_remaining_marks(self):
        spec = self.compile(total_questions=4, total_marks=8,
                            format_counts={"mcq":2,"long_answer":2}, diagram_count=2)
        sections = [{"questions":[{"format":"mcq","type":"information","marks":2,"needs_diagram":True},
                                   {"format":"mcq","type":"concept","marks":2}]}]
        remaining = remaining_requirements(spec, sections, 2)
        self.assertEqual(remaining["format_counts"], {"mcq":0,"long_answer":2})
        self.assertEqual(remaining["total_marks"], 4)
        self.assertEqual(remaining["diagram_count"], 1)
        self.assertEqual(spec["total_questions"], 4)  # no mutation
        prompt = self.prompt(remaining, topup_count=2)
        self.assertIn("Produce EXACTLY 2 questions", prompt)
        self.assertNotIn("Total marks: 50", prompt)
        self.assertIn("total EXACTLY 4.0 marks", prompt)

    def test_zero_diagrams_is_an_explicit_requirement(self):
        spec = self.compile(diagram_count=4, instruction_spec={"diagram_count":0})
        self.assertEqual(spec["diagram_count"], 0)
        self.assertIn("EXACTLY 0 questions must include", self.prompt(spec))

    def test_ui_verification_reports_mismatches(self):
        spec = {"type_counts":{"information":1},"total_marks":2,"diagram_count":0}
        valid = [{"questions":[{"type":"information","marks":2,"needs_diagram":False}]}]
        self.assertEqual(check_ui_requirements(valid, spec), [])
        invalid = [{"questions":[{"type":"concept","marks":1,"needs_diagram":True}]}]
        self.assertEqual(len(check_ui_requirements(invalid, spec)), 3)


if __name__ == "__main__":
    unittest.main()
