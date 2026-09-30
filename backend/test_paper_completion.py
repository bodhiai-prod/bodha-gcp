import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

import paper_completion as engine
from normal_paper_contract import materialize_normal_sections


def paper_fixture(count=41, choices=2):
    rows = [{'allocation_id': f'a{i}', 'slot_id': f's{i}', 'section_id': 'A', 'group_id': 'main',
             'textbook_id': 'book', 'topic_id': 'topic', 'name': 'Algebra', 'marks': 1,
             'format': 'short_answer', 'difficulty': 'medium', 'count': 1, 'diagrams': False,
             'parent_id': f'p{min(i, 21)}'} for i in range(count)]
    spec = {'total_questions': count, 'total_marks': count, 'printed_question_count': min(22, count),
            'section_allocations': rows, 'blueprint': {'id': 'test', 'version': 1, 'total_marks': count,
            'sections': [{'id': 'A', 'title': 'Section A', 'groups': [{'id': 'main', 'count': count,
                         'marks': 1, 'format': 'short_answer', 'internal_choices': choices}]}]}}
    return {'id': 'paper', 'owner_id': 'teacher', 'subject': 'Maths', 'class_name': '12', 'total_marks': count,
            'generation_revision': 1, 'generation_requirements': spec, 'sections': [], 'is_deleted': False}


