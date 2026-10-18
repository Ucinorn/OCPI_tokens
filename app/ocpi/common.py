"""OCPI 2.2.1 plumbing: response envelope, errors, timestamps and Token-header authentication."""

import base64
from datetime import datetime, timezone

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Partner, Token

VERSION = "2.2.1"


def base_url() -> str:
    return settings.base_url.rstrip("/") + "/ocpi"


def ts(dt: datetime) -> str:
    """OCPI timestamps are UTC, RFC 3339, with a Z suffix."""
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ocpi_response(data=None, status_code=1000, message="Success", http_status=200, headers=None) -> JSONResponse:
    body = {"status_code": status_code, "status_message": message, "timestamp": ts(datetime.now(timezone.utc))}
    if data is not None:
        body["data"] = data
    return JSONResponse(body, status_code=http_status, headers=headers)


class OcpiError(Exception):
    def __init__(self, status_code: int, message: str, http_status: int = 400):
        self.status_code, self.message, self.http_status = status_code, message, http_status


def ocpi_token(t: Token) -> dict:
    data = {
        "country_code": settings.country_code,
        "party_id": settings.party_id,
        "uid": t.uid,
        "type": t.type,
        "contract_id": t.contract_id,
        "visual_number": t.visual_number,
        "issuer": t.issuer,
        "group_id": t.group_id,
        "valid": t.valid,
        "whitelist": t.whitelist,
        "language": t.language,
        "default_profile_type": t.default_profile_type,
        "energy_contract": t.energy_contract,
        "last_updated": ts(t.last_updated),
    }
    return {k: v for k, v in data.items() if v is not None}


def encode_token(token: str) -> str:
    return "Token " + base64.b64encode(token.encode()).decode()


def _candidates(authorization: str | None) -> list[str]:
    # 2.2.1 says the token is base64 encoded; plenty of platforms still send it raw, so accept both.
    if not authorization or not authorization.startswith("Token "):
        return []
    raw = authorization.removeprefix("Token ").strip()
    try:
        return [base64.b64decode(raw, validate=True).decode(), raw]
    except (ValueError, UnicodeDecodeError):
        return [raw]


def _find_partner(request: Request, db: Session, authorization: str | None, *columns) -> Partner:
    tokens = _candidates(authorization)
    partner = db.scalar(select(Partner).where(or_(*(c.in_(tokens) for c in columns)))) if tokens else None
    if partner is None:
        raise OcpiError(2000, "Invalid or missing token", 401)
    request.state.partner_id = partner.id  # picked up by the message log
    return partner


def any_partner(request: Request, authorization: str | None = Header(None), db: Session = Depends(get_db)) -> Partner:
    """Token A (registration) or our token (registered)."""
    return _find_partner(request, db, authorization, Partner.token_a, Partner.our_token)


def registered_partner(request: Request, authorization: str | None = Header(None), db: Session = Depends(get_db)) -> Partner:
    """Our token only."""
    return _find_partner(request, db, authorization, Partner.our_token)
