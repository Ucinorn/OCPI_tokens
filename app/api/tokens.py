"""Admin CRUD for tokens. Sits behind Entra; the HTML frontend uses this same API."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Token
from app.schemas import TokenIn, TokenOut, TokenPage, TokenPatch, TokenType

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


def _get(db: Session, token_id: int) -> Token:
    token = db.get(Token, token_id)
    if token is None:
        raise HTTPException(404, "Token not found")
    return token


def _commit(db: Session):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A token with this uid (ignoring case) and type already exists")


@router.get("", response_model=TokenPage)
def list_tokens(
    q: str | None = Query(None, description="Prefix match on uid, contract_id or visual_number"),
    type: TokenType | None = None,
    group_id: str | None = None,
    valid: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(Token)
    if q:
        like = f"{q}%"
        stmt = stmt.where(or_(Token.uid.ilike(like), Token.contract_id.ilike(like), Token.visual_number.ilike(like)))
    if type:
        stmt = stmt.where(Token.type == type)
    if group_id:
        stmt = stmt.where(Token.group_id == group_id)
    if valid is not None:
        stmt = stmt.where(Token.valid == valid)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Token.id.desc()).offset(offset).limit(limit)).all()
    return {"total": total, "items": items}


@router.get("/groups", response_model=list[str])
def list_groups(db: Session = Depends(get_db)):
    return db.scalars(select(Token.group_id).where(Token.group_id.is_not(None)).distinct().order_by(Token.group_id))


@router.post("", response_model=TokenOut, status_code=201)
def create_token(body: TokenIn, db: Session = Depends(get_db)):
    token = Token(**body.model_dump())
    db.add(token)
    _commit(db)
    return token


@router.get("/{token_id}", response_model=TokenOut)
def get_token(token_id: int, db: Session = Depends(get_db)):
    return _get(db, token_id)


@router.patch("/{token_id}", response_model=TokenOut)
def update_token(token_id: int, body: TokenPatch, db: Session = Depends(get_db)):
    token = _get(db, token_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(token, key, value)
    _commit(db)
    return token


@router.delete("/{token_id}", status_code=204)
def delete_token(token_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, token_id))
    db.commit()
    return Response(status_code=204)
