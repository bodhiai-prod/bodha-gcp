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
from datetime import datetime, timezone

log = logging.getLogger(__name__)
current = contextvars.ContextVar('usage_operation', default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def price(model, inputs, outputs, images=0):
    """Rates are explicitly configured, never guessed. Snapshot on every call."""
    try:
        rates = json.loads(os.environ.get('USAGE_MODEL_PRICING_JSON', '{}'))
        rate = rates.get(model)
        fx = float(os.environ.get('USAGE_USD_INR', '0'))
        if not isinstance(rate, dict) or fx <= 0 or (inputs and 'input_per_million' not in rate) or (outputs and 'output_per_million' not in rate) or (images and not outputs and 'per_image' not in rate):
            return {'cost_inr': None, 'provenance': 'unknown'}
        usd = (inputs * float(rate.get('input_per_million', 0)) + outputs * float(rate.get('output_per_million', 0))) / 1_000_000 + images * float(rate.get('per_image', 0))
        return {'cost_inr': usd * fx, 'cost_usd': usd, 'fx_usd_inr': fx,
                'pricing': rate, 'provenance': 'estimated'}
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
    event = dict(row, id=str(uuid.uuid4()), operation_id=context['id'],
                 resource_id=context['resource_id'], timestamp=now())
    event.update(price(row['model_id'], row.get('input_tokens', 0), row.get('output_tokens', 0), row.get('images', 0)))
    # The sink may run on a provider thread. Keep persistence on the owning loop.
    future = asyncio.run_coroutine_threadsafe(safe_write('usage_calls', event), context['loop'])
    context['writes'].append(future)


def tracked(kind, resource_arg='paper_id'):
    def decorate(fn):
        signature = inspect.signature(fn)
        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            job = bound.get('job') or {}
            resource = bound.get(resource_arg) or job.get('paper_id') or (bound.get('paper') or {}).get('id')
            if not resource:
                return await fn(*args, **kwargs)
            parent = current.get()
            operation = {'id': str(uuid.uuid4()), 'resource_id': resource,
                         'resource_type': 'book' if kind == 'ingestion' else 'paper',
                         'kind': kind, 'user_id': bound.get('user_id') or bound.get('owner_id') or job.get('user_id'),
                         'parent_id': parent['id'] if parent else None, 'started_at': now(), 'status': 'running'}
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
                    collection = db.textbooks if kind == 'ingestion' else db.papers
                    resource_doc = await collection.find_one({'id': resource}) or {}
                    state = resource_doc.get('status') if kind == 'ingestion' else resource_doc.get('generation_status')
                    if state in ('failed', 'extraction_failed', 'partial'):
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