class MemoryStore:
    def __init__(self, paper):
        self.paper = copy.deepcopy(paper)
        self.candidate = {}
        self.history = []
    async def load(self):
        return copy.deepcopy(self.paper), copy.deepcopy(self.candidate)
    async def save(self, candidate, fields):
        self.candidate = copy.deepcopy(candidate)
        self.paper.update(copy.deepcopy(fields))
        self.history.append(copy.deepcopy(fields))


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    async def trajectory(self, initial_count):
        paper = paper_fixture()
        store = MemoryStore(paper)
        async def generate(slots, candidate, paper, payload, ref):
            qs = []
            for slot in slots:
                n = int(slot['slot_id'][1:])
                if candidate['round'] == 0 and n >= initial_count:
                    continue
                qs.append({'slot_id': slot['slot_id'], 'question': f'Explain algebraic operation number {n} in detail.'})
            return materialize_normal_sections({'questions': qs}, candidate['plan'], only_slots=slots)[0]
        async def duplicate(q, existing, **kwargs):
            # Eight first-pass questions fail dedupe, reproducing 40->32 and 41->33.
            n = int(q['slot_id'][1:])
            return (store.candidate.get('round', 0) == 0 and n < 8), None
        async def repair(working, qid, **kwargs):
            q = next(q for q in engine.questions(working['sections']) if q['id'] == qid)
            q = {**q, 'question': q['question'] + '\nOR\nExplain a second algebraic operation in detail.'}
            return {'question': q}
        with patch.object(engine, 'generate_batch', generate), patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
             patch.object(engine, 'is_duplicate', duplicate), patch('workers.regenerate_question', repair), \
             patch('workers.generate_diagrams_for_paper', AsyncMock()):
            await engine.run_completion({'id': 'job', 'paper_id': 'paper', 'revision': 1, 'payload': {}}, store=store)
        self.assertTrue(store.paper['completion']['ready'], store.paper['completion']['failed_gates'])
        self.assertEqual(len(engine.questions(store.paper['sections'])), 41)
        self.assertEqual(store.candidate['first_pass_valid_count'], initial_count - 8)
        self.assertEqual(len(store.candidate['rejected']), 8)
        for fields in store.history:
            if fields.get('sections'):
                result = fields['completion']
                self.assertTrue(result['ready'] or result['preview_available'])
                self.assertGreaterEqual(result['valid_scored_parts'], 35)
                self.assertNotIn(len(engine.questions(fields['sections'])), (11, 20))
        return store

    async def test_40_to_32_to_41_with_or(self):
        await self.trajectory(40)

    async def test_41_to_33_to_41_with_or(self):
        await self.trajectory(41)

    async def test_exhausted_run_never_publishes_small_paper(self):
        store = MemoryStore(paper_fixture())
        with patch.object(engine, 'generate_batch', AsyncMock(return_value=[])), \
             patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
             patch('workers.generate_diagrams_for_paper', AsyncMock()):
            await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {}}, store=store)
        self.assertEqual(store.paper['generation_status'], 'needs_review')
        self.assertEqual(store.paper['sections'], [])
        self.assertEqual(store.candidate['round'], 3)
        self.assertFalse(store.paper['completion']['export_eligible'])

    async def test_resume_retains_successful_or_after_later_failure(self):
        store = await self.trajectory(41)
        # Simulate checkpoint between the two alternative repairs.
        q = engine.questions(store.candidate['sections'])[1]
        q['question'] = q['question'].split('\nOR\n')[0]
        store.candidate['round'] = 1
        store.paper['completion']['ready'] = False
        before = copy.deepcopy(engine.questions(store.candidate['sections'])[0])
        async def repair(working, qid, **kwargs):
            q = next(q for q in engine.questions(working['sections']) if q['id'] == qid)
            return {'question': {**q, 'question': q['question'] + '\nOR\nExplain the alternate algebraic operation.'}}
        with patch.object(engine, 'generate_batch', AsyncMock()) as gen, patch('workers.regenerate_question', repair), \
             patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
             patch('workers.generate_diagrams_for_paper', AsyncMock()):
            await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {}}, store=store)
        gen.assert_not_awaited()
        self.assertEqual(engine.questions(store.candidate['sections'])[0], before)
        self.assertTrue(store.paper['completion']['ready'])

    async def test_pending_response_resumes_without_regeneration(self):
        paper = paper_fixture(4, 0)
        plan = engine.compile_plan(paper['generation_requirements'], paper)
        sections = materialize_normal_sections({'questions': [{'slot_id': s['slot_id'], 'question': f"Explain algebraic operation {s['slot_id']} clearly."} for s in plan['slots']]}, plan)[0]
        store = MemoryStore(paper)
        store.candidate = {'spec': paper['generation_requirements'], 'plan': plan, 'sections': [],
            'rejected': [], 'round': 0, 'repair_count': 0, 'first_pass_valid_count': None,
            'pending_batches': {','.join(s['slot_id'] for s in plan['slots']): sections},
            'budget': {'provider_calls_used': 1, 'elapsed_seconds': 5}}
        with patch.object(engine, 'generate_batch', AsyncMock()) as generate, \
             patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
             patch.object(engine, 'is_duplicate', AsyncMock(return_value=(False, None))), \
             patch('workers.generate_diagrams_for_paper', AsyncMock()):
            await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {}}, store=store)
        generate.assert_not_awaited()
        self.assertTrue(store.paper['completion']['ready'])
        self.assertEqual(store.candidate['budget']['provider_calls_used'], 1)
        self.assertGreaterEqual(store.candidate['budget']['elapsed_seconds'], 5)

    async def test_real_slot_generator_and_content_review_for_each_mode(self):
        import json
        import re
        for mode in ('normal', 'template', 'competitive'):
            paper = paper_fixture(4, 0)
            if mode != 'template':
                paper.pop('generation_requirements')
                paper.update({'total_questions': 4, 'topics': [{'name': 'Algebra', 'weight': 1}],
                              'format_distribution': {'mcq': 100}, 'distribution': {'concept': 100}})
            store = MemoryStore(paper)
            async def provider(*, user_text, **kwargs):
                if 'SLOTS:\n' in user_text:
                    slots = json.loads(re.search(r'SLOTS:\n(.*?)\n\n', user_text, re.S).group(1))
                    return {'questions': [{'slot_id': slot['slot_id'], 'question': f"Explain the algebraic rule for {slot['slot_id']}.",
                            'options': ['one', 'two', 'three', 'four'], 'correct_option': 0} for slot in slots]}
                checked = json.loads(user_text.split('\n', 1)[1].split('\nScope context:', 1)[0])
                return {'invalid_slot_ids': [], 'checked_slot_ids': [q['slot_id'] for q in checked]}
            async def competitive_provider(**kwargs):
                return json.dumps(await provider(**kwargs))
            if mode == 'competitive':
                paper['is_competitive'] = True
                store.paper['is_competitive'] = True
                # RAG checkpoint already acquired before a simulated restart.
                spec = engine.compile_requirements(total_marks=4, distribution={'concept': 100},
                    format_distribution={'mcq': 100}, topics=paper['topics'], difficulty='medium', total_questions=4)
                store.candidate = {'spec': spec, 'plan': engine.compile_plan(spec, paper), 'sections': [],
                    'round': 0, 'phase': 'generating', 'repair_count': 0, 'rejected': [],
                    'first_pass_valid_count': None, 'anchors': [], 'exam_type': 'GENERIC'}
            with patch('workers._request_paper_json', provider), patch('llm_adapter.chat_complete', competitive_provider), \
                 patch.object(engine, 'parse_instructions', AsyncMock(return_value={})), \
                 patch.object(engine, 'is_duplicate', AsyncMock(return_value=(False, None))), \
                 patch('workers.generate_diagrams_for_paper', AsyncMock()):
                await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {}}, store=store)
            self.assertTrue(store.paper['completion']['ready'], (mode, store.candidate.get('last_error'), store.paper['completion']))

    async def test_official_isc_contract_uses_41_parts_and_22_parents(self):
        from section_blueprints import catalog, Allocation, allocation_buckets, compile_allocations, topic_catalog
        book = {'id': 'book', 'topics': [{'name': name, 'subtopics': []} for name in ('Algebra', 'Vectors', 'Probability')]}
        bp = next(bp for bp in catalog() if bp['id'] == 'isc12-mathematics')
        topics = topic_catalog(book)
        rows = [Allocation(section_id=section['id'], group_id=group['id'], bucket_id=bucket['id'],
                    textbook_id='book', topic_id=topics[index]['topic_id'], count=len(bucket['slots']))
                for index, section in enumerate(bp['sections']) for group in section['groups'] for bucket in allocation_buckets(group)]
        spec, errors = compile_allocations(bp, rows, [book], 'medium', 80)
        self.assertEqual(errors, [])
        paper = {'id': 'isc', 'owner_id': 'teacher', 'total_marks': 80, 'generation_requirements': spec}
        plan = engine.compile_plan(spec, paper)
        self.assertEqual(len(plan['slots']), 41)
        self.assertEqual(len({(s['section_id'], s['parent_id']) for s in plan['slots']}), 22)
        self.assertEqual(len({s['slot_id'] for s in plan['slots']}), 41)
        self.assertTrue(all(len(batch) <= 20 for batch in engine.batches(plan['slots'])))

    async def test_required_or_and_invalid_stem_count_once_per_slot(self):
        paper = paper_fixture(41, 1)
        plan = engine.compile_plan(paper['generation_requirements'], paper)
        sections = materialize_normal_sections({'questions': [{'slot_id': s['slot_id'], 'question': f"Explain operation {s['slot_id']} thoroughly."} for s in plan['slots']]}, plan)[0]
        engine.questions(sections)[0]['question'] = 'A broken stem ending with'
        value = engine.evaluate({'sections': sections, 'plan': plan, 'spec': paper['generation_requirements']}, paper)
        self.assertEqual(value['unresolved_count'], 1)
        self.assertTrue(value['preview_available'])
        self.assertFalse(value['ready'])
