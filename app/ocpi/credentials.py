"""Credentials module, the side where the CPMS starts the handshake. See app/api/partners.py for the side where we start it."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Partner
from app.ocpi.client import ERRORS, UnsupportedVersion, client, discover
from app.ocpi.common import VERSION, OcpiError, any_partner, base_url, ocpi_response, registered_partner

router = APIRouter(prefix=f"/ocpi/{VERSION}/credentials", tags=["ocpi"])


class CredentialsIn(BaseModel):
    token: str = Field(max_length=64)
    url: str
    roles: list[dict]


def our_credentials(partner: Partner) -> dict:
    return {
        "token": partner.our_token,
        "url": f"{base_url()}/versions",
        "roles": [{
            "role": "EMSP",
            "country_code": settings.country_code,
            "party_id": settings.party_id,
            "business_details": {"name": settings.business_name},
        }],
    }


def _register(db: Session, partner: Partner, body: CredentialsIn):
    # The spec requires us to fetch their version details with their token before we answer.
    try:
        with client(partner.id, body.token) as c:
            partner.endpoints = discover(c, body.url)
    except UnsupportedVersion as e:
        raise OcpiError(3002, str(e))
    except ERRORS as e:
        raise OcpiError(3001, f"Unable to use the client's API: {e}")
    partner.their_token = body.token
    partner.versions_url = body.url
    partner.roles = body.roles
    partner.token_a = None  # token A is single use
    partner.our_token = secrets.token_urlsafe(32)
    partner.registered_at = datetime.now(timezone.utc)
    db.commit()
    return ocpi_response(our_credentials(partner))


@router.get("")
def get_credentials(partner: Partner = Depends(registered_partner)):
    return ocpi_response(our_credentials(partner))


@router.post("")
def post_credentials(body: CredentialsIn, partner: Partner = Depends(any_partner), db: Session = Depends(get_db)):
    if partner.registered_at:
        raise OcpiError(2000, "Already registered, use PUT to update", 405)
    return _register(db, partner, body)


@router.put("")
def put_credentials(body: CredentialsIn, partner: Partner = Depends(registered_partner), db: Session = Depends(get_db)):
    return _register(db, partner, body)


@router.delete("")
def delete_credentials(partner: Partner = Depends(registered_partner), db: Session = Depends(get_db)):
    partner.their_token = partner.our_token = partner.registered_at = None
    db.commit()
    return ocpi_response()
