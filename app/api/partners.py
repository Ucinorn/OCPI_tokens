"""Admin endpoints for OCPI partners: register them (whichever side starts it), test, rotate and remove."""

import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Partner
from app.ocpi.client import ERRORS, client, data, discover, endpoint
from app.ocpi.common import VERSION, base_url
from app.ocpi.credentials import our_credentials
from app.schemas import AuthorizeTest, ConnectIn, PartnerIn, PartnerOut, TestResult

router = APIRouter(prefix="/api/partners", tags=["partners"])


def _get(db: Session, partner_id: int) -> Partner:
    partner = db.get(Partner, partner_id)
    if partner is None:
        raise HTTPException(404, "Partner not found")
    return partner


def _handshake(db: Session, partner: Partner, method: str):
    """We start the handshake: POST to register, PUT to rotate tokens."""
    previous = partner.our_token
    partner.our_token = secrets.token_urlsafe(32)
    db.commit()  # the partner may call our versions endpoint with the new token before it answers
    try:
        with client(partner.id, partner.their_token) as c:
            endpoints = discover(c, partner.versions_url)
            creds = data(c.request(method, endpoint(endpoints, "credentials"), json=our_credentials(partner)))
            partner.their_token = creds["token"]
            partner.versions_url = creds["url"]
            partner.roles = creds["roles"]
    except ERRORS as e:
        db.rollback()
        partner.our_token = previous
        db.commit()
        raise HTTPException(502, f"Handshake failed: {e}")
    partner.endpoints = endpoints
    partner.token_a = None
    partner.registered_at = datetime.now(timezone.utc)
    db.commit()
    return partner


@router.get("", response_model=list[PartnerOut])
def list_partners(db: Session = Depends(get_db)):
    return db.scalars(select(Partner).order_by(Partner.id))


@router.post("", response_model=PartnerOut, status_code=201)
def create_partner(body: PartnerIn, db: Session = Depends(get_db)):
    """The CPMS starts the handshake: we issue a token A for them."""
    partner = Partner(name=body.name, token_a=secrets.token_urlsafe(32))
    db.add(partner)
    db.commit()
    return partner


@router.post("/connect", response_model=PartnerOut, status_code=201)
def connect_partner(body: ConnectIn, db: Session = Depends(get_db)):
    """We start the handshake with the token A the CPMS gave us. On failure the partner is kept, so you can retry with /register."""
    partner = Partner(name=body.name, versions_url=body.versions_url, their_token=body.token)
    db.add(partner)
    db.commit()
    return _handshake(db, partner, "POST")


@router.post("/{partner_id}/register", response_model=PartnerOut)
def register_partner(partner_id: int, db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    if partner.registered_at or not partner.their_token:
        raise HTTPException(400, "Partner is already registered, or we have no token A for them")
    return _handshake(db, partner, "POST")


@router.post("/{partner_id}/rotate", response_model=PartnerOut)
def rotate_credentials(partner_id: int, db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    if not partner.registered_at:
        raise HTTPException(400, "Partner is not registered")
    return _handshake(db, partner, "PUT")


@router.post("/{partner_id}/test", response_model=TestResult)
def test_connection(partner_id: int, db: Session = Depends(get_db)):
    """Call the partner's versions, version details and (if registered) credentials endpoints."""
    partner = _get(db, partner_id)
    if not partner.their_token or not partner.versions_url:
        return {"ok": False, "detail": "We have no token for this partner yet. They need to register with their token A first."}
    try:
        with client(partner.id, partner.their_token) as c:
            endpoints = discover(c, partner.versions_url)
            detail = f"OCPI {VERSION} OK. Endpoints: {', '.join(sorted({e['identifier'] for e in endpoints}))}"
            if partner.registered_at:
                data(c.get(endpoint(endpoints, "credentials")))
                detail += ". Credentials OK"
    except ERRORS as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": detail}


@router.post("/{partner_id}/test-authorize")
def test_authorize(partner_id: int, body: AuthorizeTest, db: Session = Depends(get_db)):
    """Send a real authorize request to our own BASE_URL as this partner, the same call the CPMS makes."""
    partner = _get(db, partner_id)
    if not partner.our_token:
        raise HTTPException(400, "Partner is not registered")
    url = f"{base_url()}/{VERSION}/tokens/{quote(body.uid, safe='')}/authorize"
    try:
        with client(None, partner.our_token) as c:
            return c.post(url, params={"type": body.type}).json()
    except ERRORS as e:
        raise HTTPException(502, f"Could not reach {url}: {e}")


@router.delete("/{partner_id}", status_code=204)
def delete_partner(partner_id: int, db: Session = Depends(get_db)):
    """Unregister on their side if we can (the result is in the message log), then delete locally."""
    partner = _get(db, partner_id)
    if partner.registered_at and partner.their_token and partner.endpoints:
        try:
            with client(partner.id, partner.their_token) as c:
                c.delete(endpoint(partner.endpoints, "credentials"))
        except ERRORS:
            pass
    db.delete(partner)
    db.commit()
    return Response(status_code=204)
