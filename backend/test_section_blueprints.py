import copy
import unittest
from unittest.mock import AsyncMock, patch

from section_blueprints import (Allocation, catalog, blueprint_errors, compile_allocations,
    topic_catalog, allocation_misses, paper_summary, apply_allocation_overrides)
from generation_requirements import remaining_requirements
from textbook_topics import merge_topic_batches, topic_context
from prompts import qgen_prompt


def fixture():
    book = {"id": "book-one", "original_filename": "Physics", "owner_id": "teacher", "is_deleted": False,
            "subject": "Physics", "class_name": "12", "chunks": ["Preface", "Transformers use electromagnetic induction."],
            "topics": [{"name": "Electromagnetism", "subtopics": ["Transformers"], "source_chunks": [1]}]}
    bp = {"id": "test", "version": 1, "label": "Test blueprint", "total_marks": 6, "duration_minutes": 30,
          "template": "Section A: 2 short questions of 1 mark. Section B: 2 short questions of 2 marks.",
          "sections": [{"id": sid, "title": f"Section {sid}", "groups": [{"id": "main", "count": 2,
                       "marks": marks, "format": "short_answer"}]} for sid, marks in [("A", 1), ("B", 2)]]}
    topic = topic_catalog(book)[1]
    rows = [Allocation(section_id=sid, group_id="main", textbook_id=book["id"], topic_id=topic["topic_id"], count=2,
                       difficulty="hard" if sid == "A" else None, diagrams=False) for sid in ["A", "B"]]
    spec, errors = compile_allocations(bp, rows, [book], "medium", 6)
    assert not errors
    sections = []
    for row in spec["section_allocations"]:
        qs = [{"id": f"{row['section_id']}-{i}", "question": f"Explain transformer behavior in situation {row['section_id']} {i}.",
               "topic": row["name"], "type": "concept", "needs_diagram": False, "internal_choice_count": 0,
               **{key: row[key] for key in ("allocation_id", "topic_id", "textbook_id", "group_id", "difficulty", "marks", "format")}} for i in range(row["count"])]
        sections.append({"section_id": row["section_id"], "title": f"Section {row['section_id']}", "questions": qs})
    return book, bp, rows, spec, sections


