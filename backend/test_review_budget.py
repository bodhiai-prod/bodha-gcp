import time
import unittest

from review_budget import ReviewBudgetExceeded, begin, reserve_provider_attempt


class ReviewBudgetTests(unittest.TestCase):
    def test_every_provider_attempt_consumes_the_shared_limit(self):
        budget = begin(seconds=60, call_limit=3)
        for _ in range(3):
            reserve_provider_attempt()
        with self.assertRaises(ReviewBudgetExceeded):
            reserve_provider_attempt()
        self.assertEqual(budget.calls_used, 3)
        self.assertIn("3-call", budget.exhausted_reason)

    def test_expired_budget_rejects_call_without_billing_it(self):
        budget = begin(seconds=0.001, call_limit=3)
        time.sleep(0.01)
        with self.assertRaises(ReviewBudgetExceeded):
            reserve_provider_attempt()
        self.assertEqual(budget.calls_used, 0)
        self.assertTrue(budget.snapshot()["deadline_exceeded"])
