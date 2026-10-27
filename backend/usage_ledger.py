"""Durable operation accounting. No provider payloads or exception text is stored."""
import asyncio
import contextvars
import functools
import inspect
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone

log = logging.getLogger(__name__)
current = contextvars.ContextVar('usage_operation', default=None)
call_context = contextvars.ContextVar('usage_call_context', default={})


@contextmanager
def call_attempt(**fields):
    token = call_context.set({**call_context.get(), **fields})
    try:
        yield
    finally:
        call_context.reset(token)


@asynccontextmanager
async def stage(name):
    context = current.get()
    if context is None:
        yield
        return
    started = now()
    clock = time.monotonic()
    status = 'completed'
    try:
        yield
    except BaseException:
        status = 'failed'
        raise
    finally:
        await safe_write('usage_stages', {'id': str(uuid.uuid4()), 'operation_id': context['id'],
            'name': name, 'started_at': started, 'finished_at': now(),
            'duration_ms': round((time.monotonic() - clock) * 1000), 'status': status})


def now():
    return datetime.now(timezone.utc).isoformat()


def price(model, inputs, outputs, images=0):
    """Rates are explicitly configured, never guessed. Snapshot on every call."""
    try:
        rates = json.loads(os.environ.get('USAGE_MODEL_PRICING_JSON', '{}'))
        rate = rates.get(model)
        fx = float(os.environ.get('USAGE_USD_INR', '0'))
        from usage_rates import public_rates
        snapshot = public_rates() if not rate or fx <= 0 else {}
        rate = rate or snapshot.get('rates', {}).get(model)
        if model == 'gcp/vision-document-text' and not rate:
            rate = {'per_image': 0.0015, 'source': 'https://cloud.google.com/vision/pricing',
                    'note': 'List price estimate; monthly free tier and volume discounts excluded'}
        fx = fx or snapshot.get('fx', 0)
        if not isinstance(rate, dict) or fx <= 0 or (inputs and 'input_per_million' not in rate) or (outputs and 'output_per_million' not in rate) or (images and not outputs and 'per_image' not in rate):
            return {'cost_inr': None, 'provenance': 'unknown'}
        usd = (inputs * float(rate.get('input_per_million', 0)) + outputs * float(rate.get('output_per_million', 0))) / 1_000_000 + images * float(rate.get('per_image', 0)) + float(rate.get('per_request', 0))
        return {'cost_inr': usd * fx, 'cost_usd': usd, 'fx_usd_inr': fx,
                'pricing': rate, 'fx_date': snapshot.get('fx_date'), 'fx_source': snapshot.get('fx_source'), 'provenance': 'estimated'}
    except (ValueError, TypeError):
        return {'cost_inr': None, 'provenance': 'unknown'}


async def safe_write(collection, row):
    try:
        from deps import db
        await getattr(db, collection).insert_one(row)
    except Exception:
        log.exception('Usage ledger persistence failed')


def record_call(row):
    context = current.get()
    if not context:
        return
    event = dict(row, **call_context.get(), id=str(uuid.uuid4()), operation_id=context['id'],
                 resource_id=context['resource_id'], timestamp=now())
    async def persist():
        event.update(await asyncio.to_thread(price, row['model_id'], row.get('input_tokens', 0), row.get('output_tokens', 0), row.get('images', 0)) if row.get('outcome') != 'failed' and not row.get('failed_requests') else {'cost_inr': None, 'provenance': 'unknown'})
        reported = row.get('reported_cost_usd')
        if reported is not None and event.get('fx_usd_inr'):
            event.update(cost_usd=float(reported), cost_inr=float(reported) * event['fx_usd_inr'], provenance='provider_reported')
        await safe_write('usage_calls', event)
    # The sink may run on a provider thread. Keep persistence on the owning loop.
    future = asyncio.run_coroutine_threadsafe(persist(), context['loop'])
    context['writes'].append(future)


