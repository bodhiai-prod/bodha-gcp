"""Public OpenRouter list prices and dated USD/INR quotes, cached per process."""
import json
import threading
import time
from urllib.request import urlopen

_cache = {}
_lock = threading.Lock()


def public_rates():
    with _lock:
        if time.monotonic() - _cache.get('time', -86400) < 3600:
            return _cache
        try:
            with urlopen('https://openrouter.ai/api/v1/models', timeout=10) as response:
                models = json.load(response)['data']
            with urlopen('https://open.er-api.com/v6/latest/USD', timeout=10) as response:
                fx = json.load(response)
            rates = {}
            for model in models:
                pricing = model.get('pricing') or {}
                if 'prompt' in pricing and 'completion' in pricing:
                    rates[model['id']] = {
                        'input_per_million': float(pricing['prompt']) * 1_000_000,
                        'output_per_million': float(pricing['completion']) * 1_000_000,
                        'per_request': float(pricing.get('request') or 0),
                        'source': 'https://openrouter.ai/api/v1/models'}
            _cache.update(time=time.monotonic(), rates=rates, fx=float(fx['rates']['INR']),
                          fx_date=fx.get('time_last_update_utc'), fx_source='https://open.er-api.com/v6/latest/USD')
        except Exception:
            # Keep the last successful snapshot on transient network failures.
            _cache['time'] = time.monotonic() - 3540
        return _cache


async def reprice_unknown(db):
    import asyncio
    from usage_ledger import price
    await asyncio.to_thread(public_rates)
    updated = 0
    rows = await db.usage_calls.find({}, {'_id': 0}).to_list(None)
    for row in rows:
        if row.get('cost_inr') is not None or row.get('outcome') == 'failed':
            continue
        charge = price(row.get('model_id', ''), row.get('input_tokens', 0), row.get('output_tokens', 0), row.get('images', 0))
        if charge['cost_inr'] is None:
            continue
        if row.get('source') == 'historical':
            charge['provenance'] = 'reconstructed_estimate'
        await db.usage_calls.update_one({'id': row['id']}, {'$set': charge})
        updated += 1
    return {'updated': updated, 'unknown': sum(r.get('cost_inr') is None for r in rows) - updated}
