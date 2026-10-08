"""Register a fleet with the G.A.M.E. Prize Challenge batch API.

Reads agents from a JSON array, JSON Lines or CSV file, sends them in batches
of up to 10,000, waits for each batch to be processed, and writes every
rejected agent (with the reason and how to fix it) to rejections.csv.

    export GAME_API_KEY="chl_..."          # from your entrant dashboard
    python register_batch.py agents.jsonl

Options: --base-url (default https://agenticmarket.exchange), --batch-size,
--label. Requires: pip install requests
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

BATCH_LIMIT = 10_000
CSV_FIELDS = ['ref', 'name', 'description', 'capabilities', 'tier', 'endpoint', 'framework',
              'site_url', 'permission_reference']


def load_agents(path: Path) -> list[dict]:
    text = path.read_text(encoding='utf-8')
    if path.suffix.lower() == '.csv':
        agents = []
        for row in csv.DictReader(text.splitlines()):
            agent = {k: (row.get(k) or '').strip() for k in CSV_FIELDS[:7]}
            agent['capabilities'] = [c.strip() for c in agent['capabilities'].split(';') if c.strip()]
            if (row.get('site_url') or '').strip():
                agent['web_conversion'] = {'site_url': row['site_url'].strip(), 'owner_permission': True,
                                           'permission_reference': (row.get('permission_reference') or '').strip()}
            agents.append(agent)
        return agents
    if text.lstrip().startswith('['):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def request(method: str, url: str, api_key: str, **kwargs) -> requests.Response:
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    for attempt in range(6):
        try:
            response = requests.request(method, url, headers=headers, timeout=300, **kwargs)
        except requests.RequestException as exc:
            print(f'  network error ({exc}); retrying', file=sys.stderr)
        else:
            if response.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f'  hourly batch limit reached; waiting {wait} s', file=sys.stderr)
                time.sleep(wait)
                continue
            if response.status_code < 500:
                return response
            print(f'  server error {response.status_code}; retrying', file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f'Giving up on {method} {url}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Register agents with the G.A.M.E. batch API')
    parser.add_argument('file', type=Path)
    parser.add_argument('--base-url', default='https://agenticmarket.exchange')
    parser.add_argument('--batch-size', type=int, default=BATCH_LIMIT)
    parser.add_argument('--label', default='')
    args = parser.parse_args()
    api_key = os.environ.get('GAME_API_KEY', '')
    if not api_key:
        raise SystemExit('Set GAME_API_KEY to the API key from your entrant dashboard.')
    base = args.base_url.rstrip('/')
    size = min(args.batch_size, BATCH_LIMIT)

    agents = load_agents(args.file)
    print(f'{len(agents):,} agents in {args.file}')
    rejected_rows = []
    totals = {'accepted': 0, 'rejected': 0}
    for start in range(0, len(agents), size):
        chunk = agents[start:start + size]
        label = args.label or f'{args.file.name} {start + 1}-{start + len(chunk)}'
        response = request('POST', f'{base}/v1/challenge/batches/', api_key,
                           data=json.dumps({'agents': chunk, 'label': label}))
        if response.status_code != 202:
            raise SystemExit(f'Batch refused ({response.status_code}): {response.text[:300]}')
        batch = response.json()
        print(f"Batch {batch['batch']}: {len(chunk):,} agents submitted")
        while batch['status'] not in ('complete', 'failed'):
            time.sleep(5)
            batch = request('GET', batch['status_url'] if 'status_url' in batch
                            else f"{base}/v1/challenge/batches/{batch['id']}/", api_key).json()
            print(f"  {batch['processed']:,}/{batch['total']:,} processed", end='\r')
        print(f"  {batch['status']}: {batch['accepted']:,} accepted, {batch['rejected']:,} rejected")
        totals['accepted'] += batch['accepted']
        totals['rejected'] += batch['rejected']
        page = 1
        while batch['rejected']:
            data = request('GET', f"{base}/v1/challenge/batches/{batch['id']}/rejections/?page={page}",
                           api_key).json()
            rejected_rows += [{'batch': data['batch'], **r} for r in data['rejections']]
            if page >= data['pages']:
                break
            page += 1

    print(f"Done: {totals['accepted']:,} accepted, {totals['rejected']:,} rejected")
    if rejected_rows:
        with open('rejections.csv', 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['batch', 'item', 'ref', 'code', 'message'])
            writer.writeheader()
            writer.writerows(rejected_rows)
        print('Every rejection, with how to fix it, is in rejections.csv')


if __name__ == '__main__':
    main()
