"""Short-lived HMAC tickets for browser-to-PaiVoice WebSocket authentication.

Ticket format: base64url(json_payload) + "." + base64url(HMAC-SHA256(payload)).
The signing secret must remain on trusted application servers, never in browser code.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import MutableMapping


AUDIENCE = "paivoice"
MAX_TICKET_TTL_SECONDS = 300


def _b64url_decode(value: str) -> bytes:
    if not value or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for ch in value):
        raise ValueError("Malformed ticket")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_ticket(
    ticket: str,
    secret: str,
    *,
    now: int | None = None,
    used_jti: MutableMapping[str, int] | None = None,
) -> dict:
    """Verify and consume a short-lived ticket. Raises ValueError on any failure."""
    if not isinstance(ticket, str) or len(ticket) > 4096:
        raise ValueError("Missing or malformed ticket")
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("PAIVOICE_AUTH_SECRET must contain at least 32 bytes")

    parts = ticket.split(".")
    if len(parts) != 2:
        raise ValueError("Malformed ticket")
    encoded_payload, encoded_signature = parts
    expected = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    supplied = _b64url_decode(encoded_signature)
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("Invalid ticket signature")

    try:
        claims = json.loads(_b64url_decode(encoded_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed ticket payload") from exc
    if not isinstance(claims, dict):
        raise ValueError("Malformed ticket payload")

    current = int(time.time()) if now is None else int(now)
    try:
        issued = int(claims["iat"])
        expires = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Ticket timestamps are required") from exc

    subject = claims.get("sub")
    token_id = claims.get("jti")
    if claims.get("aud") != AUDIENCE:
        raise ValueError("Invalid ticket audience")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 200:
        raise ValueError("Invalid ticket subject")
    if not isinstance(token_id, str) or len(token_id) < 16 or len(token_id) > 128:
        raise ValueError("Invalid ticket identifier")
    if issued > current + 30 or expires <= current or expires <= issued:
        raise ValueError("Ticket expired or not yet valid")
    if expires - issued > MAX_TICKET_TTL_SECONDS:
        raise ValueError("Ticket lifetime is too long")

    if used_jti is not None:
        for old_id, old_exp in list(used_jti.items()):
            if old_exp <= current:
                used_jti.pop(old_id, None)
        if token_id in used_jti:
            raise ValueError("Ticket has already been used")
        used_jti[token_id] = expires

    return claims
