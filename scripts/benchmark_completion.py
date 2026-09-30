#!/usr/bin/env python3
"""Opt-in staging acceptance benchmark. Never operates on existing papers.

Manifest: [{"name": "...", "mode": "normal|template|competitive",
            "path": "/api/papers/generate", "payload": {...},
            "isc_configuration": "isc6|isc7" (on the two required ISC cases)}]
Set BODHA_BENCHMARK_TOKEN outside the command line. --execute permits spend.
"""
import argparse
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, default=Path('completion-benchmark.json'))
    parser.add_argument('--execute', action='store_true', help='Explicitly incur provider spend on staging')
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text())
    counts = Counter(case['mode'] for case in cases)
    if any(counts[mode] < 10 for mode in ('normal', 'template', 'competitive')):
        parser.error('Release acceptance requires at least ten papers per mode')
    if not {'isc6', 'isc7'} <= {case.get('isc_configuration') for case in cases}:
        parser.error('Include both investigated ISC configurations, labelled isc6 and isc7')
    host = urlparse(args.base_url).hostname or ''
    if host not in ('localhost', '127.0.0.1') and 'staging' not in host:
        parser.error('Use a local or explicitly named staging host; production is forbidden')
    for case in cases:
        path = case['path']
        if path != '/api/papers/generate' and not (path.startswith('/api/competitive-exams/') and path.endswith('/generate-paper')):
            parser.error('Manifest may only create new benchmark papers')
    if not args.execute:
        print(json.dumps({'validated_cases': len(cases), 'modes': counts, 'provider_calls': 0, 'message': 'Dry run; use --execute only after approving provider spend.'}))
        return
    token = os.environ.get('BODHA_BENCHMARK_TOKEN')
    if not token:
        parser.error('Set BODHA_BENCHMARK_TOKEN')
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + token
    results = []
    for case in cases:
        start = time.monotonic()
        response = session.post(args.base_url.rstrip('/') + case['path'], json=case['payload'], timeout=60)
        response.raise_for_status()
        paper_id = response.json()['id']
        paper = {}
        while time.monotonic() - start < 480:
            response = session.get(f'{args.base_url.rstrip("/")}/api/papers/{paper_id}', timeout=30)
            response.raise_for_status()
            paper = response.json()
            completion = paper.get('completion') or {}
            if completion.get('status') in ('ready', 'incomplete', 'cancelled'):
                break
            time.sleep(2)
        completion = paper.get('completion') or {}
        results.append({'name': case['name'], 'mode': case['mode'], 'paper_id': paper_id,
            'fully_ready_seconds': round(time.monotonic() - start, 2) if completion.get('ready') else None,
            'completion': completion, 'correctness_review': 'required'})
        args.output.write_text(json.dumps({'results': results, 'release_gate': 'pending'}, indent=2))
    latencies = sorted(row['fully_ready_seconds'] for row in results if row['fully_ready_seconds'] is not None)
    p95 = latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None
    high_recovery = sum(row['completion'].get('target_scored_parts', 0) - (row['completion'].get('first_pass_valid_count') or 0) > 6 for row in results)
    summary = {'results': results, 'p95_fully_ready_seconds': p95,
        'automatic_completion_gate': len(latencies) == len(cases),
        'performance_gate': bool(p95 is not None and p95 < 120),
        'repeated_large_recovery_gate': high_recovery < 2,
        'correctness_gate': 'Requires independent subject review of all generated papers',
        'release_gate': 'blocked_pending_correctness_review'}
    args.output.write_text(json.dumps(summary, indent=2))
    print(json.dumps({key: value for key, value in summary.items() if key != 'results'}, indent=2))


if __name__ == '__main__':
    main()
