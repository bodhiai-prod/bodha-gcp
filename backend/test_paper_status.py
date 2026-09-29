import unittest

from paper_status import library_status_for_paper


class PaperLibraryStatusTests(unittest.TestCase):
    def test_pending_generation_includes_progress(self):
        self.assertEqual(
            library_status_for_paper({
                "generation_status": "pending",
                "generation_progress": {"completed": 12, "total": 30},
            }),
            {
                "key": "generating", "label": "Generating", "active": True,
                "progress": {"completed": 12, "total": 30},
            },
        )

    def test_initial_generation_overrides_every_other_state(self):
        status = library_status_for_paper({
            "generation_status": "pending",
            "topup": {"status": "failed"},
            "generation_recovery": {"status": "needs_review"},
        })
        self.assertEqual(status["key"], "generating")

    def test_active_recovery_and_question_jobs_are_updating(self):
        for paper in (
            {"generation_recovery": {"status": "queued", "reason": "allocation_metadata"}},
            {"generation_recovery": {"status": "recovering", "reason": "allocation_metadata"}},
            {"generation_recovery": {"status": "queued", "reason": "internal_choices"}},
            {"topup": {"status": "generating"}},
            {"regen": {"status": "generating"}},
        ):
            with self.subTest(paper=paper):
                self.assertEqual(library_status_for_paper(paper)["key"], "updating")

    def test_review_state_comes_from_current_validation(self):
        stale_paper = {
            "generation_recovery": {"status": "queued", "reason": "validation"},
            "instruction_check": {"honoured": False, "misses": ["wrong count"]},
        }
        self.assertEqual(library_status_for_paper(stale_paper)["key"], "ready")
        self.assertEqual(
            library_status_for_paper(stale_paper, needs_review=True)["key"],
            "needs_review",
        )

    def test_terminal_and_ready_states(self):
        cases = (
            ({"generation_status": "failed"}, "generation_failed"),
            ({"generation_status": "cancelled"}, "generation_cancelled"),
            ({"topup": {"status": "failed"}}, "update_failed"),
            ({"generation_status": "ready"}, "ready"),
        )
        for paper, expected in cases:
            with self.subTest(paper=paper):
                self.assertEqual(library_status_for_paper(paper)["key"], expected)
