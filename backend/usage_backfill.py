"""Conservative historical usage reconstruction from resources and token CSV."""
import asyncio
import csv
import hashlib
import os
from pathlib import Path

from usage_ledger import now, price

_lock = asyncio.Lock()
KINDS = {'paper': 'question_generation', 'solution': 'solution', 'paper_regen': 'regeneration',
         'paper_topup': 'completion', 'paper_internal_choice_repair': 'choice_repair',
         'paper_template_recovery': 'completion', 'diagram': 'diagram'}


def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


async def backfill(db):
    async with _lock:
        existing = await db.usage_operations.find({}, {'_id': 0}).to_list(None)
        calls = await db.usage_calls.find({}, {'_id': 0}).to_list(None)
        ids = {item['id'] for item in existing}
        call_ids = {item['id'] for item in calls}
        live = {item['resource_id'] for item in existing if item.get('source') != 'historical'}
        resources = {}
        for collection, resource_type in [('papers', 'paper'), ('textbooks', 'book')]:
            for resource in await getattr(db, collection).find({}, {'_id': 0}).to_list(None):
                resources[resource['id']] = (resource_type, resource)
        path = Path(os.environ.get('TOKEN_USAGE_CSV') or Path(__file__).parent / 'logs' / 'llm_usage.csv')
        rows = await asyncio.to_thread(read_rows, path)
        groups = {}
        malformed = 0
        for row in rows:
            ref = row.get('ref')
            if not ref or ref in live:
                continue
            try:
                for key in ('input_tokens', 'output_tokens', 'total_tokens', 'images', 'latency_ms'):
                    row[key] = int(row.get(key) or 0)
            except (ValueError, TypeError):
                malformed += 1
                continue
            groups.setdefault((ref, KINDS.get(row.get('kind'), row.get('kind') or 'unattributed')), []).append(row)
        # Resource-only history remains useful even where no provider log survives.
        for ref, (resource_type, resource) in resources.items():
            if ref not in live and not any(key[0] == ref for key in groups):
                groups[(ref, 'ingestion' if resource_type == 'book' else 'question_generation')] = []
        created = imported = 0
        for (ref, kind), records in groups.items():
            resource_type, resource = resources.get(ref, ('paper', {}))
            operation_id = 'historical-' + hashlib.sha256(f'{ref}:{kind}'.encode()).hexdigest()
            if operation_id not in ids:
                operation = dict(id=operation_id, resource_id=ref, resource_type=resource_type,
                    kind=kind, user_id=resource.get('owner_id'), parent_id=None,
                    source='historical', imported_at=now(), status=resource.get('generation_status' if resource_type == 'paper' else 'status') or 'unknown',
                    started_at=min((r.get('timestamp') or '' for r in records), default=resource.get('created_at') or ''),
                    active_ms=None, history_note='Historical resource/stage aggregate. Individual runs, retries, queue wait and operation duration cannot be reliably reconstructed from these logs.')
                await db.usage_operations.insert_one(operation)
                ids.add(operation_id)
                created += 1
            occurrences = {}
            for row in records:
                fingerprint = hashlib.sha256(repr(sorted(row.items())).encode()).hexdigest()
                occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
                call_id = f'historical-{fingerprint}-{occurrences[fingerprint]}'
                if call_id in call_ids:
                    continue
                charge = price(row.get('model_id', ''), row['input_tokens'], row['output_tokens'], row['images'])
                if charge['cost_inr'] is not None:
                    charge['provenance'] = 'reconstructed_estimate'
                await db.usage_calls.insert_one(dict(row, **charge, id=call_id, resource_id=ref,
                    operation_id=operation_id, source='historical', imported_at=now()))
                call_ids.add(call_id)
                imported += 1
        return dict(operations_created=created, calls_imported=imported, malformed_rows=malformed,
                    csv_available=path.exists(), historical_resources=len(resources))
