# OCPI Tokens

A minimal OCPI 2.2.1 **eMSP** that holds a list of RFID tokens and answers real-time
authorization requests from a CPMS. See [PROJECT_BREIF.md](PROJECT_BREIF.md) for the background.

Stack: FastAPI, SQLAlchemy 2, Postgres, and one static HTML page using Alpine.js and Pico.css.

## Run

```bash
docker compose up --build        # app on http://localhost:8000 (API docs on /docs), mock CPMS on http://localhost:8001
```

Or run locally against the compose Postgres:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d db
uvicorn app.main:app --reload
uvicorn mock_cpms.main:app --port 8001    # in a second terminal
pytest
python scripts/e2e.py                     # full end-to-end check against the mock (both must be running)
```

## Layout

| Path | What |
|---|---|
| `app/ocpi/` | OCPI endpoints: `versions`, `credentials` (handshake), `tokens` (list + real-time authorize) |
| `app/api/` | Admin JSON API: token CRUD with filters and pagination, plus partners |
| `app/static/index.html` | Admin UI, which calls `/api` |
| `app/models.py` | `Token`, `Partner` and `OcpiMessage` tables (created on startup, no migrations yet) |
| `mock_cpms/` | Mock CPMS (an OCPI CPO) with its own credentials, used for end-to-end testing |
| `scripts/e2e.py` | Drives the app and the mock through every flow, and prints PASS/FAIL |

## Admin auth

Set `ADMIN_PASSWORD` (and optionally `ADMIN_USERNAME`, default `admin`) to put HTTP Basic auth on the UI,
`/api` and `/docs`. The browser prompts once and re-sends the credentials on the UI's `/api` calls.
`/ocpi/*` is left out, since CPMSs authenticate there with their OCPI tokens. Use HTTPS in front of it,
because Basic auth sends the password in every request.

## Connecting a CPMS

Either side can start the credentials handshake. Both options are on the **Partners** tab.

- **The CPMS connects to us.** Issue a token A, then send the CPMS that token and `BASE_URL/ocpi/versions`.
  They `POST /ocpi/2.2.1/credentials`, we fetch their versions with their token, and we return ours.
- **We connect to the CPMS.** Enter their versions URL and the token A they gave you. We fetch their
  versions, then `POST` our credentials. If that fails, the partner is kept so you can retry with **Register**.

Partner actions:

- **Test:** calls their versions, version details and credentials endpoints.
- **Rotate:** `PUT`s new credentials to them.
- **Delete:** unregisters with them, then removes the partner locally.
- **Test authorize:** sends the real `POST /ocpi/2.2.1/tokens/{uid}/authorize` to our own `BASE_URL`
  using a partner's token, the same call the CPMS makes. The reply is `ALLOWED` when the token is valid,
  `BLOCKED` when it is invalid, and HTTP 404 / `2004 Unknown Token` when it is not found.

## Message log

Every OCPI request in either direction goes to stdout (logger `ocpi`) and to the `ocpi_messages` table:
headers, bodies (truncated at 10k characters), HTTP and OCPI status, and timing. Authorization headers are masked.
The **Live log** tab polls `/api/messages` and shows it as it happens. Click a row to see the full exchange.
Nothing prunes the table yet, so use **Clear log**.

## Mock CPMS

`mock_cpms/` is a separate, minimal OCPI 2.2.1 CPO (party `GB-CPO`). It has its own versions and
credentials endpoints and its own tokens. It keeps state in memory, so restarting it resets it.
Open http://localhost:8001 to control it:

- **Register, either way:** copy its versions URL and token A into the app's "We connect to the CPMS" form,
  or paste a token A issued by the app into the mock's "Connect" form.
- **Authorize:** send `POST …/tokens/{uid}/authorize` for any UID and type, with an optional location.
  Tick "invalid token" to check that the app rejects a bad OCPI token.
- **Pull the token list**, **rotate credentials** or **unregister**.
- **Log:** a live view of the mock's own inbound and outbound messages, with full headers
  (unmasked, since it's a mock).

`scripts/e2e.py` automates all of this. The script cleans up after itself, but it resets the mock's registration.

Settings: `MOCK_BASE_URL` (how the app reaches the mock), `MOCK_EMSP_VERSIONS_URL` (prefills the connect
form), `MOCK_COUNTRY_CODE`, `MOCK_PARTY_ID`. In compose, the app and the mock reach each other as
`http://app:8000` and `http://mock-cpms:8001`, which is why compose sets `BASE_URL` for the app.

## Authentication

The admin UI and `/api/*` are expected to sit behind MS Entra at the proxy or platform level.
**`/ocpi/*` must be excluded from Entra.** The CPMS authenticates with OCPI `Authorization: Token ...`
headers, and the app checks those itself.
