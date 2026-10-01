"""Atomic candidate checkpoint and visible projection, fenced by job lease."""
from __future__ import annotations
import json
import asyncio
import random
from google.cloud import firestore
from deps import db, utcnow_iso
from firestore_compat import _decode_generation_requirements, _encode_generation_requirements


async def _commit_with_retry(operation):
    # Firestore's transaction wrapper retries optimistic conflicts immediately.
    # Add jitter between exhausted retry groups to avoid synchronized writers
    # repeatedly acquiring the same shared read locks in the emulator.
    from google.api_core.exceptions import Aborted
    for attempt in range(4):
        try:
            return await operation(db._client.transaction())
        except (ValueError, Aborted) as exc:
            if not isinstance(exc, Aborted) and not isinstance(exc.__cause__, Aborted):
                raise
            if attempt == 3:
                raise
            await asyncio.sleep(random.uniform(.05, .25) * (2 ** attempt))


class OwnershipLost(RuntimeError):
    pass


class CompletionStore:
    def __init__(self, job):
        self.job = job
        self.paper_ref = None
        self.job_ref = None
        self.checkpoint_ref = db._client.collection('paper_completion_checkpoints').document(
            f"{job['paper_id']}:{job['revision']}"
        )

    async def load(self):
        papers = await db.papers._matching_snapshots({'id': self.job['paper_id']})
        jobs = await db.paper_review_jobs._matching_snapshots({'id': self.job['id']})
        if not papers or not jobs:
            raise OwnershipLost('Paper or job no longer exists')
        self.paper_ref, self.job_ref = papers[0][0].reference, jobs[0][0].reference
        checkpoint = await self.checkpoint_ref.get()
        return papers[0][1], json.loads((checkpoint.to_dict() or {}).get('candidate_json', '{}'))

    async def save(self, candidate, fields):
        @firestore.async_transactional
        async def publish(txn):
            ps = await self.paper_ref.get(transaction=txn)
            js = await self.job_ref.get(transaction=txn)
            paper = _decode_generation_requirements(ps.to_dict() or {})
            job = js.to_dict() or {}
            if (not ps.exists or paper.get('is_deleted') or paper.get('generation_status') == 'cancelled'
                or int(paper.get('generation_revision') or 1) != int(self.job['revision'])
                or job.get('status') != 'leased' or job.get('lease_token') != self.job.get('lease_token')
                or job.get('worker_id') != self.job.get('worker_id')
                or (job.get('lease_until') or '') <= utcnow_iso()):
                raise OwnershipLost('Completion lease expired, cancelled, or superseded')
            # Read both ownership documents before writing either projection.
            from question_revision import invalidate_changed_solution
            if 'sections' in fields:
                solution = invalidate_changed_solution(paper, fields['sections'])
                if solution is not None:
                    paper['solution'] = solution
            paper.update(fields)
            txn.set(self.checkpoint_ref, {
                'paper_id': self.job['paper_id'], 'revision': self.job['revision'],
                'updated_at': utcnow_iso(), 'candidate_json': json.dumps(candidate, ensure_ascii=False),
            })
            txn.set(self.paper_ref, _encode_generation_requirements(paper))
        await _commit_with_retry(publish)


