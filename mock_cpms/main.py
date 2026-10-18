"""Mock CPMS: a minimal OCPI 2.2.1 CPO for testing the OCPI Tokens service end to end.

    uvicorn mock_cpms.main:app --port 8001     # then open http://localhost:8001

It holds a single connection to one eMSP, in memory. Restart it to start over.
It's deliberately independent of app/, so it behaves like a separate party.
"""

import base64
import itertools
import json
import os
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

VERSION = "2.2.1"
BASE_URL = os.environ.get("MOCK_BASE_URL", "http://localhost:8001").rstrip("/")
COUNTRY_CODE = os.environ.get("MOCK_COUNTRY_CODE", "GB")
PARTY_ID = os.environ.get("MOCK_PARTY_ID", "CPO")
EMSP_VERSIONS_URL = os.environ.get("MOCK_EMSP_VERSIONS_URL", "http://localhost:8000/ocpi/versions")  # prefills the connect form

state = {
    "token_a": secrets.token_urlsafe(16),  # we issue this so the eMSP can register with us
    "our_token": None,  # the eMSP uses it to call us
    "their_token": None,  # we use it to call the eMSP
    "their_versions_url": None,
    "their_roles": None,
    "endpoints": [],
    "registered_at": None,
}
log: deque = deque(maxlen=300)
_ids = itertools.count(1)

app = FastAPI(title="Mock CPMS")


# --- OCPI helpers -------------------------------------------------------------------------------

class OcpiError(Exception):
    def __init__(self, status_code: int, message: str, http_status: int = 400):
        self.status_code, self.message, self.http_status = status_code, message, http_status


class MockError(Exception):
    pass


ERRORS = (httpx.HTTPError, MockError, KeyError, TypeError, ValueError)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ocpi(data=None, status_code=1000, message="Success", http_status=200) -> JSONResponse:
    body = {"status_code": status_code, "status_message": message, "timestamp": now()}
    if data is not None:
        body["data"] = data
    return JSONResponse(body, status_code=http_status)


@app.exception_handler(OcpiError)
async def ocpi_error(request: Request, exc: OcpiError):
    return ocpi(status_code=exc.status_code, message=exc.message, http_status=exc.http_status)


def check_auth(authorization: str | None, allow_token_a: bool = False):
    raw = (authorization or "").removeprefix("Token ").strip()
    try:
        candidates = {base64.b64decode(raw, validate=True).decode(), raw}
    except (ValueError, UnicodeDecodeError):
        candidates = {raw}
    valid = {state["our_token"]} | ({state["token_a"]} if allow_token_a else set())
    if not raw or not (candidates & (valid - {None})):
        raise OcpiError(2000, "Invalid or missing token", 401)


def record(direction, method, url, http_status, started, request_headers, request_body, response_body):
    try:
        ocpi_status = json.loads(response_body).get("status_code")
    except Exception:
        ocpi_status = None
    log.appendleft({
        "id": next(_ids), "time": now(), "direction": direction, "method": method, "url": str(url),
        "http_status": http_status, "ocpi_status": ocpi_status, "duration_ms": int((time.monotonic() - started) * 1000),
        "request_headers": dict(request_headers),  # unmasked on purpose: it's a mock
        "request_body": (request_body or b"").decode(errors="replace") if isinstance(request_body, bytes) else request_body,
        "response_body": response_body.decode(errors="replace") if isinstance(response_body, bytes) else response_body,
    })


class LoggingTransport(httpx.BaseTransport):
    def __init__(self):
        self.inner = httpx.HTTPTransport()

    def handle_request(self, request):
        started = time.monotonic()
        try:
            response = self.inner.handle_request(request)
            response.read()
        except httpx.HTTPError as e:
            record("OUT", request.method, request.url, None, started, request.headers, request.content, f"{type(e).__name__}: {e}")
            raise
        record("OUT", request.method, request.url, response.status_code, started, request.headers, request.content, response.content)
        return response


