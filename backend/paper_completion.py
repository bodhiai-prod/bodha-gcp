"""One bounded generate/validate/repair workflow for durable paper jobs.

The checkpoint is the input to recovery. Sections on the paper are only a
projection and are never used to resume an interrupted run.
"""
from __future__ import annotations
import asyncio
import copy
import json
from collections import OrderedDict
from datetime import datetime, timezone

from deps import utcnow_iso
from completion_store import CompletionStore, OwnershipLost
from review_budget import begin, clear
from generation_requirements import compile_requirements
from instruction_spec import parse_instructions, verify
from normal_paper_contract import compile_normal_plan, materialize_normal_sections, merge_normal_sections, validate_normal_plan
from section_blueprints import normalize_internal_choice_counts, internal_choice_misses, allocation_misses, printable_template_instructions
from question_repair import validate_question, alternative_issues
from latex_repair import repair_text
from question_dedupe import is_duplicate
from export_utils import canonical_question_type, printable_instructions
from case_study_contract import case_issues, parent_context, repair_slots

ENGINE_VERSION = 'v2'
MAX_ROUNDS = 2
CALL_LIMIT = 24
ACTIVE_SECONDS = 360


def questions(sections):
    return [q for s in sections for q in s.get('questions', [])]


def compile_plan(spec, paper):
    if not spec.get('section_allocations'):
        plan = copy.deepcopy(paper.get('normal_generation_plan')) if paper.get('normal_generation_plan') else compile_normal_plan(spec, paper.get('topics') or [], paper['total_marks'])
        for slot in plan['slots']:
            slot['id'] = slot['slot_id']
            slot['display_number'] = slot['order']
        return plan
    sections = [{'id': s['id'], 'title': s.get('title') or s['id'], 'order': i}
                for i, s in enumerate(spec['blueprint']['sections'])]
    slots = []
    blueprint_slot_order = {
        slot.get('id'): index
        for index, slot in enumerate(
            slot
            for section in spec['blueprint']['sections']
            for group in section.get('groups') or []
            for slot in group.get('slots') or []
        )
        if slot.get('id')
    }
    saved_questions = questions(paper.get('sections') or [])
    for row in spec['section_allocations']:
        for i in range(int(row.get('count') or 1)):
            sid = row.get('slot_id') or f"{row['allocation_id']}:{i + 1}"
            existing = next((q for q in saved_questions if q.get('slot_id') == sid), None)
            if existing is None:
                matches = [q for q in saved_questions if q.get('allocation_id') == row['allocation_id']]
                existing = matches[i] if i < len(matches) else {}
            # Blueprint rows usually leave cognitive type unconstrained. Do not
            # turn the generation default into a new restriction on saved work.
            qtype = row.get('type') or canonical_question_type(existing.get('type')) or 'concept'
            slots.append({**row, 'slot_id': sid, 'id': sid, 'topic': row['name'],
                          'type': qtype, 'order': len(slots),
                          'diagram_mode': 'required' if row.get('diagrams') else 'forbidden'})
    # Topic allocation rows are stored in the order teachers assign them, not
    # necessarily in printable question order. Compound parents must remain
    # contiguous or the renderer will print the same question number twice.
    section_order = {section['id']: index for index, section in enumerate(spec['blueprint']['sections'])}
    slots.sort(key=lambda slot: (
        section_order.get(slot.get('section_id'), 10_000),
        blueprint_slot_order.get(slot.get('slot_id'), 10_000),
    ))
    for index, slot in enumerate(slots):
        slot['order'] = index
    # Make each group's OR quota explicit in the first content request. The
    # provider cannot infer group quotas from isolated normal-paper slots.
    for section in spec['blueprint']['sections']:
        for group in section.get('groups') or []:
            if group.get('internal_choices') is None:
                continue
            eligible = [slot for slot in slots if slot['section_id'] == section['id']
                        and slot.get('group_id') == group['id']]
            if any('internal_choice_count' in slot for slot in eligible):
                for slot in eligible:
                    slot.setdefault('internal_choice_count', 0)
                continue
            for index, slot in enumerate(eligible):
                slot['internal_choice_count'] = int(index < int(group['internal_choices']))
    parents = {}
    for slot in slots:
        key = (slot['section_id'], slot.get('parent_id') or slot['slot_id'])
        parents.setdefault(key, len(parents) + 1)
        slot['display_number'] = parents[key]
    first_case_slots = set()
    for slot in slots:
        key = (slot['section_id'], slot.get('parent_id') or slot['slot_id'])
        if slot.get('case_study') and key not in first_case_slots:
            slot['requires_shared_stimulus'] = True
            first_case_slots.add(key)
    return {'slots': slots, 'sections': sections, 'contract_version': 2}