async def enqueue_continuation(paper, payload):
    """Atomically snapshot the current revision and enqueue one continuation.

    Repeated concurrent clicks return the already queued revision. A teacher
    edit advances the revision, so its visible content takes precedence over
    an older checkpoint.
    """
    from paper_review_queue import generation_job
    matches = await db.papers._matching_snapshots({'id': paper['id']})
    if not matches:
        raise OwnershipLost('Paper no longer exists')
    paper_ref = matches[0][0].reference
    @firestore.async_transactional
    async def enqueue(txn):
        snap = await paper_ref.get(transaction=txn)
        current = _decode_generation_requirements(snap.to_dict() or {})
        if (current.get('completion') or {}).get('status') in ('queued', 'running') and current.get('generation_status') != 'cancelled':
            return {'status': 'queued', 'revision': current.get('generation_revision', 1)}
        old_revision = int(current.get('generation_revision') or 1)
        revision = old_revision + 1
        old_ref = db._client.collection('paper_completion_checkpoints').document(f"{paper['id']}:{old_revision}")
        old = await old_ref.get(transaction=txn)
        candidate = json.loads((old.to_dict() or {}).get('candidate_json', '{}'))
        if candidate:
            candidate.update({'budget': {}, 'round': 0, 'phase': 'queued', 'repair_count': 0,
                              'preserve_publication': bool((current.get('completion') or {}).get('ready'))})
            candidate.pop('replacement_requested', None)
        job = generation_job(paper_id=paper['id'], revision=revision, payload={**payload, 'continue_existing': True})
        job['engine_version'] = 'v2'
        job['max_attempts'] = 3
        current.update({'generation_revision': revision, 'completion_engine': 'v2',
                        'generation_status': 'ready' if (current.get('completion') or {}).get('ready') else 'queued',
                        'completion': {**(current.get('completion') or {}), 'status': 'queued'},
                        'quality_review': {'status': 'running', 'stage': 'queued'},
                        'topup': None, 'generation_recovery': None, 'regen': None,
                        'export_warning_acknowledgement': None})
        txn.set(paper_ref, _encode_generation_requirements(current))
        txn.set(db._client.collection('paper_review_jobs').document(job['id']), job)
        if candidate:
            txn.set(db._client.collection('paper_completion_checkpoints').document(f"{paper['id']}:{revision}"),
                    {'paper_id': paper['id'], 'revision': revision, 'candidate_json': json.dumps(candidate)})
        return {'status': 'queued', 'revision': revision}
    return await _commit_with_retry(enqueue)


async def save_teacher_edit(paper, fields):
    """Advance revision and carry hidden accepted work alongside teacher edits."""
    import copy
    matches = await db.papers._matching_snapshots({'id': paper['id']})
    if not matches:
        raise OwnershipLost('Paper no longer exists')
    reference = matches[0][0].reference
    @firestore.async_transactional
    async def edit(txn):
        snapshot = await reference.get(transaction=txn)
        current = _decode_generation_requirements(snapshot.to_dict() or {})
        revision = int(current.get('generation_revision') or 1)
        if revision != int(paper.get('generation_revision') or 1) or (current.get('quality_review') or {}).get('status') in ('queued', 'running'):
            raise OwnershipLost('Completion owns this paper or the paper changed while editing')
        old_ref = db._client.collection('paper_completion_checkpoints').document(f"{paper['id']}:{revision}")
        old = await old_ref.get(transaction=txn)
        candidate = json.loads((old.to_dict() or {}).get('candidate_json', '{}'))
        if candidate and 'sections' in fields:
            from paper_completion import questions, adopt_existing
            visible_ids = {q.get('slot_id') for q in questions(current.get('sections') or [])}
            hidden = [{**section, 'questions': [q for q in section['questions'] if q.get('slot_id') not in visible_ids]}
                      for section in candidate['sections']]
            candidate['sections'] = adopt_existing([*copy.deepcopy(fields['sections']), *hidden], candidate['plan'])
            placed_ids = {q.get('id') for q in questions(candidate['sections'])}
            candidate['unplaced'] = [copy.deepcopy(q) for q in questions(fields['sections']) if q.get('id') not in placed_ids]
            candidate['preserve_publication'] = False
            candidate['phase'] = 'edited'
            candidate['budget'] = {}
            candidate['round'] = 0
        from firestore_compat import _set
        for key, value in fields.items():
            _set(current, key, value)
        current.update({'generation_revision': revision + 1, 'completion': None,
                        'export_warning_acknowledgement': None})
        if candidate:
            from paper_completion import evaluate
            result = evaluate({**candidate, 'sections': current.get('sections') or []}, current)
            result.pop('visible', None)
            result.update({'status': 'ready' if result['ready'] else 'incomplete', 'export_eligible': result['ready'],
                           'revision': revision + 1, 'action_label': 'Review question placement' if candidate.get('unplaced') else 'Finish remaining items',
                           'recovery_reason': 'ambiguous_placement' if candidate.get('unplaced') else 'automatic_recovery'})
            current['completion'] = result
            current['generation_status'] = 'ready' if result['ready'] else 'needs_review'
        txn.set(reference, _encode_generation_requirements(current))
        if candidate:
            txn.set(db._client.collection('paper_completion_checkpoints').document(f"{paper['id']}:{revision + 1}"),
                    {'paper_id': paper['id'], 'revision': revision + 1, 'candidate_json': json.dumps(candidate)})
    await _commit_with_retry(edit)
