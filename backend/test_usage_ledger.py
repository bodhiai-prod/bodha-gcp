import json

from usage_ledger import price


def test_unknown_pricing_is_not_zero(monkeypatch):
    monkeypatch.delenv('USAGE_MODEL_PRICING_JSON', raising=False)
    assert price('unconfigured', 100, 100)['cost_inr'] is None


def test_price_snapshot_and_conversion(monkeypatch):
    monkeypatch.setenv('USAGE_MODEL_PRICING_JSON', json.dumps({'model': {
        'input_per_million': 2, 'output_per_million': 8, 'per_image': .02}}))
    monkeypatch.setenv('USAGE_USD_INR', '85')
    charge = price('model', 1_000_000, 500_000, 2)
    assert charge['cost_usd'] == 6.04
    assert charge['cost_inr'] == 513.4
    assert charge['fx_usd_inr'] == 85


def test_invalid_configuration_is_unknown(monkeypatch):
    monkeypatch.setenv('USAGE_MODEL_PRICING_JSON', 'invalid')
    assert price('model', 1, 1)['provenance'] == 'unknown'


def test_unknown_model_keeps_exchange_rate_for_provider_reported_cost(monkeypatch):
    monkeypatch.setenv('USAGE_MODEL_PRICING_JSON', '{}')
    monkeypatch.setenv('USAGE_USD_INR', '85')
    monkeypatch.setattr('usage_rates.public_rates', lambda: {})
    result = price('model-not-in-public-catalogue', 1, 1)
    assert result['cost_inr'] is None
    assert result['fx_usd_inr'] == 85


def test_vision_pdf_cost_uses_list_price(monkeypatch):
    monkeypatch.setenv('USAGE_MODEL_PRICING_JSON', '{}')
    monkeypatch.setenv('USAGE_USD_INR', '80')
    monkeypatch.setattr('usage_rates.public_rates', lambda: {})
    result = price('gcp/vision-document-text', 0, 0, 20)
    assert result['cost_inr'] == 2.4
    assert 'free tier' in result['pricing']['note']


def test_concurrent_provider_calls_keep_retry_context(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    from usage_ledger import call_attempt, current, record_call

    stored = []
    class Collection:
        async def insert_one(self, row):
            stored.append(row)
    monkeypatch.setitem(sys.modules, 'deps', SimpleNamespace(db=SimpleNamespace(usage_calls=Collection())))
    monkeypatch.setenv('USAGE_MODEL_PRICING_JSON', json.dumps({'m': {'input_per_million': 1, 'output_per_million': 2}}))
    monkeypatch.setenv('USAGE_USD_INR', '80')

    async def run():
        ctx = {'id': 'op', 'resource_id': 'paper', 'loop': asyncio.get_running_loop(), 'writes': []}
        token = current.set(ctx)
        try:
            async def emit(attempt):
                with call_attempt(request_group='group', provider_attempt=attempt):
                    await asyncio.to_thread(record_call, {'kind': 'paper', 'model_id': 'm', 'input_tokens': 10, 'output_tokens': 20, 'outcome': 'ok'})
            await asyncio.gather(emit(1), emit(2))
            await asyncio.gather(*(asyncio.wrap_future(future) for future in ctx['writes']))
        finally:
            current.reset(token)
    asyncio.run(run())
    assert {row['provider_attempt'] for row in stored} == {1, 2}
    assert all(row['request_group'] == 'group' and row['operation_id'] == 'op' for row in stored)