def adopt_existing(sections, plan):
    """Use exact saved identities; never infer placement from provider headings."""
    saved = copy.deepcopy(sections)
    expected_ids = {s['slot_id'] for s in plan['slots']}
    used = {q.get('slot_id') for q in questions(saved) if q.get('slot_id') in expected_ids}
    for q in questions(saved):
        if not q.get('slot_id'):
            matches = [s for s in plan['slots'] if s.get('allocation_id') == q.get('allocation_id')
                       and q.get('allocation_id') and s['slot_id'] not in used]
            if len(matches) == 1:
                q['slot_id'] = matches[0]['slot_id']
        used.add(q.get('slot_id'))
    merged = merge_normal_sections(plan, saved)
    originals = {}
    for q in questions(saved):
        originals.setdefault(q.get('slot_id'), q)
    for section in merged:
        for i, q in enumerate(section['questions']):
            original = originals[q['slot_id']]
            # Restore absent metadata only; conflicting saved content stays visible
            # to validation rather than being relabelled as compliant.
            section['questions'][i] = {**q, **{k: v for k, v in original.items() if v is not None}}
    return merged


def batches(slots, limit=20):
    """Keep parent questions together; independent batches run three at a time."""
    groups = OrderedDict()
    for slot in slots:
        key = (slot['section_id'], slot.get('parent_id') or slot['slot_id'])
        groups.setdefault(key, []).append(slot)
    result, batch = [], []
    for group in groups.values():
        if len(group) > limit:
            raise ValueError('A parent question exceeds the 20-part batch limit')
        if batch and len(batch) + len(group) > limit:
            result.append(batch)
            batch = []
        batch.extend(group)
    if batch:
        result.append(batch)
    return result


def content_issues(q):
    from section_blueprints import _OR_SEPARATOR_RE
    first_branch = _OR_SEPARATOR_RE.split(q.get('question') or '')[0].strip()
    issues = validate_question({**q, 'question': first_branch})
    for text in [q.get('question') or '', *(q.get('options') or [])]:
        _, found = repair_text(str(text))
        issues.extend(found)
    return sorted(set(issues))


