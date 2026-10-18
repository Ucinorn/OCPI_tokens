"""Tokens module, eMSP (Sender) interface: token list for the CPO to pull, and real-time authorization."""

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Token
from app.ocpi.common import VERSION, OcpiError, ocpi_response, ocpi_token, registered_partner
from app.schemas import TokenType

router = APIRouter(prefix=f"/ocpi/{VERSION}/tokens", tags=["ocpi"], dependencies=[Depends(registered_partner)])

MAX_LIMIT = 1000


class LocationReferences(BaseModel):
    location_id: str
    evse_uids: list[str] | None = None


@router.get("")
def list_tokens(
    request: Request,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(MAX_LIMIT, ge=1),
    db: Session = Depends(get_db),
):
    limit = min(limit, MAX_LIMIT)
    stmt = select(Token)
    if date_from:
        stmt = stmt.where(Token.last_updated >= date_from)
    if date_to:
        stmt = stmt.where(Token.last_updated < date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Token.last_updated, Token.id).offset(offset).limit(limit))

    headers = {"X-Total-Count": str(total), "X-Limit": str(MAX_LIMIT)}
    if offset + limit < total:
        headers["Link"] = f'<{request.url.include_query_params(offset=offset + limit, limit=limit)}>; rel="next"'
    return ocpi_response([ocpi_token(t) for t in rows], headers=headers)


@router.post("/{token_uid}/authorize")
def authorize(
    token_uid: str,
    type: TokenType = "RFID",
    location: LocationReferences | None = Body(None),  # accepted per spec, not used for the decision
    db: Session = Depends(get_db),
):
    token = db.scalar(select(Token).where(Token.uid_key == token_uid.upper(), Token.type == type))
    if token is None:
        raise OcpiError(2004, "Unknown Token", 404)
    return ocpi_response({"allowed": "ALLOWED" if token.valid else "BLOCKED", "token": ocpi_token(token)})
