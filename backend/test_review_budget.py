import time
import unittest

from review_budget import ReviewBudgetExceeded, begin, reserve_provider_attempt


class ReviewBudgetTests(unittest.TestCase):
    def tearDown(self):
        from review_budget import clear
        clear()

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


class PersistedBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from review_budget import clear
        clear()

    async def test_nested_provider_attempts_persist_before_dispatch(self):
        import llm_adapter
        from unittest.mock import patch
        saved, dispatched = [], []
        budget = begin(seconds=360, call_limit=24)
        async def persist():
            saved.append(budget.calls_used)
        budget.persist = persist
        def provider(*args):
            dispatched.append(saved[-1])
            if len(dispatched) == 1:
                raise RuntimeError('temporary 503')
            return 'complete'
        with patch.object(llm_adapter, 'LOAD_TEST_STUB', False), \
             patch.object(llm_adapter, '_text_complete_sync', provider), \
             patch.object(llm_adapter, 'RETRY_MAX_ATTEMPTS', 2), \
             patch.object(llm_adapter, 'RETRY_BASE_DELAY_SEC', 0), \
             patch.object(llm_adapter, '_is_transient', return_value=True):
            value = await llm_adapter.chat_complete(system_message='test', user_text='test', provider_chain=['test-provider'])
        self.assertEqual(value, 'complete')
        self.assertEqual(dispatched, [1, 2])
        self.assertEqual(budget.calls_used, 2)

    async def test_threaded_image_retries_share_persisted_allowance(self):
        import asyncio
        from review_budget import reserve_persisted_provider_attempt, reserve_thread_provider_retry
        budget = begin(seconds=360, call_limit=2)
        saved = []
        async def persist():
            saved.append(budget.calls_used)
        budget.persist = persist
        await reserve_persisted_provider_attempt()
        await asyncio.to_thread(reserve_thread_provider_retry)
        with self.assertRaises(ReviewBudgetExceeded):
            await asyncio.to_thread(reserve_thread_provider_retry)
        self.assertEqual(saved, [1, 2])