def evaluate(candidate, paper):
    from workers import internal_choice_repair_targets
    sections, plan, spec = candidate['sections'], candidate['plan'], candidate['spec']
    expected = {s['slot_id']: s for s in plan['slots']}
    present = {q.get('slot_id'): q for q in questions(sections) if q.get('slot_id') in expected}
    unresolved = {s['slot_id'] for s in plan['slots'] if s['slot_id'] not in present}
    unresolved.update(q['slot_id'] for q in questions(sections) if q.get('slot_id') in expected and content_issues(q))
    choices = internal_choice_misses(sections, spec)
    pending_choices = set()
    try:
        by_id = {q['id']: q['slot_id'] for q in questions(sections) if q.get('slot_id') in expected}
        pending_choices = {by_id[t['question_id']] for t in internal_choice_repair_targets(sections, spec, allow_partial=True)}
    except Exception:
        # Missing base slots also account for their absent alternatives. For
        # groups with too few eligible slots, conservatively mark eligible ones.
        for miss in choices:
            ids = set(miss.get('eligible_question_ids') or [])
            pending_choices.update(q['slot_id'] for q in questions(sections) if q['id'] in ids and q.get('slot_id') in expected)
    unresolved.update(pending_choices)
    for q in questions(sections):
        slot = expected.get(q.get('slot_id'))
        if not slot:
            continue
        if (slot.get('diagram_mode') == 'required' or q.get('needs_diagram')) and not q.get('diagram_path'):
            unresolved.add(q['slot_id'])
    structural = validate_normal_plan(sections, plan)
    unresolved.update(issue['slot_id'] for issue in structural if issue.get('slot_id') in present)
    gates = list(verify(sections, spec).get('misses') or [])
    gates.extend(allocation_misses(sections, spec))
    gates.extend(str(issue) for issue in structural)
    for issue in case_issues(sections, plan):
        unresolved.update(issue['slot_ids'])
        gates.append(issue['message'])
    if candidate.get('unplaced') or any(q.get('slot_id') not in expected for q in questions(sections)):
        gates.append('Saved questions need explicit slot placement before completion can continue.')
    if choices:
        gates.append('Required internal OR alternatives are incomplete.')
    if unresolved:
        gates.append(f'{len(unresolved)} scored parts remain incomplete.')
    total = len(plan['slots'])
    valid = total - len(unresolved)
    ready = not gates and not unresolved
    visible = copy.deepcopy(sections)
    for section in visible:
        section['questions'] = [q for q in section['questions'] if not content_issues(q)]
        for q in section['questions']:
            q.pop('completion_pending', None)
            if q.get('slot_id') in pending_choices:
                q['completion_pending'] = 'Internal OR alternative pending'
            elif q.get('needs_diagram') and not q.get('diagram_path'):
                q['completion_pending'] = 'Figure pending'
    # Alternatives and figures affect completion, not whether a safe stem can be read.
    viewable_count = len(questions(visible))
    preview = bool(total and viewable_count / total >= .85)
    return {'ready': ready, 'preview_available': preview, 'viewable_scored_parts': viewable_count, 'valid_scored_parts': valid,
            'actual_scored_parts': len(present), 'target_scored_parts': total,
            'pending_alternatives': sum(max(0, int(m['expected']) - int(m['actual'])) for m in choices) + sum(bool(alternative_issues(q)) for q in questions(sections)),
            'unresolved_count': len(unresolved), 'unresolved_slot_ids': sorted(unresolved),
            'failed_gates': list(dict.fromkeys(gates)), 'visible': visible}


async def generate_batch(slots, candidate, paper, payload, ref):
    from workers import _request_paper_json
    from prompts import normal_slots_prompt
    prompt = normal_slots_prompt(subject=paper.get('subject', ''), klass=paper.get('class_name', ''),
        slots=slots, context_excerpt=payload.get('context_excerpt', ''),
        custom_instructions=paper.get('custom_instructions') or '',
        feedback_hints=payload.get('feedback_hints', ''),
        existing_questions=[q.get('question', '') for q in questions(candidate['sections'])]
            + [r['question'].get('question', '') for r in candidate['rejected']])
    # Template parent structure belongs to the server, but content must be
    # written in the context of the whole printed parent question.
    prompt += '\nParent context: ' + json.dumps([{k: s.get(k) for k in
        ('slot_id', 'parent_label', 'part_label', 'parent_lead_in')} for s in slots])
    retained_parent_context = parent_context(candidate['sections'], candidate['plan'], slots)
    if retained_parent_context:
        prompt += '\nAccepted case context (do not change it while repairing a child): ' + json.dumps(retained_parent_context, ensure_ascii=False)
    if paper.get('is_competitive'):
        from prompts import competitive_system_for_exam
        from llm_adapter import chain_for_exam
        from llm_adapter import chat_complete, parse_json_response
        prompt += '\nExam calibration examples (never copy): ' + json.dumps(candidate.get('anchors') or [], default=str)
        raw = await chat_complete(system_message=competitive_system_for_exam(candidate.get('exam_type', 'GENERIC')),
                                  user_text=prompt, provider_chain=chain_for_exam(candidate.get('exam_type', 'GENERIC')))
        data = parse_json_response(raw)
    else:
        data = await _request_paper_json(system_message='Write original exam question content for the supplied slots. Return JSON.',
                                         user_text=prompt, ref=ref, attempts=1)
    sections, _, _ = materialize_normal_sections(data, candidate['plan'], only_slots=slots)
    return sections


