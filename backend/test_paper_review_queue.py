import asyncio
import unittest

import paper_review_queue as queue


class _Result:
    def __init__(self, count=1):
        self.modified_count = count


class _Collection:
    def __init__(self):
        self.inserted = []
        self.updates = []

    async def insert_one(self, document):
        self.inserted.append(document)
        return _Result()

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return _Result()


class _Db:
    def __init__(self):
        self.paper_review_jobs = _Collection()


class PaperReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.original_db = queue.db
        self.db = _Db()
        queue.db = self.db

    def tearDown(self):
        queue.db = self.original_db

    def test_enqueue_creates_durable_queued_job(self):
        job = asyncio.run(queue.enqueue_generation(paper_id="p1", payload={"paper_id": "p1"}))
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["stage"], "queued")
        self.assertEqual(job["paper_id"], "p1")
        self.assertEqual(self.db.paper_review_jobs.inserted[0]["id"], job["id"])

    def test_exhausted_retry_becomes_needs_review(self):
        job = {"id": "j1", "attempt": 2, "max_attempts": 3}
        status = asyncio.run(queue.retry_or_needs_review(job, "timed out"))
        self.assertEqual(status, "needs_review")
        _query, update = self.db.paper_review_jobs.updates[-1]
        self.assertEqual(update["$set"]["status"], "needs_review")

    def test_diagrams_are_a_separate_durable_job_kind(self):
        job = asyncio.run(queue.enqueue_diagrams(paper_id="p1", owner_id="u1", revision=2))
        self.assertEqual(job["kind"], "paper_diagram")
        self.assertEqual(job["revision"], 2)
        self.assertEqual(job["max_attempts"], 2)

    def test_retry_is_requeued_with_backoff(self):
        job = {"id": "j1", "attempt": 0, "max_attempts": 3}
        status = asyncio.run(queue.retry_or_needs_review(job, "temporary"))
        self.assertEqual(status, "queued")
        _query, update = self.db.paper_review_jobs.updates[-1]
        self.assertEqual(update["$set"]["attempt"], 1)
        self.assertEqual(update["$set"]["stage"], "retrying")
