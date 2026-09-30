"""Exercise transformations and acceptance boundaries, without paid providers."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

import paper_completion as engine
import workers
from normal_paper_contract import materialize_normal_sections
from question_revision import invalidate_changed_solution, sources
from test_paper_completion import MemoryStore, paper_fixture
from test_question_regenerate import make_paper, reply
from section_blueprints import (catalog, Allocation, allocation_buckets, compile_allocations,
                                topic_catalog, allocation_prompt)
from prompts import render_spec


def run(coro):
    return asyncio.run(coro)


def test_regeneration_preserves_parent_topic_through_actual_normalization():
    async def check():
        paper = make_paper()
        original = paper['sections'][0]['questions'][1]
        original.update(parent_topic='Optics', slot_id='light-slot', regen_history=['Earlier rejected lens question.'], display_number=12)
        with patch.object(workers, 'chat_complete', AsyncMock(return_value=reply('Describe the refraction of a ray through a glass slab.'))), \
             patch.object(workers, 'complete_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'repair_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'is_duplicate', AsyncMock(return_value=(False, None))):
            result = await workers.regenerate_question(paper, 'q2')
        q = result['question']
        assert q['parent_topic'] == 'Optics'
        assert q['important'] is True and q['display_number'] == 12 and q['slot_id'] == 'light-slot'
        assert original['question'] in q['regen_history']
        assert paper['sections'][0]['questions'][1] == original
    run(check())


def test_missing_metadata_restored_but_conflicting_topic_never_relabelled():
    paper = paper_fixture(2, 0)
    plan = engine.compile_plan(paper['generation_requirements'], paper)
    plan['slots'][0]['parent_topic'] = 'Mathematics'
    sections = materialize_normal_sections({'questions': [{'slot_id': 's0', 'question': 'Explain how to add two fractions.'}]}, plan)[0]
    q = sections[0]['questions'][0]
    q.pop('parent_topic')
    q['topic'] = 'Conflicting saved topic'
    q['important'] = True
    adopted = engine.adopt_existing(sections, plan)[0]['questions'][0]
    assert adopted['parent_topic'] == 'Mathematics'
    assert adopted['topic'] == 'Conflicting saved topic' and adopted['important']


def test_ambiguous_allocation_is_not_assigned_to_first_slot():
    paper = paper_fixture(2, 0)
    plan = engine.compile_plan(paper['generation_requirements'], paper)
    for slot in plan['slots']:
        slot['allocation_id'] = 'shared'
    saved = [{'questions': [{'id': 'teacher-q', 'allocation_id': 'shared', 'question': 'Explain the saved algebraic expression.'}]}]
    assert engine.adopt_existing(saved, plan) == []
    assert saved[0]['questions'][0]['id'] == 'teacher-q'


def test_solution_invalidation_is_content_specific_and_metadata_safe():
    paper = make_paper()
    paper['solution'] = {'status': 'ready', 'completed': 2, 'sections': [{'answers': [
        {'question_id': 'q1', 'answer': 'Answer one'}, {'question_id': 'q2', 'answer': 'Answer two'}]}]}
    sections = copy.deepcopy(paper['sections'])
    sections[0]['questions'][0]['parent_topic'] = 'Physics'
    assert invalidate_changed_solution(paper, sections) is None
    sections[0]['questions'][0]['question'] += '\nOR\nDescribe the effect of resistance on current.'
    solution = invalidate_changed_solution(paper, sections)
    assert solution['sections'][0]['answers'][0]['answer'] == ''
    assert solution['sections'][0]['answers'][1]['answer'] == 'Answer two'
    assert solution['completed'] == 1 and solution['status'] == 'partial'
    assert sources(sections)['q1'] != sources(paper['sections'])['q1']


def test_diagram_replacement_uses_immutable_asset_keys():
    async def check():
        paths = []
        def put(path, data, content_type):
            paths.append(path)
            return {'path': path}
        def candidate():
            return [{'questions': [{'id': 'stable-q', 'needs_diagram': True}]}]
        with patch.object(workers, 'apply_diagram_policy'), patch.object(workers, 'normalise_spec', return_value={'figure': 'circuit'}), \
             patch.object(workers, 'generate_diagram', AsyncMock(side_effect=[b'first-image', b'replacement-image'])), \
             patch.object(workers, 'put_object', side_effect=put):
            first, second = candidate(), candidate()
            await workers.generate_diagrams_for_paper('paper', 'owner', first)
            old_path = first[0]['questions'][0]['diagram_path']
            await workers.generate_diagrams_for_paper('paper', 'owner', second)
        assert len(set(paths)) == 2
        assert first[0]['questions'][0]['diagram_path'] == old_path
    run(check())


def test_targeted_or_is_conservative_and_preserves_base():
    async def check():
        paper = make_paper()
        old = paper['sections'][0]['questions'][0]
        for extra in ({'format': 'mcq'}, {'parent_lead_in': 'Read this passage'}, {'part_label': '(i)'}, {'needs_diagram': True}):
            assert workers._independent_or_branches({**old, **extra}) is None
        with patch.object(workers, 'TARGETED_OR_ENABLED', True), \
             patch.object(workers, 'chat_complete', AsyncMock(return_value=reply('Explain why resistance increases with wire length.'))), \
             patch.object(workers, '_request_paper_json', AsyncMock(return_value={'valid': True})), \
             patch.object(workers, 'complete_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'repair_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'is_duplicate', AsyncMock(return_value=(False, None))):
            result = await workers.regenerate_question(paper, old['id'], required_internal_choice_count=1)
        assert result['question']['question'].startswith(old['question'] + '\nOR\n')
        assert result['question']['internal_choice_count'] == 1
    run(check())


def test_malformed_alternative_is_repairable_even_when_quota_matches():
    paper = paper_fixture(1, 1)
    q = {'id': 'q', 'allocation_id': 'a0', 'question': 'Explain algebraic identities in detail.\nOR\n', 'format': 'short_answer'}
    targets = workers.internal_choice_repair_targets([{'questions': [q]}], paper['generation_requirements'])
    assert targets == [{'question_id': 'q', 'required_internal_choice_count': 1}]


def test_official_isc_prompt_counts_and_or_quotas():
    book = {'id': 'book', 'topics': [{'name': name, 'subtopics': []} for name in ('Algebra', 'Vectors', 'Probability')]}
    bp = next(b for b in catalog() if b['id'] == 'isc12-mathematics')
    topics = topic_catalog(book)
    rows = [Allocation(section_id=s['id'], group_id=g['id'], bucket_id=b['id'], textbook_id='book',
                       topic_id=topics[i]['topic_id'], count=len(b['slots']))
            for i, s in enumerate(bp['sections']) for g in s['groups'] for b in allocation_buckets(g)]
    spec, errors = compile_allocations(bp, rows, [book], 'medium', 80)
    assert not errors
    assert (spec['total_questions'], spec['printed_question_count'], spec['attemptable_marks'], spec['total_marks']) == (41, 22, 80, 95)
    prompt = render_spec(spec)
    assert '41 scored part objects' in prompt and '22 printed questions' in prompt
    assert '95 marks' in prompt and 'attempt 80 marks' in prompt
    allocation_text = allocation_prompt(spec)
    payload = json.loads(allocation_text[allocation_text.index('{'):])
    assert sum(row.get('internal_choice_count', 0) for row in payload['allocations']) == 10
    assert 'optional Sections B/C' in allocation_text


def test_transient_provider_failure_does_not_end_completion_budget():
    async def check():
        paper = paper_fixture(1, 0)
        store = MemoryStore(paper)
        async def generate(slots, candidate, *args):
            if candidate['round'] == 0:
                raise RuntimeError('temporary provider failure')
            return materialize_normal_sections({'questions': [{'slot_id': 's0', 'question': 'Explain the distributive law with an example.'}]}, candidate['plan'])[0]
        with patch.object(engine, 'generate_batch', generate), patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
             patch.object(engine, 'is_duplicate', AsyncMock(return_value=(False, None))), patch.object(workers, 'generate_diagrams_for_paper', AsyncMock()):
            await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {}}, store=store)
        assert store.paper['completion']['ready']
        assert store.candidate['round'] == 2
    run(check())


def test_concurrent_duplicate_or_candidate_is_not_published_twice():
    async def check():
        paper = paper_fixture(2, 2)
        plan = engine.compile_plan(paper['generation_requirements'], paper)
        paper['sections'] = materialize_normal_sections({'questions': [
            {'slot_id': 's0', 'question': 'Explain the distributive law.'},
            {'slot_id': 's1', 'question': 'Factor the quadratic polynomial.'}]}, plan)[0]
        async def regenerate(working, qid, **kwargs):
            q = next(q for q in engine.questions(working['sections']) if q['id'] == qid)
            return {'question': {**q, 'question': 'Find the sum of two integers.\nOR\nFind the product of two integers.'}, 'old_text': q['question'], 'diagram_pending': False}
        with patch.object(workers, 'regenerate_question', regenerate):
            result = await workers.repair_internal_choices(paper)
        assert len(result['repairs']) == 1
        assert result['remaining']
    run(check())


def test_solution_worker_discards_changed_sources_and_keeps_unaffected_answers():
    async def check():
        paper = make_paper()
        paper['solution'] = {'status': 'generating', 'total': 2, 'completed': 0, 'sections': [{'answers': [
            {'question_id': 'q1', 'answer': ''}, {'question_id': 'q2', 'answer': ''}]}]}
        class Papers:
            async def find_one(self, *args, **kwargs):
                return copy.deepcopy(paper)
            async def update_one(self, query, update):
                if any(paper.get(key) != value for key, value in query.items()):
                    return type('Write', (), {'modified_count': 0})()
                paper.update(copy.deepcopy(update['$set']))
                return type('Write', (), {'modified_count': 1})()
        async def batches(subject, klass, payload, context, feedback, batch_size, answers, **kwargs):
            paper['sections'][0]['questions'][0]['question'] = 'Explain why electric power is dissipated in a resistor.'
            answers.update(q1='Stale answer for original Ohm question', q2='Current lens answer')
            await kwargs['on_batch']()
        with patch.object(workers, 'db', type('DB', (), {'papers': Papers()})()), \
             patch.object(workers, '_solution_context', AsyncMock(return_value=('', ''))), \
             patch.object(workers, '_run_solution_batches', batches), \
             patch.object(workers, 'repair_answers', AsyncMock(return_value={} )):
            await workers.generate_solution_background('paper-1', 'teacher')
        answers = paper['solution']['sections'][0]['answers']
        assert answers[0]['answer'] == ''
        assert answers[1]['answer'] == 'Current lens answer'
        assert paper['solution']['status'] == 'partial'
    run(check())


def test_ambiguous_saved_questions_do_not_starve_independent_recovery():
    async def check():
        paper = paper_fixture(2, 0)
        paper['sections'] = [{'title': 'Saved', 'questions': [{'id': 'unplaced', 'question': 'Preserve this teacher-authored question.'}]}]
        store = MemoryStore(paper)
        async def generate(slots, candidate, *args):
            return materialize_normal_sections({'questions': [{'slot_id': s['slot_id'], 'question': f"Explain arithmetic rule {s['slot_id']} with an example."} for s in slots]}, candidate['plan'])[0]
        with patch.object(engine, 'generate_batch', generate), patch.object(engine, 'check_topics', AsyncMock(return_value=set())), \
             patch.object(engine, 'is_duplicate', AsyncMock(return_value=(False, None))), patch.object(workers, 'generate_diagrams_for_paper', AsyncMock()):
            await engine.run_completion({'id': 'job', 'revision': 1, 'payload': {'continue_existing': True}}, store=store)
        assert len(engine.questions(store.candidate['sections'])) == 2
        assert store.candidate['unplaced'][0]['id'] == 'unplaced'
        assert store.paper['sections'] == paper['sections']
        assert store.paper['completion']['recovery_reason'] == 'ambiguous_placement'
        assert not store.paper['completion']['export_eligible']
    run(check())


def test_legacy_normalization_restores_missing_parent_topic_without_relabelling():
    from test_section_blueprints import fixture
    from section_blueprints import normalize_template_sections
    _, _, _, spec, sections = fixture()
    original = sections[0]['questions'][0]
    original['topic'] = 'Conflicting saved topic'
    original['important'] = True
    normalized, _ = normalize_template_sections(sections, spec)
    q = normalized[0]['questions'][0]
    assert q['parent_topic'] == spec['section_allocations'][0]['parent_topic']
    assert q['topic'] == 'Conflicting saved topic' and q['important']


def test_automatic_replacement_with_failed_figure_retains_published_question():
    async def check():
        paper = make_paper()
        original = paper['sections'][0]['questions'][1]
        original.update(needs_diagram=True, diagram_path='published/old.png')
        before = copy.deepcopy(paper)
        with patch.object(workers, 'chat_complete', AsyncMock(return_value=reply(
                'Explain refraction through the supplied prism.\nOR\nDescribe dispersion through the supplied prism.', needs_diagram=True))), \
             patch.object(workers, 'complete_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'repair_sections', AsyncMock(return_value={})), \
             patch.object(workers, 'apply_diagram_policy'), \
             patch.object(workers, 'generate_diagrams_for_paper', AsyncMock()), \
             patch.object(workers, 'is_duplicate', AsyncMock(return_value=(False, None))):
            try:
                await workers.regenerate_question(paper, 'q2', required_internal_choice_count=1)
            except workers.RegenerationFailed:
                pass
            else:
                raise AssertionError('A replacement without its required figure was accepted')
        assert paper == before
    run(check())