def client(token: str) -> httpx.Client:
    auth = "Token " + base64.b64encode(token.encode()).decode()
    return httpx.Client(timeout=10, headers={"Authorization": auth}, transport=LoggingTransport())


def data(response: httpx.Response):
    response.raise_for_status()
    body = response.json()
    if body.get("status_code") != 1000:
        raise MockError(f"OCPI {body.get('status_code')}: {body.get('status_message')}")
    return body.get("data")


def discover(c: httpx.Client, versions_url: str) -> list[dict]:
    match = next((v for v in data(c.get(versions_url)) if v["version"] == VERSION), None)
    if match is None:
        raise MockError(f"eMSP does not support OCPI {VERSION}")
    return data(c.get(match["url"]))["endpoints"]


def endpoint(identifier: str) -> str:
    urls = [e["url"] for e in state["endpoints"] if e["identifier"] == identifier]
    if not urls:
        raise HTTPException(400, f"No {identifier} endpoint known. Register with the eMSP first.")
    return urls[0]


def our_credentials() -> dict:
    return {
        "token": state["our_token"],
        "url": f"{BASE_URL}/ocpi/versions",
        "roles": [{"role": "CPO", "country_code": COUNTRY_CODE, "party_id": PARTY_ID, "business_details": {"name": "Mock CPMS"}}],
    }


def set_registered(token: str, versions_url: str, roles: list, endpoints: list):
    state.update(their_token=token, their_versions_url=versions_url, their_roles=roles, endpoints=endpoints,
                 registered_at=now(), token_a=None)


def clear_registration():
    state.update(our_token=None, their_token=None, their_roles=None, endpoints=[], registered_at=None)


@app.middleware("http")
async def log_ocpi(request: Request, call_next):
    if not request.url.path.startswith("/ocpi"):
        return await call_next(request)
    started = time.monotonic()
    request_body = await request.body()
    response = await call_next(request)
    response_body = b"".join([chunk async for chunk in response.body_iterator])
    record("IN", request.method, request.url, response.status_code, started, request.headers, request_body, response_body)
    return Response(response_body, status_code=response.status_code, headers=dict(response.headers))


# --- OCPI endpoints (what the eMSP calls) --------------------------------------------------------

@app.get("/ocpi/versions")
def versions(authorization: str | None = Header(None)):
    check_auth(authorization, allow_token_a=True)
    return ocpi([{"version": VERSION, "url": f"{BASE_URL}/ocpi/{VERSION}"}])


@app.get(f"/ocpi/{VERSION}")
def version_details(authorization: str | None = Header(None)):
    check_auth(authorization, allow_token_a=True)
    url = f"{BASE_URL}/ocpi/{VERSION}/credentials"
    return ocpi({"version": VERSION, "endpoints": [
        {"identifier": "credentials", "role": "SENDER", "url": url},
        {"identifier": "credentials", "role": "RECEIVER", "url": url},
    ]})


class CredentialsIn(BaseModel):
    token: str
    url: str
    roles: list[dict]


def accept_credentials(body: CredentialsIn):
    try:
        with client(body.token) as c:
            endpoints = discover(c, body.url)
    except ERRORS as e:
        raise OcpiError(3001, f"Unable to use the client's API: {e}")
    state["our_token"] = secrets.token_urlsafe(16)
    set_registered(body.token, body.url, body.roles, endpoints)
    return ocpi(our_credentials())


@app.get(f"/ocpi/{VERSION}/credentials")
def get_credentials(authorization: str | None = Header(None)):
    check_auth(authorization)
    return ocpi(our_credentials())


@app.post(f"/ocpi/{VERSION}/credentials")
def post_credentials(body: CredentialsIn, authorization: str | None = Header(None)):
    check_auth(authorization, allow_token_a=True)
    if state["registered_at"]:
        raise OcpiError(2000, "Already registered, use PUT to update", 405)
    return accept_credentials(body)


@app.put(f"/ocpi/{VERSION}/credentials")
def put_credentials(body: CredentialsIn, authorization: str | None = Header(None)):
    check_auth(authorization)
    return accept_credentials(body)


