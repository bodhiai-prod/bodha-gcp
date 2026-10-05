"""Offline regressions for shared case dependencies (no provider or database)."""
import asyncio
import copy
import unittest
from unittest.mock import AsyncMock
from question_revision import sources, invalidate_changed_solution
from case_study_contract import case_groups, case_issues, case_fingerprint, review_cases, review_misses, repair_slots, parent_context


def fixture():
    slots = [dict(slot_id=f's{i}', allocation_id=f'a{i}', section_id='D', parent_id='q29', case_study=True,
                  marks=1, format='short_answer', difficulty='medium', topic='Motion', internal_choice_count=0)
             for i in range(4)]
    qs = [dict(s, id=f'q{i}', question=f'Use the measured acceleration to calculate quantity {i}.') for i,s in enumerate(slots)]
    qs[0]['shared_stimulus'] = 'u = 2 m/s; a = 3 m/s²; t = 4 s.'
    return {'slots': slots}, [{'section_id':'D','questions':qs}]


class CaseContractTests(unittest.TestCase):
    def test_short_dataset_and_complete_case(self):
        plan, sections = fixture()
        self.assertEqual(case_issues(sections, plan), [])

    def test_later_passage_resolves_without_mutation(self):
        plan, sections = fixture()
        qs = sections[0]['questions']; qs[-1]['shared_stimulus'] = qs[0].pop('shared_stimulus')
        before = copy.deepcopy(sections)
        self.assertTrue(case_groups(sections, plan)[0]['shared_stimulus'])
        self.assertEqual(before, sections)

    def test_conflicting_and_missing_children(self):
        plan, sections = fixture()
        sections[0]['questions'][1]['shared_stimulus'] = 'Other measurements'
        self.assertTrue(case_issues(sections, plan))
        sections[0]['questions'].pop()
        self.assertTrue(any(i['code']=='case_children' for i in case_issues(sections, plan)))

    def test_repeated_passage_is_not_silently_accepted(self):
        plan, sections = fixture()
        sections[0]['questions'][1]['shared_stimulus'] = sections[0]['questions'][0]['shared_stimulus']
        self.assertTrue(any(i['code'] == 'case_stimulus' for i in case_issues(sections, plan)))

    def test_child_repair_freezes_passage_but_missing_passage_expands_parent(self):
        plan, sections = fixture()
        self.assertEqual([s['slot_id'] for s in repair_slots([plan['slots'][2]], sections, plan)], ['s2'])
        self.assertIn('u = 2', str(parent_context(sections, plan)))
        sections[0]['questions'][0].pop('shared_stimulus')
        self.assertEqual(len(repair_slots([plan['slots'][2]], sections, plan)), 4)

    def test_review_is_complete_fingerprinted_and_fail_closed(self):
        plan, sections = fixture()
        bundle = case_groups(sections, plan)[0]
        request = AsyncMock(return_value={'cases':[{'section_id':'D','parent_id':'q29','valid':True,
            'checked_slot_ids':['s0','s1','s2','s3'],'reason':''}]})
        cache = asyncio.run(review_cases(sections, plan, request, ref='test'))
        self.assertEqual(review_misses(sections, plan, cache), [])
        sections[0]['questions'][1]['question'] = 'Define magnetic flux.'
        self.assertTrue(review_misses(sections, plan, cache))
        request.return_value = {'cases':[{'section_id':'D','parent_id':'q29','valid':True,'checked_slot_ids':['s1']}]}
        self.assertTrue(review_misses(sections, plan, asyncio.run(review_cases(sections, plan, request, ref='test'))))
        request.return_value = {'cases':[{'section_id':'D','parent_id':'q29','valid':False,'checked_slot_ids':['s0','s1','s2','s3'],'reason':'Unrelated definitions'}]}
        self.assertTrue(review_misses(sections, plan, asyncio.run(review_cases(sections, plan, request, ref='test'))))
        before = case_fingerprint(bundle)
        sections[0]['questions'][2]['question'] += ' Again.'
        self.assertNotEqual(before, case_fingerprint(case_groups(sections, plan)[0]))

    def test_passage_changes_invalidate_all_sibling_answers(self):
        _, sections = fixture()
        paper = {'sections': sections, 'solution': {'status':'ready','sections':[{'answers':[
            {'question_id':q['id'],'answer':'old'} for q in sections[0]['questions']]}]}}
        revised = copy.deepcopy(sections); revised[0]['questions'][0]['shared_stimulus'] = 'u = 8 m/s'
        result = invalidate_changed_solution(paper, revised)
        self.assertEqual(result['completed'], 0)
        self.assertNotEqual(sources(sections)['q3'], sources(revised)['q3'])
        revised = copy.deepcopy(sections); revised[0]['questions'][2]['question'] += ' Explain.'
        self.assertEqual(invalidate_changed_solution(paper,revised)['completed'], 3)

    def test_ordinary_multipart_is_unaffected(self):
        plan, sections = fixture()
        for s in plan['slots']: s.pop('case_study')
        for q in sections[0]['questions']: q.pop('case_study')
        self.assertEqual(case_groups(sections,plan), [])
