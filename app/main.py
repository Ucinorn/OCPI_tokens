import base64
import binascii
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from app.api import messages, partners, tokens
from app.db import Base, engine
from app.ocpi import credentials, versions
from app.ocpi import tokens as ocpi_tokens
from app.config import settings
from app.ocpi.common import OcpiError, base_url, ocpi_response
from app.ocpi.log import record

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # POC: no migrations yet
    yield


app = FastAPI(title="OCPI Tokens", lifespan=lifespan)

for module in (versions, credentials, ocpi_tokens, tokens, partners, messages):
    app.include_router(module.router)


@app.exception_handler(OcpiError)
async def ocpi_error(request: Request, exc: OcpiError):
    return ocpi_response(status_code=exc.status_code, message=exc.message, http_status=exc.http_status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/ocpi"):
        return ocpi_response(status_code=2001, message=str(exc.errors()), http_status=400)
    return await request_validation_exception_handler(request, exc)


def _basic_auth_ok(header: str) -> bool:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        username, _, password = base64.b64decode(encoded).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return (secrets.compare_digest(username.encode(), settings.admin_username.encode())
            & secrets.compare_digest(password.encode(), settings.admin_password.encode()))


@app.middleware("http")
async def admin_auth(request: Request, call_next):
    """HTTP Basic auth on everything except /ocpi, which CPMSs reach with their OCPI tokens."""
    if not settings.admin_password or request.url.path.startswith("/ocpi"):
        return await call_next(request)
    if not _basic_auth_ok(request.headers.get("Authorization", "")):
        return Response("Unauthorized", status_code=401, headers={"WWW-Authenticate": 'Basic realm="OCPI Tokens"'})
    return await call_next(request)


@app.middleware("http")
async def log_ocpi(request: Request, call_next):
    """Log every inbound OCPI request and response, and echo the OCPI tracing headers."""
    if not request.url.path.startswith("/ocpi"):
        return await call_next(request)
    start = time.monotonic()
    request_body = await request.body()
    log = dict(direction="IN", method=request.method, url=request.url, request_headers=request.headers, request_body=request_body)
    try:
        response = await call_next(request)
    except Exception as e:
        await run_in_threadpool(record, http_status=500, duration_ms=int((time.monotonic() - start) * 1000),
                                response_body=f"{type(e).__name__}: {e}", **log)
        raise
    response_body = b"".join([chunk async for chunk in response.body_iterator])
    headers = dict(response.headers)
    for header in ("X-Request-ID", "X-Correlation-ID"):
        if header in request.headers:
            headers[header] = request.headers[header]
    await run_in_threadpool(record, http_status=response.status_code, duration_ms=int((time.monotonic() - start) * 1000),
                            partner_id=getattr(request.state, "partner_id", None), response_body=response_body, **log)
    return Response(response_body, status_code=response.status_code, headers=headers)


@app.get("/api/info", tags=["info"])
def info():
    """What to give a CPMS so it can reach us."""
    return {"versions_url": f"{base_url()}/versions", "country_code": settings.country_code, "party_id": settings.party_id}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
