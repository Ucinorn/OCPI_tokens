import os

os.environ["DATABASE_URL"] = "sqlite://"

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ocpi import credentials
from app.ocpi.client import TokenAuth
from app.ocpi.common import encode_token


@pytest.fixture
def client(monkeypatch):
    # Don't call out to a real CPMS during the handshake.
    monkeypatch.setattr(credentials, "discover", lambda c, url: [])
    with TestClient(app) as c:
        yield c


def register(client) -> str:
    """Run the credentials handshake and return token C."""
    token_a = client.post("/api/partners", json={"name": "CPMS"}).json()["token_a"]
    assert client.get("/ocpi/versions", headers={"Authorization": encode_token(token_a)}).status_code == 200
    res = client.post(
        "/ocpi/2.2.1/credentials",
        headers={"Authorization": encode_token(token_a)},
        json={"token": "their-token-b", "url": "https://cpms.example/ocpi/versions", "roles": []},
    )
    assert res.json()["status_code"] == 1000
    # token A is single use
    assert client.get("/ocpi/versions", headers={"Authorization": encode_token(token_a)}).status_code == 401
    return res.json()["data"]["token"]


def test_token_crud(client):
    created = client.post("/api/tokens", json={"uid": "CRUD1", "contract_id": "GB-XXX-C1", "group_id": "fleet"})
    assert created.status_code == 201
    token_id = created.json()["id"]
    assert client.post("/api/tokens", json={"uid": "CRUD1", "contract_id": "dup"}).status_code == 409
    assert client.post("/api/tokens", json={"uid": "crud1", "contract_id": "dup"}).status_code == 409  # uid ignores case
    assert client.patch(f"/api/tokens/{token_id}", json={"valid": False}).json()["valid"] is False
    assert client.get("/api/tokens", params={"q": "CRU"}).json()["total"] == 1
    assert "fleet" in client.get("/api/tokens/groups").json()
    assert client.delete(f"/api/tokens/{token_id}").status_code == 204


def test_ocpi_requires_auth(client):
    res = client.post("/ocpi/2.2.1/tokens/ANY/authorize")
    assert res.status_code == 401


def test_accepts_raw_and_base64_tokens(client):
    token_a = client.post("/api/partners", json={"name": "CPMS"}).json()["token_a"]
    assert client.get("/ocpi/versions", headers={"Authorization": f"Token {token_a}"}).status_code == 200
    assert client.get("/ocpi/versions", headers={"Authorization": encode_token(token_a)}).status_code == 200
    token_c = client.post(
        "/ocpi/2.2.1/credentials",
        headers={"Authorization": f"Token {token_a}"},
        json={"token": "their-token-b", "url": "https://cpms.example/ocpi/versions", "roles": []},
    ).json()["data"]["token"]
    assert client.get("/ocpi/2.2.1/credentials", headers={"Authorization": f"Token {token_c}"}).status_code == 200
    assert client.get("/ocpi/2.2.1/credentials", headers={"Authorization": encode_token(token_c)}).status_code == 200


def test_outbound_retries_with_raw_token():
    sent = []

    def handler(request):
        sent.append(request.headers["Authorization"])
        return httpx.Response(200 if request.headers["Authorization"] == "Token raw-token" else 401)

    with httpx.Client(auth=TokenAuth("raw-token"), transport=httpx.MockTransport(handler)) as c:
        assert c.get("https://cpms.example/versions").status_code == 200
        assert c.get("https://cpms.example/2.2.1").status_code == 200
    assert sent == [encode_token("raw-token"), "Token raw-token", "Token raw-token"]


def test_realtime_authorize(client):
    auth = {"Authorization": encode_token(register(client))}
    client.post("/api/tokens", json={"uid": "AUTH-OK", "contract_id": "C1"})
    client.post("/api/tokens", json={"uid": "AUTH-BLOCKED", "contract_id": "C2", "valid": False})

    ok = client.post("/ocpi/2.2.1/tokens/AUTH-OK/authorize?type=RFID", headers=auth).json()
    assert ok["data"]["allowed"] == "ALLOWED"
    assert ok["data"]["token"]["uid"] == "AUTH-OK"

    # uid is a case-insensitive CiString: a lower-case read matches, and we return the uid as stored
    lower = client.post("/ocpi/2.2.1/tokens/auth-ok/authorize", headers=auth).json()
    assert lower["data"]["allowed"] == "ALLOWED"
    assert lower["data"]["token"]["uid"] == "AUTH-OK"

    blocked = client.post("/ocpi/2.2.1/tokens/AUTH-BLOCKED/authorize", headers=auth).json()
    assert blocked["data"]["allowed"] == "BLOCKED"

    unknown = client.post("/ocpi/2.2.1/tokens/NOPE/authorize", headers=auth)
    assert unknown.status_code == 404
    assert unknown.json()["status_code"] == 2004

    listing = client.get("/ocpi/2.2.1/tokens", headers=auth, params={"limit": 1})
    assert listing.headers["X-Total-Count"] == "2"
    assert 'rel="next"' in listing.headers["Link"]


def test_messages_are_logged(client):
    client.delete("/api/messages")
    auth = {"Authorization": encode_token(register(client)), "X-Request-ID": "req-1"}
    res = client.post("/ocpi/2.2.1/tokens/NOPE/authorize", headers=auth)
    assert res.headers["X-Request-ID"] == "req-1"

    last = client.get("/api/messages").json()[-1]
    assert last["direction"] == "IN"
    assert last["http_status"] == 404 and last["ocpi_status"] == 2004
    assert last["partner_id"] is not None
    assert last["request_headers"]["authorization"].endswith("…")  # token is masked

    newer = client.get("/api/messages", params={"after_id": last["id"]}).json()
    assert newer == []


def test_admin_basic_auth(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_password", "secret")
    for path in ("/", "/api/tokens", "/docs"):
        res = client.get(path)
        assert res.status_code == 401
        assert res.headers["WWW-Authenticate"].startswith("Basic")
    assert client.get("/api/tokens", auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/tokens", headers={"Authorization": "Basic !!!"}).status_code == 401
    assert client.get("/api/tokens", auth=("admin", "secret")).status_code == 200
    assert client.get("/", auth=("admin", "secret")).status_code == 200
    # OCPI keeps its own token auth: no Basic challenge there
    assert "WWW-Authenticate" not in client.get("/ocpi/versions").headers
