"""Records OCPI messages to stdout and the ocpi_messages table."""

import json
import logging

from app.db import SessionLocal
from app.models import OcpiMessage

log = logging.getLogger("ocpi")

MAX_BODY = 10_000  # characters kept per body; token listings can be large


def _text(body: bytes | str | None) -> str | None:
    if not body:
        return None
    text = body.decode(errors="replace") if isinstance(body, bytes) else body
    return text if len(text) <= MAX_BODY else text[:MAX_BODY] + "…[truncated]"


def _mask(headers) -> dict:
    """Keep enough of the Authorization header to tell tokens apart, never the whole token."""
    masked = {}
    for key, value in dict(headers).items():
        if key.lower() == "authorization":
            scheme, _, token = value.partition(" ")
            value = f"{scheme} {token[:6]}…"
        masked[key] = value
    return masked


def _ocpi_status(body: str | None) -> int | None:
    try:
        status = json.loads(body).get("status_code")
        return status if isinstance(status, int) else None
    except (TypeError, ValueError, AttributeError):
        return None


def record(direction, method, url, http_status, duration_ms, partner_id=None, request_headers=None, request_body=None, response_body=None):
    response_text = _text(response_body)
    message = OcpiMessage(
        direction=direction,
        method=method,
        url=str(url)[:1024],
        http_status=http_status,
        ocpi_status=_ocpi_status(response_text),
        duration_ms=duration_ms,
        partner_id=partner_id,
        request_headers=_mask(request_headers or {}),
        request_body=_text(request_body),
        response_body=response_text,
    )
    log.info("%s %s %s -> %s %s (%dms) partner=%s", direction, method, url, http_status, message.ocpi_status or "", duration_ms, partner_id)
    try:
        with SessionLocal() as db:
            db.add(message)
            db.commit()
    except Exception:
        log.exception("Could not save OCPI message")  # logging must never break the OCPI flow
