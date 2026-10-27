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