async def check_topics(sections, paper, payload, ref):
    """Check the actual stem against the assigned scope, not provider metadata."""
    from workers import _request_paper_json
    if not questions(sections):
        return set()
    data = await _request_paper_json(system_message='Review exam questions. Return strict JSON.',
        user_text='Review EVERY OR branch independently. Identify questions outside their assigned topic, marks or difficulty, with duplicate alternatives, an incorrect MCQ answer, or without enough information to answer. '
        'For a case-study parent, also verify that every sibling subpart depends on its one shared_stimulus and that the stimulus and subparts form one coherent case. If not, mark every slot in that parent invalid. '
        'Return {"invalid_slot_ids": [...], "checked_slot_ids": [...]} listing EVERY reviewed slot in checked_slot_ids. '
        'Do not flag a valid question merely because wording differs from the textbook.\n'
        + json.dumps(questions(sections), ensure_ascii=False)
        + '\nScope context: ' + payload.get('context_excerpt', ''), ref=ref, attempts=1)
    checked = set(data.get('checked_slot_ids') or [])
    return set(data.get('invalid_slot_ids') or []) | {q['slot_id'] for q in questions(sections) if q['slot_id'] not in checked}


async def run_completion(job, *, store=None):
    """Initial generation plus two targeted rounds under one persisted budget."""
    from workers import internal_choice_repair_targets, regenerate_question, generate_diagrams_for_paper
    from token_usage import tag
    tag('paper', job.get('paper_id', ''))
    store = store or CompletionStore(job)
    paper, candidate = await store.load()
    payload = job.get('payload') or {}
    lock = asyncio.Lock()
    commit_lock = asyncio.Lock()
    budget_data = candidate.get('budget') or {}
    elapsed = float(budget_data.get('elapsed_seconds') or 0)
    if budget_data.get('checkpoint_at') and candidate.get('phase') not in ('ready', 'incomplete', 'queued', 'edited'):
        from paper_review_queue import LEASE_SECONDS
        elapsed += min(LEASE_SECONDS, max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(budget_data['checkpoint_at'])).total_seconds()))
    budget = begin(seconds=max(0, ACTIVE_SECONDS - elapsed), call_limit=CALL_LIMIT)
    budget.calls_used = int(budget_data.get('provider_calls_used') or 0)
    budget.elapsed_before = elapsed
    # A fresh user continuation has a new job/revision and allowance; a lease
    # takeover of the same job retains calls, active time and round cursor.
    if not candidate:
        candidate.update({'sections': [], 'rejected': [], 'round': 0, 'phase': 'resolve',
                          'job_id': job['id'], 'preserve_publication': bool((paper.get('completion') or {}).get('ready')), 'first_pass_valid_count': None, 'repair_count': 0})

    async def checkpoint(*, finished=False):
        async with commit_lock:
            await write_checkpoint(finished=finished)

    async def write_checkpoint(*, finished=False):
        candidate['budget'] = {**budget.snapshot(), 'checkpoint_at': utcnow_iso()}
        fields = {'completion_engine': ENGINE_VERSION}
        if finished and 'plan' not in candidate:
            fields.update({'generation_status': 'needs_review', 'quality_review': {'status': 'needs_review', 'message': candidate.get('last_error')},
                           'completion': {'status': 'incomplete', 'ready': False, 'export_eligible': False, 'action_label': 'Retry completion'}})
        if 'plan' in candidate:
            result = evaluate(candidate, paper)
            visible = result.pop('visible')
            candidate['unresolved_issues'] = result['failed_gates']
            result['status'] = 'ready' if result['ready'] else ('incomplete' if finished else 'running')
            result['recovery_reason'] = 'ambiguous_placement' if candidate.get('unplaced') else 'automatic_recovery'
            result['action_label'] = 'Review question placement' if candidate.get('unplaced') else 'Finish remaining items'
            stage_index = 0 if candidate['phase'] in ('resolve', 'queued') else 1 if candidate['phase'] == 'generating' and candidate['round'] == 0 else 2
            candidate['presentation_stage'] = max(candidate.get('presentation_stage', 0), stage_index)
            result['presentation_stage'] = candidate['presentation_stage']
            result['phase'] = candidate['phase']
            result['revision'] = job['revision']
            parents = {(q['section_id'], q.get('parent_id') or q['slot_id']) for q in questions(candidate['sections'])}
            result['actual_printed_questions'] = len(parents)
            result['target_printed_questions'] = candidate['spec'].get('printed_question_count') or len(candidate['plan']['slots'])
            result['export_eligible'] = result['ready']
            result['budget'] = candidate['budget']
            result['first_pass_valid_count'] = candidate.get('first_pass_valid_count')
            result['repair_count'] = candidate['repair_count']
            # A previous completed revision remains visible during repair.
            previous_ready = candidate.get('preserve_publication')
            publish = not candidate.get('unplaced') and (result['ready'] or (result['preview_available'] and not previous_ready))
            status = 'ready' if result['ready'] or (publish and not finished) else ('needs_review' if finished else 'generating')
            if previous_ready and not result['ready']:
                status = 'ready'
            fields.update({'completion': result, 'generation_status': status,
                'generation_error': None, 'generation_requirements': candidate['spec'],
                'normal_generation_plan': candidate['plan'] if not candidate['spec'].get('section_allocations') else None,
                'target_question_count': result['target_scored_parts'],
                'quality_review': {'status': 'passed' if result['ready'] else ('needs_review' if finished else 'running'),
                    'stage': candidate['phase'], 'failed_gates': result['failed_gates'],
                    'budget': candidate['budget'], 'message': '; '.join(result['failed_gates'])[:500]},
                'generation_progress': {'completed': result['valid_scored_parts'], 'total': result['target_scored_parts'],
                    'status': status, 'phase': candidate['phase'], 'updated_at': utcnow_iso()}})
            if publish:
                fields.update({'sections': visible, 'question_count': len(questions(visible)),
                               'instructions': printable_instructions(paper.get('instructions', ''),
                                   instructions_edited=bool(paper.get('instructions_edited')),
                                   template_instructions=printable_template_instructions({'generation_requirements': candidate['spec']}))})
        await store.save(copy.deepcopy(candidate), fields)

    async def persist_attempt():
        async with lock:
            await checkpoint()
    budget.persist = persist_attempt
    async def persist_active_time():
        while True:
            await asyncio.sleep(15)
            await persist_attempt()
    ticker = asyncio.create_task(persist_active_time())
    try:
        if 'plan' not in candidate:
            spec = candidate.get('spec') or paper.get('generation_requirements')
            if not spec:
                instruction_spec = await parse_instructions(paper.get('custom_instructions') or '', ref=paper['id'])
                spec = compile_requirements(total_marks=paper['total_marks'],
                    distribution=paper.get('distribution') or {}, format_distribution=paper.get('format_distribution') or {},
                    topics=paper.get('topics') or [], difficulty=paper.get('difficulty', 'medium'),
                    total_questions=paper.get('total_questions') or paper.get('target_question_count'),
                    format_counts=paper.get('format_counts'), diagram_count=paper.get('diagram_count'),
                    instruction_spec=instruction_spec, has_blueprint=False)
            candidate['spec'] = spec
            await checkpoint()
            candidate['plan'] = compile_plan(spec, paper)
            if not spec.get('section_allocations'):
                spec['topic_counts'] = candidate['plan'].get('topic_counts') or spec.get('topic_counts') or {}
            # Manual continuation is explicit; snapshot accepted teacher content.
            if payload.get('continue_existing'):
                candidate['sections'] = adopt_existing(paper.get('sections') or [], candidate['plan'])
                accepted_ids = {q['id'] for q in questions(candidate['sections'])}
                unplaced = [q for q in questions(paper.get('sections') or []) if q.get('id') not in accepted_ids]
                if unplaced:
                    candidate['unplaced'] = copy.deepcopy(unplaced)
                    await checkpoint()
                    candidate['last_error'] = 'Saved questions need review to identify their placement.'
            await checkpoint()
        replacement = payload.get('replace_question_id')
        if replacement and not candidate.get('replacement_requested'):
            for section in candidate['sections']:
                for q in section['questions']:
                    if q['id'] == replacement:
                        candidate['rejected'].append({'question': q, 'issues': ['teacher_requested_replacement']})
                section['questions'] = [q for q in section['questions'] if q['id'] != replacement]
            candidate['replacement_requested'] = replacement
            await checkpoint()
        if paper.get('is_competitive') and 'anchors' not in candidate:
            from competitive_routes import _retrieve_anchors
            from deps import db
            exam = await db.competitive_exams.find_one({'id': paper['competitive_exam_id']}, {'_id': 0})
            if not exam:
                raise ValueError('Competitive exam no longer exists')
            candidate['exam_type'] = (exam.get('exam_type') or 'GENERIC').upper()
            candidate['anchors'] = await _retrieve_anchors(exam, [t['name'] for t in paper.get('topics') or []])
            await checkpoint()
        semaphore = asyncio.Semaphore(3)
        while candidate['round'] <= MAX_ROUNDS:
            round_index = candidate['round']
            candidate['phase'] = 'generating' if round_index == 0 else 'repairing'
            candidate['sections'] = adopt_existing(candidate['sections'], candidate['plan'])
            structural_issues = validate_normal_plan(candidate['sections'], candidate['plan'])
            replacement_slots = {issue['slot_id'] for issue in structural_issues if issue.get('slot_id') and issue['code'] in ('slot_metadata', 'slot_identity', 'slot_diagram', 'missing_case_stimulus')}
            candidate['repair_issues'] = structural_issues
            for section in candidate['sections']:
                bad = [q for q in section['questions'] if content_issues(q)]
                candidate['rejected'].extend({'question': q, 'issues': content_issues(q)} for q in bad)
                section['questions'] = [q for q in section['questions'] if not content_issues(q)]
            accepted = {q['slot_id'] for q in questions(candidate['sections']) if not content_issues(q)}
            missing = [s for s in candidate['plan']['slots'] if s['slot_id'] not in accepted or s['slot_id'] in replacement_slots]
            missing = repair_slots(missing, candidate['sections'], candidate['plan'])

            async def fill(index, slots):
                async with semaphore:
                    key = ','.join(s['slot_id'] for s in slots)
                    try:
                        generated = candidate.get('pending_batches', {}).get(key) or await generate_batch(slots, candidate, paper, payload,
                                                         f"{paper['id']}:{round_index}:{index}")
                        async with lock:
                            candidate.setdefault('pending_batches', {})[key] = generated
                            await checkpoint()
                        proposed = adopt_existing([*candidate['sections'], *generated], candidate['plan'])
                        invalid_topics = await check_topics(proposed, paper, payload, f"{paper['id']}:scope:{round_index}:{index}")
                        replacements_with_figures = [q for q in questions(generated)
                            if q['slot_id'] in replacement_slots and q.get('needs_diagram')
                            and q['slot_id'] not in invalid_topics and not content_issues(q)]
                        if replacements_with_figures:
                            await generate_diagrams_for_paper(paper['id'], paper['owner_id'], [{'questions': replacements_with_figures}])
                        async with lock:
                            for section in generated:
                                for q in section.get('questions') or []:
                                    if q['slot_id'] in {old['slot_id'] for old in questions(candidate['sections'])} and q['slot_id'] not in replacement_slots:
                                        continue
                                    issues = content_issues(q)
                                    if q['slot_id'] in replacement_slots and q.get('needs_diagram') and not q.get('diagram_path'):
                                        issues.append('replacement_figure_pending')
                                    if q['slot_id'] in invalid_topics:
                                        issues.append('topic_or_answer')
                                    others = [old for old in questions(candidate['sections']) if old['slot_id'] != q['slot_id']]
                                    if (await is_duplicate(q, others, use_llm=False))[0]:
                                        issues.append('duplicate')
                                    if issues:
                                        candidate['rejected'].append({'question': q, 'issues': issues, 'round': round_index})
                                        continue
                                    if q['slot_id'] in replacement_slots:
                                        for old_section in candidate['sections']:
                                            for old in old_section['questions']:
                                                if old['slot_id'] == q['slot_id']:
                                                    q.update({key: copy.deepcopy(old[key]) for key in ('id', 'important', 'regen_history') if key in old})
                                            old_section['questions'] = [old for old in old_section['questions'] if old['slot_id'] != q['slot_id']]
                                        replacement_slots.discard(q['slot_id'])
                                    candidate['sections'] = adopt_existing(
                                        [*candidate['sections'], {**section, 'questions': [q]}], candidate['plan'])
                                    if round_index:
                                        candidate['repair_count'] += 1
                            candidate.get('pending_batches', {}).pop(key, None)
                            await checkpoint()
                    except OwnershipLost:
                        raise
                    except Exception as exc:
                        async with lock:
                            candidate['last_error'] = str(exc)[:500]
                            await checkpoint()

            pending = list((candidate.get('pending_batches') or {}).keys())
            for index, key in enumerate(pending):
                ids = set(key.split(','))
                await fill(f'resume-{index}', [s for s in candidate['plan']['slots'] if s['slot_id'] in ids])
            accepted = {q['slot_id'] for q in questions(candidate['sections'])}
            missing = [s for s in candidate['plan']['slots'] if s['slot_id'] not in accepted or s['slot_id'] in replacement_slots]
            missing = repair_slots(missing, candidate['sections'], candidate['plan'])
            await asyncio.gather(*(fill(i, batch) for i, batch in enumerate(batches(missing))))
            if round_index == 0 and candidate['first_pass_valid_count'] is None:
                candidate['first_pass_valid_count'] = len(questions(candidate['sections']))
            # Repair available groups even when another base slot is missing.
            # Persist each accepted alternative independently.
            if questions(candidate['sections']):
                candidate['phase'] = 'alternatives'
                normalize_internal_choice_counts(candidate['sections'])
                targets = internal_choice_repair_targets(candidate['sections'], candidate['spec'], allow_partial=True)
                async def repair_choice(target):
                    async with semaphore:
                        try:
                            working = {**paper, 'generation_requirements': candidate['spec'],
                                       'sections': copy.deepcopy(candidate['sections'])}
                            result = await regenerate_question(working, target['question_id'],
                                context_excerpt=payload.get('context_excerpt', ''),
                                feedback_hints=payload.get('feedback_hints', ''),
                                required_internal_choice_count=target['required_internal_choice_count'])
                            invalid = await check_topics([{'questions': [result['question']]}], paper, payload,
                                                         f"{paper['id']}:alternative-scope:{target['question_id']}")
                            if invalid or content_issues(result['question']) or alternative_issues(result['question']):
                                async with lock:
                                    candidate['rejected'].append({'question': result['question'], 'issues': ['invalid_alternative']})
                                    await checkpoint()
                                return
                            async with lock:
                                current_targets = internal_choice_repair_targets(candidate['sections'], candidate['spec'], allow_partial=True)
                                if target not in current_targets:
                                    return
                                others = [q for q in questions(candidate['sections']) if q['id'] != target['question_id']]
                                if (await is_duplicate(result['question'], others, use_llm=False))[0]:
                                    return
                                for section in candidate['sections']:
                                    for i, q in enumerate(section['questions']):
                                        if q['id'] == target['question_id']:
                                            section['questions'][i] = result['question']
                                candidate['repair_count'] += 1
                                normalize_internal_choice_counts(candidate['sections'])
                                await checkpoint()
                        except OwnershipLost:
                            raise
                        except Exception as exc:
                            candidate['last_error'] = str(exc)[:500]
                await asyncio.gather(*(repair_choice(t) for t in targets))
            candidate['phase'] = 'figures'
            required = {s['slot_id'] for s in candidate['plan']['slots'] if s.get('diagram_mode') == 'required'}
            figure_sections = [{**section, 'questions': [q for q in section['questions']
                                if (q['slot_id'] in required or q.get('needs_diagram')) and not q.get('diagram_path')]}
                               for section in candidate['sections']]
            try:
                await generate_diagrams_for_paper(paper['id'], paper['owner_id'], figure_sections)
            except OwnershipLost:
                raise
            except Exception as exc:
                candidate['last_error'] = str(exc)[:500]
            candidate['round'] += 1
            await checkpoint()
            if evaluate(candidate, paper)['ready'] or budget.remaining_seconds() <= 0 or budget.calls_used >= CALL_LIMIT:
                break
        candidate['phase'] = 'ready' if evaluate(candidate, paper)['ready'] else 'incomplete'
        await checkpoint(finished=True)
    except OwnershipLost:
        raise
    except Exception as exc:
        candidate['last_error'] = str(exc)[:500]
        candidate['phase'] = 'incomplete'
        await checkpoint(finished=True)
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass
        clear()