def tracked(kind, resource_arg='paper_id'):
    def decorate(fn):
        signature = inspect.signature(fn)
        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            job = bound.get('job') or {}
            resource = bound.get(resource_arg) or job.get('paper_id') or (bound.get('paper') or {}).get('id') or (bound.get('tb') or {}).get('id')
            if not resource:
                return await fn(*args, **kwargs)
            parent = current.get()
            operation = {'id': str(uuid.uuid4()), 'resource_id': resource,
                         'resource_type': 'book' if kind in ('ingestion', 'book_indexing') else 'paper',
                         'kind': kind, 'user_id': bound.get('user_id') or bound.get('owner_id') or job.get('user_id') or (bound.get('tb') or {}).get('owner_id'),
                         'parent_id': parent['id'] if parent else None, 'started_at': now(), 'status': 'running'}
            if job:
                operation.update(job_id=job.get('id'), revision=job.get('revision'),
                                 attempt=job.get('attempt'), queued_at=job.get('queued_at'),
                                 lease_started_at=job.get('started_at'), retry_reason_available=bool(job.get('diagnostics')))
                try:
                    queued = datetime.fromisoformat(job['queued_at'].replace('Z', '+00:00'))
                    leased = datetime.fromisoformat(job['started_at'].replace('Z', '+00:00'))
                    operation['queue_wait_ms'] = max(0, round((leased - queued).total_seconds() * 1000))
                except (KeyError, ValueError, TypeError):
                    pass
            await safe_write('usage_operations', operation)
            context = dict(operation, loop=asyncio.get_running_loop(), writes=[])
            token = current.set(context)
            started = time.monotonic()
            status, error = 'completed', None
            try:
                result = await fn(*args, **kwargs)
                # Background workers commonly persist failures instead of raising.
                try:
                    from deps import db
                    collection = db.textbooks if kind in ('ingestion', 'book_indexing') else db.papers
                    resource_doc = await collection.find_one({'id': resource}) or {}
                    if kind == 'book_indexing':
                        state = (resource_doc.get('topic_extraction') or {}).get('status')
                    elif kind == 'paper_job' and job.get('id'):
                        job_doc = await db.paper_review_jobs.find_one({'id': job['id']}) or {}
                        state = job_doc.get('status')
                        if state in ('queued', 'leased'):
                            state = 'retrying' if state == 'queued' else 'unknown'
                    elif kind == 'solution':
                        state = (resource_doc.get('solution') or {}).get('status')
                    elif kind == 'regeneration':
                        state = (resource_doc.get('regen') or {}).get('status') or resource_doc.get('generation_status')
                    elif kind in ('template_recovery', 'choice_repair'):
                        state = (resource_doc.get('generation_recovery') or {}).get('status') or resource_doc.get('generation_status')
                    elif kind == 'completion' and not job:
                        state = (resource_doc.get('topup') or {}).get('status')
                    else:
                        state = resource_doc.get('status') if kind == 'ingestion' else resource_doc.get('generation_status')
                    if state in ('failed', 'extraction_failed', 'partial', 'needs_review', 'cancelled', 'retrying', 'unknown', 'superseded'):
                        status = state
                except Exception:
                    log.exception('Usage outcome lookup failed')
                return result
            except asyncio.CancelledError:
                status = 'cancelled'
                raise
            except Exception as exc:
                status, error = 'failed', type(exc).__name__
                raise
            finally:
                current.reset(token)
                if context['writes']:
                    await asyncio.gather(*(asyncio.wrap_future(f) for f in context['writes']), return_exceptions=True)
                try:
                    from deps import db
                    await db.usage_operations.update_one({'id': operation['id']}, {'$set': {
                        'status': status, 'error_type': error, 'finished_at': now(),
                        'active_ms': round((time.monotonic() - started) * 1000)}})
                except Exception:
                    log.exception('Usage operation finalization failed')
        return wrapped
    return decorate
