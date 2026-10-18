from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings

TokenType = Literal["AD_HOC_USER", "APP_USER", "OTHER", "RFID"]
WhitelistType = Literal["ALWAYS", "ALLOWED", "ALLOWED_OFFLINE", "NEVER"]
ProfileType = Literal["CHEAP", "FAST", "GREEN", "REGULAR"]


class EnergyContract(BaseModel):
    supplier_name: str = Field(max_length=64)
    contract_id: str | None = Field(None, max_length=64)


class TokenIn(BaseModel):
    uid: str = Field(max_length=36)
    type: TokenType = "RFID"
    contract_id: str = Field(max_length=36)
    visual_number: str | None = Field(None, max_length=64)
    issuer: str = Field(default_factory=lambda: settings.issuer, max_length=64)
    group_id: str | None = Field(None, max_length=36)
    valid: bool = True
    # NEVER = the CPO must always ask us in real time, which is the flow this service exists for.
    whitelist: WhitelistType = "NEVER"
    language: str | None = Field(None, min_length=2, max_length=2)
    default_profile_type: ProfileType | None = None
    energy_contract: EnergyContract | None = None


class TokenPatch(BaseModel):
    uid: str | None = Field(None, max_length=36)
    type: TokenType | None = None
    contract_id: str | None = Field(None, max_length=36)
    visual_number: str | None = Field(None, max_length=64)
    issuer: str | None = Field(None, max_length=64)
    group_id: str | None = Field(None, max_length=36)
    valid: bool | None = None
    whitelist: WhitelistType | None = None
    language: str | None = Field(None, min_length=2, max_length=2)
    default_profile_type: ProfileType | None = None
    energy_contract: EnergyContract | None = None


class TokenOut(TokenIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_updated: datetime


class TokenPage(BaseModel):
    total: int
    items: list[TokenOut]


class PartnerIn(BaseModel):
    name: str = Field(max_length=100)


class ConnectIn(BaseModel):
    """The CPMS gave us a token A and their versions URL, and we start the handshake."""

    name: str = Field(max_length=100)
    versions_url: str
    token: str = Field(max_length=64)


class AuthorizeTest(BaseModel):
    uid: str = Field(max_length=36)
    type: TokenType = "RFID"


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_a: str | None
    versions_url: str | None
    endpoints: list[dict] | None
    roles: list[dict] | None
    their_token_set: bool
    created_at: datetime
    registered_at: datetime | None


class TestResult(BaseModel):
    ok: bool
    detail: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    partner_id: int | None
    direction: str
    method: str
    url: str
    http_status: int | None
    ocpi_status: int | None
    duration_ms: int
    request_headers: dict | None
    request_body: str | None
    response_body: str | None
