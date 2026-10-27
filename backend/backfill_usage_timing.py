"""Backfill only historical solution groups whose calls fit one saved run."""
import asyncio
from datetime import datetime, timezone


def parse(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except ValueError:
        return None


async def run(db, apply=False):
    operations = await db.usage_operations.find({'source': 'historical', 'kind': 'solution'}, {'_id': 0}).to_list(None)
    papers = await db.papers.find({}, {'_id': 0}).to_list(None)
    calls = await db.usage_calls.find({'source': 'historical'}, {'_id': 0}).to_list(None)
    paper_by_id = {p['id']: p for p in papers}
    by_op = {}
    for call in calls:
        by_op.setdefault(call.get('operation_id'), []).append(call)
    eligible = missing = ambiguous = 0
    for operation in operations:
        if operation.get('active_ms') is not None:
            continue
        solution = (paper_by_id.get(operation['resource_id']) or {}).get('solution') or {}
        start, end = parse(solution.get('started_at')), parse(solution.get('finished_at'))
        if not start or not end or end < start or solution.get('status') not in ('ready', 'partial'):
            missing += 1
            continue
        matching_calls = by_op.get(operation['id'], [])
        # CSV timestamps mark call completion; every recorded call must belong to
        # this saved solution run. Multiple runs cannot be separated reliably.
        if not matching_calls or any(not parse(c.get('timestamp')) or not start <= parse(c['timestamp']) <= end for c in matching_calls):
            ambiguous += 1
            continue
        elapsed = round((end - start).total_seconds() * 1000)
        eligible += 1
        if apply:
            await db.usage_operations.update_one({'id': operation['id'], 'active_ms': None}, {'$set': {
                'active_ms': elapsed, 'started_at': start.isoformat(), 'finished_at': end.isoformat(),
                'timing_source': 'saved_solution_timestamps',
                'history_note': 'Elapsed time reconstructed from saved solution start and finish timestamps; provider calls matched this interval. Individual retries remain unavailable.'}})
    return {'eligible': eligible, 'missing_timestamps': missing, 'ambiguous': ambiguous, 'applied': apply}


if __name__ == '__main__':
    import argparse
    from deps import db
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    print(asyncio.run(run(db, parser.parse_args().apply)))
