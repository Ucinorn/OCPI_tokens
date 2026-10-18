"""Outbound OCPI calls to a partner. Every request goes through LoggingTransport, so it shows up in the message log."""

import time

import httpx

from app.ocpi.common import VERSION, encode_token
from app.ocpi.log import record


class PartnerError(Exception):
    pass


class UnsupportedVersion(PartnerError):
    pass


# Anything that can go wrong talking to a partner.
ERRORS = (httpx.HTTPError, PartnerError, KeyError, TypeError, ValueError)


class LoggingTransport(httpx.BaseTransport):
    def __init__(self, partner_id: int | None):
        self.partner_id = partner_id
        self.inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        start = time.monotonic()
        log = dict(direction="OUT", method=request.method, url=request.url, partner_id=self.partner_id,
                   request_headers=request.headers, request_body=request.content)
        try:
            response = self.inner.handle_request(request)
            response.read()
        except httpx.HTTPError as e:
            record(http_status=None, duration_ms=int((time.monotonic() - start) * 1000), response_body=f"{type(e).__name__}: {e}", **log)
            raise
        record(http_status=response.status_code, duration_ms=int((time.monotonic() - start) * 1000), response_body=response.content, **log)
        return response

    def close(self):
        self.inner.close()


def client(partner_id: int | None, token: str) -> httpx.Client:
    return httpx.Client(timeout=10, headers={"Authorization": encode_token(token)}, transport=LoggingTransport(partner_id))


def data(response: httpx.Response):
    """Unwrap an OCPI response envelope, raising on HTTP or OCPI errors."""
    response.raise_for_status()
    body = response.json()
    if body.get("status_code") != 1000:
        raise PartnerError(f"OCPI {body.get('status_code')}: {body.get('status_message')}")
    return body.get("data")


def discover(c: httpx.Client, versions_url: str) -> list[dict]:
    """GET versions, pick ours, and return that version's endpoints."""
    match = next((v for v in data(c.get(versions_url)) if v["version"] == VERSION), None)
    if match is None:
        raise UnsupportedVersion(f"Partner does not support OCPI {VERSION}")
    return data(c.get(match["url"]))["endpoints"]


def endpoint(endpoints: list[dict], identifier: str) -> str:
    matches = sorted((e for e in endpoints if e["identifier"] == identifier), key=lambda e: e.get("role") != "RECEIVER")
    if not matches:
        raise PartnerError(f"Partner has no {identifier} endpoint")
    return matches[0]["url"]
