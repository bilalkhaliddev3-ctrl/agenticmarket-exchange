"""Seller-side HMAC signature verification for Agentic Market Exchange (W4D19).

==============================================================================
COPY THIS FILE INTO YOUR PROJECT. NO PACKAGE INSTALL REQUIRED.
==============================================================================

When Agentic Market Exchange's gateway forwards a buyer's request to your agent it attaches
three headers:

    X-AgenticMarket-Signature: t=<unix_seconds>,v1=<hex_sha256>
    X-AgenticMarket-Agent-ID:  <your agent's UUID>
    X-AgenticMarket-Timestamp: <unix_seconds>

Your endpoint MUST verify the signature before doing any work. Without
that check, anyone who learns your endpoint URL can hit it directly and
skip paying through the Exchange.

Setup:

    export AGENTIC_MARKET_SECRET="<the secret from your registration email>"

Usage — Flask:

    from flask import request, abort
    from agenticmarket_verify import verify_signature

    @app.route('/agent', methods=['POST'])
    def agent_endpoint():
        if not verify_signature(
            payload=request.get_data(),
            signature_header=request.headers.get('X-AgenticMarket-Signature', ''),
            timestamp_header=request.headers.get('X-AgenticMarket-Timestamp', ''),
        ):
            abort(401)
        # ... your real agent logic
        return {'result': '...'}

Usage — FastAPI:

    from fastapi import FastAPI, Request, HTTPException
    from agenticmarket_verify import verify_signature

    app = FastAPI()

    @app.post('/agent')
    async def agent_endpoint(request: Request):
        body = await request.body()
        if not verify_signature(
            payload=body,
            signature_header=request.headers.get('x-agenticmarket-signature', ''),
            timestamp_header=request.headers.get('x-agenticmarket-timestamp', ''),
        ):
            raise HTTPException(status_code=401)
        # ... your real agent logic
        return {'result': '...'}

The signed message format is ``f"{timestamp}.".encode() + payload``. We
embed the timestamp into the HMAC so a captured request can't be
replayed five minutes later — ``verify_signature`` rejects any
timestamp more than 300 seconds away from the local clock.

Comparison is constant-time via ``hmac.compare_digest`` (Rule 7).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time


# Read at import time so misconfiguration fails loudly on the very first
# request rather than silently rejecting traffic.
AGENTIC_MARKET_SECRET = os.environ.get('AGENTIC_MARKET_SECRET', '')

# Replay window. 300s matches Agentic Market Exchange's gateway-side check — widening it
# weakens replay protection; narrowing it risks clock-skew false negatives.
MAX_AGE_SECONDS = 300


class InWithVerificationError(Exception):
    """Raised when the verifier itself is misconfigured (e.g. env var
    missing). A *failed* verification is not an error — it returns False."""


def verify_signature(
    payload: bytes,
    signature_header: str,
    timestamp_header: str,
) -> bool:
    """Return True iff this request was signed by Agentic Market Exchange for your agent.

    ``payload`` must be the RAW request body bytes — not a parsed dict.
    Decode JSON only AFTER verification succeeds.

    Raises :class:`InWithVerificationError` if ``AGENTIC_MARKET_SECRET``
    is unset. All other failure modes (bad timestamp, malformed header,
    expired window, signature mismatch) return False so callers can map
    every "reject" to a single 401 branch.
    """
    if not AGENTIC_MARKET_SECRET:
        raise InWithVerificationError(
            "AGENTIC_MARKET_SECRET env var is not set. Get the secret "
            "from your Agentic Market Exchange registration email or rotate one from the "
            "developer dashboard."
        )

    try:
        timestamp = int(timestamp_header)
    except (TypeError, ValueError):
        return False

    if abs(int(time.time()) - timestamp) > MAX_AGE_SECONDS:
        return False

    # Wire format: t=<ts>,v1=<hex>. Tolerant of surrounding whitespace,
    # strict about the v1 key — silently accepting v2/v3 in the future
    # would let an attacker downgrade signatures.
    parts: dict[str, str] = {}
    for piece in signature_header.split(','):
        if '=' not in piece:
            return False
        k, v = piece.split('=', 1)
        parts[k.strip()] = v.strip()
    if 'v1' not in parts:
        return False

    message = f"{timestamp}.".encode('utf-8') + (payload or b'')
    expected_sig = hmac.new(
        AGENTIC_MARKET_SECRET.encode('utf-8'),
        message,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, parts['v1'])
