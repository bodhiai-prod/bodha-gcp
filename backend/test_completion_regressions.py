"""Regression probes for the production incomplete-paper failures."""
import copy
from unittest.mock import AsyncMock, patch
import asyncio
import workers
from question_repair import validate_question
from section_blueprints import paper_summary
from test_section_blueprints import fixture


def test_complete_mcq_predicate_is_not_truncated():
    assert validate_question({'question': 'The derivative of x squared is', 'format': 'mcq',
                              'options': ['2x', 'x', '2', '0'], 'correct_option': 0}) == []


def test_summary_does_not_report_planned_parents_as_actual():
    summary = paper_summary({'generation_requirements': {'printed_question_count': 22, 'total_questions': 41},
                             'sections': [{'questions': [{'id': 'a', 'parent_id': 'p1'}, {'id': 'b', 'parent_id': 'p1'}]}]})
    assert summary['question_count'] == 1
    assert summary['scored_part_count'] == 2


def test_actual_template_generator_does_not_skip_topup_without_stated_flag():
    asyncio.run(_template_probe())


async def _template_probe():
    _, bp, _, spec, sections = fixture()
    sections[0]['questions'].pop()
    db = type('DB', (), {'papers': type('Papers', (), {
        'update_one': AsyncMock(), 'find_one': AsyncMock(return_value={'id': 'test'})})()})()
    with patch.object(workers, 'db', db), \
         patch.object(workers, '_request_paper_json', AsyncMock(return_value={'sections': sections})), \
         patch.object(workers, 'record_activity', AsyncMock()), \
         patch.object(workers, 'complete_sections', AsyncMock(return_value={})), \
         patch.object(workers, 'repair_sections', AsyncMock(return_value={})), \
         patch.object(workers, 'dedupe_sections', AsyncMock(return_value={})), \
         patch.object(workers, '_auto_topup_if_short', AsyncMock()) as topup:
        await workers.generate_paper_background(paper_id='test', owner_id='teacher', subject='Physics', klass='12',
            topics_weighted=[{'name': 'Transformers', 'weight': 5}], difficulty='medium', distribution={}, total_marks=6,
            duration_minutes=30, context_excerpt='', feedback_hints='', section_blueprint=bp['template'], saved_requirements=spec)
    topup.assert_awaited()


def test_or_helper_retains_first_accepted_repair_when_second_fails():
    async def run():
        _, _, _, spec, sections = fixture()
        spec['blueprint']['sections'][0]['groups'][0]['internal_choices'] = 2
        async def regenerate(paper, qid, **kwargs):
            if qid.endswith('1'):
                raise RuntimeError('incomplete provider response')
            q = copy.deepcopy(next(q for s in sections for q in s['questions'] if q['id'] == qid))
            q['question'] += '\nOR\nDescribe another transformer operating principle.'
            return {'question': q, 'old_text': 'old', 'diagram_pending': False}
        checkpoint = AsyncMock()
        with patch.object(workers, 'is_duplicate', AsyncMock(return_value=(False, None))), patch.object(workers, 'regenerate_question', regenerate):
            result = await workers.repair_internal_choices({'sections': sections, 'generation_requirements': spec}, on_accept=checkpoint)
        assert len(result['repairs']) == 1
        assert result['remaining']
        assert '\nOR\n' in result['sections'][0]['questions'][0]['question']
        checkpoint.assert_awaited_once()
    asyncio.run(run())
