"""Run only against the local emulator; never access a production database."""
import asyncio
import copy
import os
import unittest
import uuid
from unittest.mock import patch

from firestore_compat import FirestoreDatabase
import completion_store
import paper_review_queue as queue
from test_paper_completion import paper_fixture


@unittest.skipUnless(os.environ.get('FIRESTORE_EMULATOR_HOST', '').startswith(('127.0.0.1:', 'localhost:')), 'Local Firestore emulator required')
class EmulatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FirestoreDatabase('bodha-test-' + uuid.uuid4().hex[:12])
        self.patches = [patch.object(completion_store, 'db', self.db), patch.object(queue, 'db', self.db)]
        for p in self.patches:
            p.start()
        self.paper = paper_fixture()
        await self.db.papers.insert_one(self.paper)
        await queue.enqueue_generation(paper_id='paper', payload={}, revision=1)
        self.job = await queue.claim_next('worker-1')
        self.store = completion_store.CompletionStore(self.job)
        await self.store.load()

    async def asyncTearDown(self):
        self.db._client.close()
        for p in reversed(self.patches):
            p.stop()

    async def test_independent_stale_snapshot_updates_do_not_lose_fields(self):
        snapshots = await self.db.papers._matching_snapshots({'id': 'paper'})
        snapshot, old = snapshots[0]
        await self.db.papers._apply_update(snapshot, old, {'$set': {'topup.status': 'ready'}})
        await self.db.papers._apply_update(snapshot, old, {'$set': {'quality_review.status': 'passed'}})
        paper = await self.db.papers.find_one({'id': 'paper'})
        self.assertEqual(paper['topup']['status'], 'ready')
        self.assertEqual(paper['quality_review']['status'], 'passed')

    async def test_conditional_updates_recheck_fresh_document(self):
        snapshots = await self.db.papers._matching_snapshots({'id': 'paper'})
        await self.db.papers.update_one({'id': 'paper'}, {'$set': {'generation_status': 'cancelled'}})
        with patch.object(self.db.papers, '_matching_snapshots', return_value=snapshots):
            result = await self.db.papers.update_one({'id': 'paper', 'generation_status': 'generating'}, {'$set': {'sections': ['bad']}})
        self.assertEqual(result.modified_count, 0)

    async def test_expired_owner_cannot_publish_or_ack_new_lease(self):
        await self.db.paper_review_jobs.update_one({'id': self.job['id']}, {'$set': {'lease_until': '2000-01-01'}})
        with self.assertRaises(completion_store.OwnershipLost):
            await self.store.save({'sections': ['stale']}, {'sections': ['stale']})
        winner = await queue.claim_next('worker-2')
        self.assertNotEqual(winner['lease_token'], self.job['lease_token'])
        await queue.complete(self.job, status='completed', stage='ready')
        self.assertFalse(await queue.heartbeat(self.job['id'], 'worker-1', self.job['lease_token']))
        with self.assertRaises(completion_store.OwnershipLost):
            await self.store.save({}, {'sections': ['stale']})
        current = await self.db.paper_review_jobs.find_one({'id': self.job['id']})
        self.assertEqual(current['status'], 'leased')
        self.assertEqual(current['worker_id'], 'worker-2')

    async def test_cancellation_and_new_revision_fence_publication(self):
        for fields in ({'generation_status': 'cancelled'}, {'generation_status': 'generating', 'generation_revision': 2}):
            await self.db.papers.update_one({'id': 'paper'}, {'$set': fields})
            with self.assertRaises(completion_store.OwnershipLost):
                await self.store.save({}, {'sections': ['bad']})
        self.assertEqual((await self.db.papers.find_one({'id': 'paper'}))['sections'], [])

    async def test_heartbeat_publication_and_independent_update_agree(self):
        await asyncio.gather(
            self.store.save({'sections': [], 'budget': {'provider_calls_used': 3}}, {'question_count': 0}),
            queue.heartbeat(self.job['id'], 'worker-1', self.job['lease_token']),
            self.db.papers.update_one({'id': 'paper'}, {'$set': {'title': 'Teacher title'}}),
        )
        paper, checkpoint = await self.store.load()
        self.assertEqual(paper['title'], 'Teacher title')
        self.assertEqual(checkpoint['budget']['provider_calls_used'], 3)

    async def test_duplicate_enqueue_has_one_job(self):
        await asyncio.gather(*(queue.enqueue_generation(paper_id='paper', payload={}, revision=1) for _ in range(3)))
        self.assertEqual(len(await self.db.paper_review_jobs._matching_snapshots({'paper_id': 'paper'})), 1)

    async def test_repeated_continuation_snapshots_checkpoint_once(self):
        await self.store.save({'sections': [], 'round': 2, 'budget': {'provider_calls_used': 24}},
                              {'completion': {'status': 'incomplete'}})
        results = await asyncio.gather(*(completion_store.enqueue_continuation(self.paper, {}) for _ in range(2)))
        self.assertEqual([r['revision'] for r in results], [2, 2])
        paper = await self.db.papers.find_one({'id': 'paper'})
        self.assertEqual(paper['generation_revision'], 2)
        checkpoint = await self.db._client.collection('paper_completion_checkpoints').document('paper:2').get()
        import json
        data = json.loads(checkpoint.to_dict()['candidate_json'])
        self.assertEqual(data['budget'], {})
        self.assertEqual(data['round'], 0)

    async def test_teacher_edit_after_cancel_keeps_hidden_accepted_work(self):
        import paper_completion as engine
        from normal_paper_contract import materialize_normal_sections
        plan = engine.compile_plan(self.paper['generation_requirements'], self.paper)
        all_sections = materialize_normal_sections({'questions': [
            {'slot_id': s['slot_id'], 'question': f"Explain operation {s['slot_id']} in detail."} for s in plan['slots'][:2]]}, plan)[0]
        candidate = {'plan': plan, 'spec': self.paper['generation_requirements'], 'sections': all_sections, 'budget': {}, 'round': 1}
        visible = copy.deepcopy(all_sections)
        visible[0]['questions'] = visible[0]['questions'][:1]
        await self.store.save(candidate, {'sections': visible})
        await self.db.papers.update_one({'id': 'paper'}, {'$set': {'generation_status': 'cancelled', 'quality_review.status': 'cancelled'}})
        before = await self.db.papers.find_one({'id': 'paper'})
        edited = copy.deepcopy(visible)
        edited[0]['questions'][0]['question'] = 'Explain this teacher-specified algebraic identity carefully.'
        await completion_store.save_teacher_edit(before, {'sections': edited})
        checkpoint = await self.db._client.collection('paper_completion_checkpoints').document('paper:2').get()
        import json
        saved = json.loads(checkpoint.to_dict()['candidate_json'])
        self.assertEqual(len(engine.questions(saved['sections'])), 2)
        self.assertEqual(engine.questions(saved['sections'])[0]['question'], edited[0]['questions'][0]['question'])
        with self.assertRaises(completion_store.OwnershipLost):
            await self.store.save(candidate, {'sections': all_sections})

    async def test_legacy_worker_writes_are_also_lease_fenced(self):
        from firestore_compat import worker_write_lease
        jobs = await self.db.paper_review_jobs._matching_snapshots({'id': self.job['id']})
        token = worker_write_lease.set({'job': self.job, 'reference': jobs[0][0].reference})
        try:
            await self.db.paper_review_jobs.update_one({'id': self.job['id']}, {'$set': {'lease_until': '2000-01-01'}})
            result = await self.db.papers.update_one({'id': 'paper'}, {'$set': {'sections': ['stale']}})
            self.assertEqual(result.modified_count, 0)
        finally:
            worker_write_lease.reset(token)
        self.assertEqual((await self.db.papers.find_one({'id': 'paper'}))['sections'], [])

    async def test_worker_resumes_checkpoint_after_initial_topup_and_or_restart(self):
        import paper_completion as engine
        import paper_review_worker as worker
        from normal_paper_contract import materialize_normal_sections
        from unittest.mock import AsyncMock
        plan = engine.compile_plan(self.paper['generation_requirements'], self.paper)
        for stage, count, round_index in [('generating', 20, 0), ('repairing', 33, 1), ('alternatives', 41, 1)]:
            # Each stage is a new paper revision with the last accepted checkpoint.
            revision = self.job['revision']
            sections = materialize_normal_sections({'questions': [{'slot_id': s['slot_id'],
                'question': f"Explain algebraic operation {s['slot_id']} completely."} for s in plan['slots'][:count]]}, plan)[0]
            if stage == 'alternatives':
                engine.questions(sections)[0]['question'] += '\nOR\nExplain a different algebraic operation.'
            candidate = {'spec': self.paper['generation_requirements'], 'plan': plan, 'sections': sections,
                'rejected': [], 'round': round_index, 'phase': stage, 'repair_count': 0,
                'first_pass_valid_count': count, 'budget': {'provider_calls_used': 4, 'elapsed_seconds': 10}}
            await self.store.save(candidate, {'generation_status': 'generating'})
            await self.db.paper_review_jobs.update_one({'id': self.job['id']}, {'$set': {'lease_until': '2000-01-01'}})
            winner = await queue.claim_next('resumed-worker')
            winner['engine_version'] = 'v2'
            requested = []
            async def generate(slots, candidate, paper, payload, ref):
                requested.extend(s['slot_id'] for s in slots)
                return materialize_normal_sections({'questions': [{'slot_id': s['slot_id'],
                    'question': f"Explain restored operation {s['slot_id']} completely."} for s in slots]}, plan, only_slots=slots)[0]
            async def repair(working, qid, **kwargs):
                q = next(q for q in engine.questions(working['sections']) if q['id'] == qid)
                return {'question': {**q, 'question': q['question'] + '\nOR\nExplain an alternative algebraic rule.'}}
            with patch.object(worker, 'db', self.db), patch.object(engine, 'generate_batch', generate), \
                 patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
                 patch.object(engine, 'is_duplicate', AsyncMock(return_value=(False, None))), \
                 patch('workers.regenerate_question', repair), patch('workers.generate_diagrams_for_paper', AsyncMock()):
                await worker.process_job(winner, 'resumed-worker')
            current = await self.db.papers.find_one({'id': 'paper'})
            self.assertTrue(current['completion']['ready'], current['completion'])
            self.assertEqual(len(requested), 41 - count)
            self.assertFalse(set(requested) & {s['slot_id'] for s in plan['slots'][:count]})
            # Prepare the next simulated interruption without touching any production data.
            await self.db.papers.update_one({'id': 'paper'}, {'$set': {'generation_revision': revision + 1, 'completion': None}})
            await queue.enqueue_generation(paper_id='paper', revision=revision + 1, payload={})
            self.job = await queue.claim_next('worker-1')
            self.store = completion_store.CompletionStore(self.job)
            await self.store.load()
