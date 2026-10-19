"""HTTP transport (FastAPI). Thin: authenticate, validate, call the service, map errors.

Routes authenticate with the real Access (S20); usage, budget and client routes use the real Meter and Access (S34);
redeeming and the issuance trace are real (S21-S23, wired by S33). `/health` answers from a stub until S35.
"""

import re
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, StrictInt
from starlette.exceptions import HTTPException as StarletteHTTPException

from qrypt_entropy.config import HTTP_SERVICE_SECRETS, get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, NotFound, TemporarilyUnavailable
from qrypt_entropy.logs import configure_logging, get_logger, register_secret
from qrypt_entropy.models import Principal
from qrypt_entropy.service import access, issuance, meter, status, tokens

configure_logging(get_settings().log_level)
for _secret in get_settings().secret_values():
    register_secret(_secret)
log = get_logger("http")
_BEARER = re.compile(r"^Bearer (\S+)$")
REFUSAL_STATUS = {"unknown": 404, "already_used": 409, "expired": 410, "rate_limited": 429}


def require_roles(*roles: str) -> Callable:
    """Dependency that authenticates the bearer key. `allowed_roles` lets the matrix test read the policy."""

    def dependency(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
        match = _BEARER.match(authorization or "")
        principal = access.authenticate(match.group(1) if match else None, set(roles))
        request.state.client_id = principal.client_id
        return principal

    dependency.allowed_roles = set(roles)  # type: ignore[attr-defined]
    return dependency


class VerifyBody(BaseModel):
    owner: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00]*$")  # Postgres text cannot hold NUL
    value: str = Field(min_length=1, max_length=4096)


class BudgetBody(BaseModel):
    capKib: StrictInt = Field(ge=0, le=2**63 - 1)  # noqa: N815  (the column is a bigint)


class ClientBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    get_settings().require(HTTP_SERVICE_SECRETS)  # a missing secret stops the start, naming the variable
    get_settings().bind_role("http_service")  # the hosted service never runs on the owner connection string
    yield


app = FastAPI(title="Qrypt entropy service", openapi_url=None, docs_url=None, redoc_url=None, lifespan=_lifespan)


@app.middleware("http")
async def _log_request(request: Request, call_next):
    status = 500  # an unhandled exception still leaves its request line
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        fields = {"client_id": request.state.client_id} if hasattr(request.state, "client_id") else {}
        log.info("request", method=request.method, path=request.url.path, status=status, **fields)


@app.exception_handler(AppError)
async def _app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.http_status)


@app.exception_handler(RequestValidationError)
async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()})
    err = InvalidArgument(f"invalid input: {', '.join(fields)}")  # field names only, never values
    return JSONResponse(err.body(), status_code=400)


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "invalid_argument"}.get(exc.status_code, "invalid_argument")
    return JSONResponse({"error": code, "message": "no such route or method"}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _crash(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled", error_type=type(exc).__name__)
    err = TemporarilyUnavailable("the request could not be completed")
    return JSONResponse(err.body(), status_code=503)


@app.get("/health")
def health() -> dict:
    return status.get_health()


@app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(require_roles("admin"))])
def metrics() -> str:
    return status.get_metrics()


@app.post("/verify", dependencies=[Depends(require_roles("client", "admin"))])
def verify(body: VerifyBody) -> JSONResponse:
    outcome = tokens.redeem(body.owner, body.value)
    if outcome == "redeemed":
        return JSONResponse({"outcome": outcome})
    refusal = {"error": outcome, "message": "the code was not redeemed", "outcome": outcome}
    return JSONResponse(refusal, status_code=REFUSAL_STATUS[outcome])


@app.get("/admin/usage", dependencies=[Depends(require_roles("admin"))])
def usage() -> dict:
    return meter.get_usage()


@app.put("/admin/budget", dependencies=[Depends(require_roles("admin"))])
def set_budget(body: BudgetBody) -> dict:
    return meter.set_budget(body.capKib)


@app.get("/admin/issuances/{reference}", dependencies=[Depends(require_roles("admin"))])
def get_issuance(reference: str) -> dict:
    try:
        parsed = uuid.UUID(reference)
    except ValueError:
        raise InvalidArgument("reference must be a uuid") from None
    return issuance.get_issuance(str(parsed))


@app.post("/admin/clients", status_code=201, dependencies=[Depends(require_roles("admin"))])
def create_client(body: ClientBody) -> dict:
    return access.create_client(body.name)


@app.post("/admin/clients/{client_id}/revoke", dependencies=[Depends(require_roles("admin"))])
def revoke_client(client_id: str) -> dict:
    try:
        parsed = uuid.UUID(client_id)
    except ValueError:
        raise NotFound("no such client") from None
    return access.revoke_client(str(parsed))


@app.post("/admin/trace-token", dependencies=[Depends(require_roles("admin"))])
def trace_token(body: VerifyBody) -> dict:
    """F5b: an owner and a token value in, the issuance provenance out. The value is hashed and never echoed."""
    return issuance.get_issuance(tokens.find_issuance(body.owner, body.value))