@app.delete(f"/ocpi/{VERSION}/credentials")
def delete_credentials(authorization: str | None = Header(None)):
    check_auth(authorization)
    clear_registration()
    return ocpi()


# --- Control API (what you drive from the mock's page or a script) -----------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/mock/state")
def get_state():
    return {**state, "versions_url": f"{BASE_URL}/ocpi/versions", "country_code": COUNTRY_CODE, "party_id": PARTY_ID,
            "emsp_versions_url": EMSP_VERSIONS_URL}


@app.post("/mock/token-a")
def issue_token_a():
    """A fresh token A so the eMSP can register with us. This drops any current registration."""
    clear_registration()
    state["token_a"] = secrets.token_urlsafe(16)
    return get_state()


class ConnectIn(BaseModel):
    versions_url: str
    token: str


def handshake(method: str, token: str, versions_url: str):
    """We start the handshake: POST to register, PUT to rotate."""
    previous = state["our_token"]
    state["our_token"] = secrets.token_urlsafe(16)  # set first: the eMSP fetches our versions with it
    try:
        with client(token) as c:
            endpoints = discover(c, versions_url)
            url = next(e["url"] for e in endpoints if e["identifier"] == "credentials")
            creds = data(c.request(method, url, json=our_credentials()))
            set_registered(creds["token"], creds["url"], creds["roles"], endpoints)
    except (*ERRORS, StopIteration) as e:
        state["our_token"] = previous
        raise HTTPException(502, f"Handshake failed: {e}")
    return get_state()


@app.post("/mock/connect")
def connect(body: ConnectIn):
    """Register with the eMSP using the token A it gave us."""
    if state["registered_at"]:
        raise HTTPException(400, "Already registered. Unregister first.")
    return handshake("POST", body.token, body.versions_url)


@app.post("/mock/rotate")
def rotate():
    if not state["registered_at"]:
        raise HTTPException(400, "Not registered")
    return handshake("PUT", state["their_token"], state["their_versions_url"])


@app.post("/mock/unregister")
def unregister():
    if not state["registered_at"]:
        raise HTTPException(400, "Not registered")
    try:
        with client(state["their_token"]) as c:
            data(c.delete(endpoint("credentials")))
    except ERRORS as e:
        raise HTTPException(502, f"Unregister failed: {e}")
    finally:
        clear_registration()
    return get_state()


class AuthorizeIn(BaseModel):
    uid: str
    type: str = "RFID"
    location_id: str | None = None
    evse_uids: list[str] = []
    bad_token: bool = False  # send a wrong token, to check the eMSP rejects it


def _result(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return {"http_status": r.status_code, "headers": {k: v for k, v in r.headers.items() if k.lower().startswith(("x-", "link"))}, "body": body}


@app.post("/mock/authorize")
def authorize(body: AuthorizeIn):
    """Real-time authorization, the call a charger's 'unknown RFID' triggers."""
    url = f"{endpoint('tokens')}/{quote(body.uid, safe='')}/authorize"
    location = {"location_id": body.location_id, "evse_uids": body.evse_uids or None} if body.location_id else None
    token = "not-a-valid-token" if body.bad_token else state["their_token"]
    try:
        with client(token) as c:
            return _result(c.post(url, params={"type": body.type}, json=location))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach {url}: {e}")


@app.get("/mock/tokens")
def pull_tokens(offset: int = 0, limit: int = Query(10, le=1000)):
    """Pull one page of the eMSP's token list."""
    try:
        with client(state["their_token"] or "") as c:
            return _result(c.get(endpoint("tokens"), params={"offset": offset, "limit": limit}))
    except httpx.HTTPError as e:
        raise HTTPException(502, str(e))


@app.get("/mock/log")
def get_log(after_id: int = 0):
    return [m for m in log if m["id"] > after_id]


@app.delete("/mock/log", status_code=204)
def clear_log():
    log.clear()
    return Response(status_code=204)
