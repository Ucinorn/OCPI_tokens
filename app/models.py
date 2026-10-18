from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Token(Base):
    """An OCPI 2.2.1 Token. country_code/party_id are ours (from settings), so not stored."""

    __tablename__ = "tokens"
    __table_args__ = (UniqueConstraint("uid_key", "type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36))  # as entered, and returned as-is over OCPI
    uid_key: Mapped[str] = mapped_column(String(36))  # upper-cased uid for lookups: OCPI uid is a case-insensitive CiString
    type: Mapped[str] = mapped_column(String(16), default="RFID")
    contract_id: Mapped[str] = mapped_column(String(36), index=True)
    visual_number: Mapped[str | None] = mapped_column(String(64), index=True)
    issuer: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[str | None] = mapped_column(String(36), index=True)
    valid: Mapped[bool] = mapped_column(default=True)
    whitelist: Mapped[str] = mapped_column(String(16), default="NEVER")
    language: Mapped[str | None] = mapped_column(String(2))
    default_profile_type: Mapped[str | None] = mapped_column(String(16))
    energy_contract: Mapped[dict | None] = mapped_column(JSON)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True)

    @validates("uid")
    def _set_uid_key(self, key, value):
        self.uid_key = value.upper()
        return value


class Partner(Base):
    """A connected OCPI platform (the CPMS), registered via the credentials handshake.

    Either side can start the handshake, so tokens are named by who uses them rather than the spec's A/B/C.
    """

    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_a: Mapped[str | None] = mapped_column(String(64), unique=True)  # one-time registration token we hand out
    our_token: Mapped[str | None] = mapped_column(String(64), unique=True)  # they use it to call us
    their_token: Mapped[str | None] = mapped_column(String(64))  # we use it to call them
    versions_url: Mapped[str | None] = mapped_column(String(255))
    endpoints: Mapped[list | None] = mapped_column(JSON)
    roles: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def their_token_set(self) -> bool:
        return self.their_token is not None


class OcpiMessage(Base):
    """Every OCPI request in or out, for debugging the connection."""

    __tablename__ = "ocpi_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    partner_id: Mapped[int | None] = mapped_column(index=True)
    direction: Mapped[str] = mapped_column(String(3))  # IN or OUT
    method: Mapped[str] = mapped_column(String(8))
    url: Mapped[str] = mapped_column(String(1024))
    http_status: Mapped[int | None]
    ocpi_status: Mapped[int | None]
    duration_ms: Mapped[int]
    request_headers: Mapped[dict | None] = mapped_column(JSON)
    request_body: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[str | None] = mapped_column(Text)  # or the error, if no response came back
