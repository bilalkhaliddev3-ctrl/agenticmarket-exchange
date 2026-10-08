"""Example website conversion for the G.A.M.E. Prize Challenge.

Converts an existing website into a working agent by exposing one of its
public functions (here, a catalog search) as an agent endpoint. Website
conversions count toward the 1,000,000 exactly like new agents, provided the
site owner has given permission: record it in the manifest's
``permission_reference`` and set ``owner_permission`` to true.

    export AGENTIC_MARKET_SECRET="<your fleet signing secret>"
    export SITE_SEARCH_URL="https://shop.example.com/api/search?q={query}"
    python website_conversion.py --port 8081

Register it with an entry like the last line of agents.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agenticmarket_verify import verify_signature

SITE_SEARCH_URL = os.environ.get('SITE_SEARCH_URL', '')
CHECK_HEADER = 'X-AgenticMarket-Challenge-Check'


def search_site(query: str) -> list:
    """Call the site's own public search and return the first results."""
    url = SITE_SEARCH_URL.format(query=urllib.parse.quote(query))
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 (operator-set URL)
        data = json.loads(response.read())
    items = data.get('results', data) if isinstance(data, dict) else data
    return list(items)[:10]


class ConversionHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {'ok': True})

    def do_POST(self):
        body = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 1024 * 1024))
        if not verify_signature(body, self.headers.get('X-AgenticMarket-Signature', ''),
                                self.headers.get('X-AgenticMarket-Timestamp', '')):
            return self._send(401, {'error': 'invalid signature'})
        task = json.loads(body or b'{}')
        if self.headers.get(CHECK_HEADER) == '1':
            return self._send(200, {'ok': True, 'site': SITE_SEARCH_URL.split('/')[2] if SITE_SEARCH_URL else ''})
        query = str(task.get('query') or task.get('task') or '').strip()
        if not query:
            return self._send(400, {'error': 'send {"query": "..."}'})
        try:
            return self._send(200, {'query': query, 'results': search_site(query)})
        except Exception as exc:  # noqa: BLE001 — a 5xx refunds the buyer
            return self._send(502, {'error': f'site search failed: {exc}'[:200]})

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Website conversion agent')
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()
    ThreadingHTTPServer(('0.0.0.0', args.port), ConversionHandler).serve_forever()