class BlueprintTests(unittest.TestCase):
    def test_catalog_arithmetic_and_optional_marks(self):
        presets = {b["id"]: b for b in catalog()}
        for key in ("icse10", "isc12", "cbse12"):
            self.assertEqual(blueprint_errors(presets[key]), [], key)
        self.assertIn("79", " ".join(blueprint_errors(presets["cbse10"])))

    def test_topic_identity_includes_book_and_parent(self):
        book, *_ = fixture()
        second = {**book, "id": "book-two"}
        self.assertNotEqual(topic_catalog(book)[1]["topic_id"], topic_catalog(second)[1]["topic_id"])
        book["topics"].append({"name": "Other chapter", "subtopics": ["Transformers"]})
        self.assertEqual(len({r["topic_id"] for r in topic_catalog(book)}), 4)

    def test_exact_allocations_and_difficulty_inheritance(self):
        _, _, _, spec, sections = fixture()
        self.assertEqual(spec["total_questions"], 4)
        self.assertEqual([r["difficulty"] for r in spec["section_allocations"]], ["hard", "medium"])
        self.assertEqual(allocation_misses(sections, spec), [])

    def test_duplicate_stale_and_underallocated_rows_rejected(self):
        book, bp, rows, *_ = fixture()
        _, errors = compile_allocations(bp, [rows[0], rows[0]], [book], "medium", 6)
        self.assertTrue(any("duplicated" in e for e in errors))
        _, errors = compile_allocations(bp, rows, [{**book, "id": "other"}], "medium", 6)
        self.assertTrue(any("stale" in e for e in errors))

    def test_zero_and_fractional_counts_rejected(self):
        for value in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                Allocation(section_id="A", group_id="g", textbook_id="b", topic_id="t", count=value)

    def test_diagram_minimum_and_disabled_rows(self):
        _, _, _, spec, sections = fixture()
        spec["section_allocations"][0]["diagrams"] = True
        self.assertTrue(any("at least one" in e for e in allocation_misses(sections, spec)))
        sections[0]["questions"][0]["needs_diagram"] = True
        self.assertEqual(allocation_misses(sections, spec), [])
        sections[1]["questions"][0]["needs_diagram"] = True
        self.assertTrue(any("disabled" in e for e in allocation_misses(sections, spec)))

    def test_global_diagram_count_conflict(self):
        book, bp, rows, *_ = fixture()
        _, errors = compile_allocations(bp, rows, [book], "medium", 6, diagram_count=1)
        self.assertTrue(any("between 0 and 0" in e for e in errors))

    def test_section_and_question_overrides_win(self):
        _, _, _, spec, sections = fixture()
        spec, errors = apply_allocation_overrides(spec, {"section_difficulty": {"Section A": "easy"},
            "section_diagrams": {"Section B": True}, "question_overrides": [{"n": 2, "difficulty": "hard"}]})
        self.assertFalse(errors)
        sections[0]["questions"][0]["difficulty"] = "easy"
        sections[1]["questions"][0]["needs_diagram"] = True
        self.assertEqual(allocation_misses(sections, spec), [])

    def test_structural_instruction_conflicts_are_visible(self):
        _, _, _, spec, _ = fixture()
        _, errors = apply_allocation_overrides(spec, {"total_questions": 10})
        self.assertTrue(errors)

    def test_wrong_section_and_marks_are_detected(self):
        _, _, _, spec, sections = fixture()
        sections[0]["questions"][0]["marks"] = 8
        sections[1]["questions"].append(sections[0]["questions"].pop())
        misses = allocation_misses(sections, spec)
        self.assertTrue(any("expected 1 marks" in e for e in misses))
        self.assertTrue(any("must be in Section A" in e for e in misses))

    def test_topup_only_requests_unfilled_rows(self):
        _, _, _, spec, sections = fixture()
        partial = [sections[0], {**sections[1], "questions": sections[1]["questions"][:1]}]
        remaining = remaining_requirements(spec, partial, 1)
        self.assertEqual(remaining["total_questions"], 1)
        self.assertEqual(remaining["total_marks"], 2)
        self.assertEqual(remaining["section_allocations"][0]["section_id"], "B")
        self.assertEqual(spec["section_allocations"][1]["count"], 2)

    def test_topup_preserves_already_met_diagram_minimum(self):
        _, _, _, spec, sections = fixture()
        spec["section_allocations"][0]["diagrams"] = True
        sections[0]["questions"][0]["needs_diagram"] = True
        partial = [{**sections[0], "questions": sections[0]["questions"][:1]}, sections[1]]
        remaining = remaining_requirements(spec, partial, 1)
        self.assertTrue(remaining["section_allocations"][0]["diagram_minimum_satisfied"])

    def test_prompt_contains_contract_without_global_topic_quotas(self):
        _, bp, _, spec, _ = fixture()
        prompt = qgen_prompt(subject="Physics", klass="12", topics_weighted=[{"name": "Transformers", "weight": 5}],
            difficulty="medium", distribution={}, total_marks=6, duration=30, context_excerpt="source", spec=spec,
            section_blueprint=bp["template"], custom_instructions="Use whole numbers.")
        self.assertIn("SECTION ALLOCATION CONTRACT", prompt)
        self.assertIn(spec["section_allocations"][0]["allocation_id"], prompt)
        self.assertIn("Use whole numbers.", prompt)
        self.assertIn("Do not apply global topic weights", prompt)

    def test_summary_reflects_edits_and_diagram_failures(self):
        _, _, _, spec, sections = fixture()
        paper = {"generation_requirements": spec, "sections": sections, "total_marks": 6}
        summary = paper_summary(paper)
        self.assertEqual(summary["question_count"], 4)
        self.assertEqual(summary["attemptable_marks"], 6)
        sections[0]["questions"][0].update(needs_diagram=True, diagram_status="failed", marks=3)
        summary = paper_summary(paper)
        self.assertEqual(summary["offered_marks"], 8)
        self.assertEqual(summary["diagrams"]["failed"], 1)
        self.assertIsNone(summary["attemptable_marks"])

    def test_legacy_summary_marks_unknown_classifications(self):
        summary = paper_summary({"sections": [{"title": "Old", "questions": [{"marks": 3}]}]})
        self.assertEqual(summary["difficulty"][-1]["count"], 1)
        self.assertEqual(summary["choices_unspecified"], 1)
        self.assertIsNone(summary["blueprint"])

    def test_extraction_merges_without_silent_topic_caps(self):
        topics = [{"name": f"Topic {i}", "subtopics": [f"Sub {j}" for j in range(12)], "source_chunks": [i]} for i in range(30)]
        result = merge_topic_batches([(topics, list(range(30))), ([topics[0]], [0])])
        self.assertEqual(len(result), 30)
        self.assertEqual(len(result[0]["subtopics"]), 12)
        self.assertEqual(result[0]["source_chunks"], [0])

    def test_context_retrieves_later_source_chunks(self):
        book, _, _, spec, _ = fixture()
        context = topic_context([book], spec["section_allocations"])
        self.assertIn("Transformers use electromagnetic induction", context)
        self.assertNotIn("Preface", context)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_retains_saved_allocations_and_corrects_mismatch(self):
        import workers
        book, bp, _, spec, sections = fixture()
        bad = copy.deepcopy(sections)
        bad[0]["questions"][0]["difficulty"] = "easy"
        db = type("DB", (), {"papers": type("Papers", (), {"update_one": AsyncMock()})()})()
        with patch.object(workers, "db", db), patch.object(workers, "chat_complete", AsyncMock(side_effect=["bad", "good"])) as chat, \
             patch.object(workers, "parse_json_response", side_effect=[{"sections": bad}, {"sections": sections}]), \
             patch.object(workers, "parse_instructions", AsyncMock()) as parse, \
             patch.object(workers, "complete_sections", AsyncMock(return_value={})), \
             patch.object(workers, "repair_sections", AsyncMock(return_value={})), \
             patch.object(workers, "dedupe_sections", return_value={}), \
             patch.object(workers, "_auto_topup_if_short", AsyncMock()):
            await workers.generate_paper_background(paper_id="test", owner_id="teacher", subject="Physics", klass="12",
                topics_weighted=[{"name": "Transformers", "weight": 5}], difficulty="medium", distribution={}, total_marks=6,
                duration_minutes=30, context_excerpt="Transformers", feedback_hints="", section_blueprint=bp["template"], saved_requirements=spec)
        self.assertEqual(chat.await_count, 2)
        parse.assert_not_awaited()
        saved = db.papers.update_one.call_args_list[-1].args[1]["$set"]
        self.assertTrue(saved["instruction_check"]["honoured"])
        self.assertEqual(saved["sections"][0]["questions"][0]["allocation_id"], spec["section_allocations"][0]["allocation_id"])


if __name__ == "__main__":
    unittest.main()
