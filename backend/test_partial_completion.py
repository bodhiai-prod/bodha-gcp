"""Near-complete stems remain available while missing OR choices are repaired."""
from unittest.mock import AsyncMock, patch
import asyncio
import paper_completion as engine
from normal_paper_contract import materialize_normal_sections
from test_paper_completion import MemoryStore, paper_fixture


def candidate_with_stems(count=38, choices=10):
    paper = paper_fixture(41, choices)
    plan = engine.compile_plan(paper['generation_requirements'], paper)
    sections = materialize_normal_sections({'questions': [
        {'slot_id': s['slot_id'], 'question': f"Explain algebraic operation {s['slot_id']} thoroughly."}
        for s in plan['slots'][:count]]}, plan)[0]
    candidate = {'spec': paper['generation_requirements'], 'plan': plan, 'sections': sections,
                 'rejected': [], 'round': 0, 'repair_count': 0, 'first_pass_valid_count': count}
    return paper, candidate


def test_pending_alternatives_do_not_hide_38_of_41_safe_stems():
    paper, candidate = candidate_with_stems()
    result = engine.evaluate(candidate, paper)
    assert result['preview_available']
    assert len(engine.questions(result['visible'])) == 38
    assert not result['ready']


def test_missing_base_slot_does_not_starve_all_or_repairs():
    asyncio.run(_missing_base_slot_probe())


async def _missing_base_slot_probe():
    paper, candidate = candidate_with_stems(40, 2)
    store = MemoryStore(paper)
    store.candidate = candidate
    async def repair(working, qid, **kwargs):
        q = next(q for q in engine.questions(working['sections']) if q['id'] == qid)
        return {'question': {**q, 'question': q['question'] + '\nOR\nExplain a second algebraic operation thoroughly.'}}
    with patch.object(engine, 'generate_batch', AsyncMock(return_value=[])), \
         patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
         patch.object(engine, 'is_duplicate', AsyncMock(return_value=(False, None))), \
         patch('workers.regenerate_question', AsyncMock(side_effect=repair)) as regenerate, \
         patch('workers.generate_diagrams_for_paper', AsyncMock()):
        await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {}}, store=store)
    assert regenerate.await_count == 2
    assert len(engine.questions(store.paper['sections'])) == 40
    assert store.paper['completion']['unresolved_count'] == 1


def test_initial_template_prompt_contains_actual_or_requirements():
    import json
    from prompts import normal_slots_prompt
    paper = paper_fixture(41, 10)
    plan = engine.compile_plan(paper['generation_requirements'], paper)
    prompt = normal_slots_prompt(subject='Maths', klass='12', slots=plan['slots'], context_excerpt='')
    rows = json.loads(prompt.split('SLOTS:\n')[1].split('\n\n')[0])
    assert sum(row['internal_choice_count'] for row in rows) == 10
    assert 'standalone OR lines' in prompt


def test_partial_or_targets_keep_other_groups_repairable():
    import workers
    paper, candidate = candidate_with_stems(40, 2)
    spec = candidate['spec']
    spec['blueprint']['sections'][0]['groups'].append({'id': 'absent', 'internal_choices': 1})
    spec['section_allocations'][-1]['group_id'] = 'absent'
    targets = workers.internal_choice_repair_targets(candidate['sections'], spec, allow_partial=True)
    assert len(targets) == 2


def test_legacy_or_repair_does_not_abort_for_an_absent_group():
    async def run():
        import workers
        paper, candidate = candidate_with_stems(40, 2)
        candidate['spec']['blueprint']['sections'][0]['groups'].append({'id': 'absent', 'internal_choices': 1})
        candidate['spec']['section_allocations'][-1]['group_id'] = 'absent'
        async def repair(working, qid, **kwargs):
            q = next(q for q in engine.questions(working['sections']) if q['id'] == qid)
            return {'question': {**q, 'question': q['question'] + '\nOR\nDescribe a different algebraic operation.'},
                    'old_text': q['question'], 'diagram_pending': False}
        with patch('workers.is_duplicate', AsyncMock(return_value=(False, None))), \
             patch('workers.regenerate_question', AsyncMock(side_effect=repair)):
            result = await workers.repair_internal_choices({**paper, 'sections': candidate['sections']})
        assert len(result['repairs']) == 2
        assert len(result['remaining']) == 1
        assert result['remaining'][0]['group_id'] == 'absent'
    asyncio.run(run())


def test_legacy_topup_repairs_alternatives_on_restored_slots():
    async def run():
        import copy
        import workers
        from test_section_blueprints import fixture
        from section_blueprints import derived_internal_choice_count
        _, bp, _, spec, complete = fixture()
        spec['blueprint']['sections'][1]['groups'][0]['internal_choices'] = 2
        initial = copy.deepcopy(complete)
        initial[1]['questions'].pop()
        latest = {'id': 'test', 'sections': complete, 'generation_requirements': spec}
        papers = type('Papers', (), {'update_one': AsyncMock(), 'find_one': AsyncMock(return_value=latest)})()
        async def regenerate(paper, qid, **kwargs):
            q = next(q for q in engine.questions(paper['sections']) if q['id'] == qid)
            return {'question': {**q, 'question': q['question'] + '\nOR\nDescribe another transformer operating principle.'},
                    'old_text': q['question'], 'diagram_pending': False}
        with patch.object(workers, 'db', type('DB', (), {'papers': papers})()), \
             patch.object(workers, '_request_paper_json', AsyncMock(return_value={'sections': initial})), \
             patch.object(workers, 'record_activity', AsyncMock()), \
             patch.object(workers, 'complete_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'repair_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'dedupe_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'is_duplicate', AsyncMock(return_value=(False, None))), \
             patch.object(workers, '_auto_topup_if_short', AsyncMock()), \
             patch.object(workers, 'regenerate_question', regenerate):
            await workers.generate_paper_background(paper_id='test', owner_id='teacher', subject='Physics', klass='12',
                topics_weighted=[{'name': 'Transformers', 'weight': 5}], difficulty='medium', distribution={}, total_marks=6,
                duration_minutes=30, context_excerpt='', feedback_hints='', section_blueprint=bp['template'], saved_requirements=spec)
        published = [call.args[1]['$set'] for call in papers.update_one.await_args_list
                     if 'sections' in call.args[1].get('$set', {})][-1]
        assert len(engine.questions(published['sections'])) == 4
        assert sum(derived_internal_choice_count(q) for q in engine.questions(published['sections'])) == 2
        assert published['quality_review.internal_choice_diagnostics'] == []
    asyncio.run(run())


def test_legacy_continuation_preserves_unconstrained_cognitive_types():
    from normal_paper_contract import validate_normal_plan
    paper, candidate = candidate_with_stems(40, 0)
    paper['sections'] = candidate['sections']
    for q in engine.questions(paper['sections']):
        q['type'] = 'application'
    plan = engine.compile_plan(paper['generation_requirements'], paper)
    adopted = engine.adopt_existing(paper['sections'], plan)
    assert not [issue for issue in validate_normal_plan(adopted, plan) if issue.get('field') == 'type']
