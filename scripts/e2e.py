"""End-to-end check of OCPI Tokens against the mock CPMS, using only their HTTP APIs.

    python scripts/e2e.py [APP_URL] [MOCK_URL]     # defaults: http://localhost:8000 http://localhost:8001

Set ADMIN_USERNAME / ADMIN_PASSWORD if the app has Basic auth turned on.

Runs both registration directions, real-time authorize (allowed, blocked, unknown, bad token),
token list pull, credential rotation and unregistering. Cleans up after itself. Exits 1 on any failure.
"""

import os
import secrets
import sys

import httpx

APP = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
MOCK = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8001"
ADMIN_AUTH = (os.environ.get("ADMIN_USERNAME", "admin"), os.environ["ADMIN_PASSWORD"]) if os.environ.get("ADMIN_PASSWORD") else None
app = httpx.Client(base_url=APP, timeout=30, auth=ADMIN_AUTH)
mock = httpx.Client(base_url=MOCK, timeout=30)
failures = 0


def check(name: str, ok: bool, detail=""):
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def ok(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json() if response.content else {}


def authorize(uid: str, **extra) -> dict:
    return ok(mock.post("/mock/authorize", json={"uid": uid, **extra}))


def authorize_checks(label: str, valid_uid: str, blocked_uid: str):
    r = authorize(valid_uid)
    check(f"{label}: valid token is ALLOWED", r["body"].get("data", {}).get("allowed") == "ALLOWED", r)
    r = authorize(valid_uid.lower())
    check(f"{label}: valid token in lower case is ALLOWED (uid is case-insensitive)", r["body"].get("data", {}).get("allowed") == "ALLOWED", r)
    r = authorize(blocked_uid)
    check(f"{label}: invalid token is BLOCKED", r["body"].get("data", {}).get("allowed") == "BLOCKED", r)
    r = authorize("E2E-UNKNOWN-" + secrets.token_hex(4))
    check(f"{label}: unknown token is 404 / 2004", r["http_status"] == 404 and r["body"].get("status_code") == 2004, r)
    r = authorize(valid_uid, bad_token=True)
    check(f"{label}: wrong OCPI token is rejected with 401", r["http_status"] == 401, r)
    r = authorize(valid_uid, location_id="LOC1", evse_uids=["EVSE1"])
    check(f"{label}: authorize with LocationReferences body", r["body"].get("data", {}).get("allowed") == "ALLOWED", r)


def main():
    suffix = secrets.token_hex(3).upper()
    valid = ok(app.post("/api/tokens", json={"uid": f"E2E-OK-{suffix}", "contract_id": "E2E-C1", "group_id": "e2e"}))
    blocked = ok(app.post("/api/tokens", json={"uid": f"E2E-NO-{suffix}", "contract_id": "E2E-C2", "group_id": "e2e", "valid": False}))
    partner_ids = []
    try:
        # Make sure the mock is starting clean
        ok(mock.post("/mock/token-a"))

        # --- Direction 1: the CPMS (mock) registers with us using our token A
        partner = ok(app.post("/api/partners", json={"name": f"E2E mock (they connect) {suffix}"}))
        partner_ids.append(partner["id"])
        versions_url = ok(app.get("/api/info"))["versions_url"]
        state = ok(mock.post("/mock/connect", json={"versions_url": versions_url, "token": partner["token_a"]}))
        check("mock registers with app using token A", bool(state["registered_at"]))
        partner = next(p for p in ok(app.get("/api/partners")) if p["id"] == partner["id"])
        check("app shows the partner as registered", bool(partner["registered_at"]))
        check("app knows the mock's credentials endpoint", any(e["identifier"] == "credentials" for e in partner["endpoints"] or []))

        authorize_checks("direction 1", valid["uid"], blocked["uid"])

        pulled = ok(mock.get("/mock/tokens", params={"limit": 1}))
        check("mock pulls the token list with pagination headers",
              pulled["http_status"] == 200 and int(pulled["headers"].get("x-total-count", 0)) >= 2, pulled)

        test = ok(app.post(f"/api/partners/{partner['id']}/test"))
        check("app's connection test against the mock", test["ok"], test)

        ok(mock.post("/mock/rotate"))
        check("after the mock rotates credentials, authorize still works",
              authorize(valid["uid"])["body"].get("data", {}).get("allowed") == "ALLOWED")
        ok(app.post(f"/api/partners/{partner['id']}/rotate"))
        check("after the app rotates credentials, authorize still works",
              authorize(valid["uid"])["body"].get("data", {}).get("allowed") == "ALLOWED")

        ok(mock.post("/mock/unregister"))
        partner = next(p for p in ok(app.get("/api/partners")) if p["id"] == partner["id"])
        check("mock unregisters, and app shows the partner as unregistered", partner["registered_at"] is None)

        # --- Direction 2: we register with the CPMS (mock) using its token A
        mock_state = ok(mock.post("/mock/token-a"))
        r = app.post("/api/partners/connect", json={
            "name": f"E2E mock (we connect) {suffix}", "versions_url": mock_state["versions_url"], "token": mock_state["token_a"]})
        check("app registers with mock using the mock's token A", r.status_code == 201, r.text)
        if r.status_code == 201:
            partner_ids.append(r.json()["id"])
            check("mock shows itself as registered", bool(ok(mock.get("/mock/state"))["registered_at"]))
            authorize_checks("direction 2", valid["uid"], blocked["uid"])
            test = ok(app.post(f"/api/partners/{partner_ids[-1]}/test"))
            check("app's connection test against the mock", test["ok"], test)

        messages = ok(app.get("/api/messages", params={"limit": 500}))
        check("app's message log has entries in both directions", {m["direction"] for m in messages} >= {"IN", "OUT"})
    finally:
        for pid in partner_ids:
            app.delete(f"/api/partners/{pid}")
        app.delete(f"/api/tokens/{valid['id']}")
        app.delete(f"/api/tokens/{blocked['id']}")

    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
