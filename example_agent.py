"""Example agent fleet server for the G.A.M.E. Prize Challenge.

One process serves many agents: each agent is a path, /agents/<slug>, so a
fleet of thousands can run behind one HTTPS host. Every request is checked
with the exchange signature before any work happens.

    export AGENTIC_MARKET_SECRET="<your fleet signing secret from the dashboard>"
    python example_agent.py --port 8080

Put it behind HTTPS (nginx, Caddy or a platform with TLS): the exchange only
calls public HTTPS endpoints. Register each agent with its own endpoint, for
example https://agents.example.com/agents/invoice-summarizer.

Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agenticmarket_verify import verify_signature

MAX_BODY = 1024 * 1024
CHECK_HEADER = 'X-AgenticMarket-Challenge-Check'


def handle_task(slug: str, task: dict) -> dict:
    """Your agent's real work goes here. Each slug can do something different;
    this example uppercases text so every agent has an observable result."""
    text = str(task.get('text') or task.get('task') or '')
    return {'agent': slug, 'result': text.upper()}


class AgentHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # liveness check, no work
        self._send(200, {'ok': True})

    do_HEAD = do_GET

    def do_POST(self):
        if not self.path.startswith('/agents/'):
            return self._send(404, {'error': 'unknown agent'})
        slug = self.path.removeprefix('/agents/').strip('/')
        length = min(int(self.headers.get('Content-Length') or 0), MAX_BODY)
        body = self.rfile.read(length)
        # 1. Verify first. Unsigned or wrongly signed calls get 401: this is
        #    what lets only the exchange bill through your agent.
        if not verify_signature(body,
                                self.headers.get('X-AgenticMarket-Signature', ''),
                                self.headers.get('X-AgenticMarket-Timestamp', '')):
            return self._send(401, {'error': 'invalid signature'})
        task = json.loads(body or b'{}')
        # 2. The exchange's health check: answer fast, without heavy work.
        if self.headers.get(CHECK_HEADER) == '1':
            return self._send(200, {'agent': slug, 'ok': True})
        # 3. A paid call. Return 2xx with your result (the buyer is charged);
        #    return 5xx if you cannot do the work (the buyer is refunded).
        try:
            return self._send(200, handle_task(slug, task))
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {'error': str(exc)[:200]})

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Example G.A.M.E. agent fleet server')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    print(f'Serving agents on http://0.0.0.0:{args.port}/agents/<slug>')
    ThreadingHTTPServer(('0.0.0.0', args.port), AgentHandler).serve_forever()
