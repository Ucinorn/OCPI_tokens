"""OCPI message log, polled by the UI's live view."""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OcpiMessage
from app.schemas import MessageOut

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("", response_model=list[MessageOut])
def list_messages(
    after_id: int = Query(0, description="Only messages newer than this id, for polling"),
    partner_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Returned oldest first. Without after_id, you get the latest `limit` messages."""
    stmt = select(OcpiMessage)
    if partner_id:
        stmt = stmt.where(OcpiMessage.partner_id == partner_id)
    if after_id:
        return db.scalars(stmt.where(OcpiMessage.id > after_id).order_by(OcpiMessage.id).limit(limit)).all()
    return list(reversed(db.scalars(stmt.order_by(OcpiMessage.id.desc()).limit(limit)).all()))


@router.delete("", status_code=204)
def clear_messages(db: Session = Depends(get_db)):
    db.execute(delete(OcpiMessage))
    db.commit()
    return Response(status_code=204)
