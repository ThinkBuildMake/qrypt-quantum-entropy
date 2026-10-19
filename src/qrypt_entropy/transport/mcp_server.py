"""MCP transport (stdio). Thin: authenticate, call the service, map errors. No logic of its own."""

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from qrypt_entropy.config import MCP_SERVER_SECRETS, ConfigError, get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, TemporarilyUnavailable, Unauthorized
from qrypt_entropy.logs import configure_logging, get_logger, register_secret
from qrypt_entropy.models import IssuedValue, Principal
from qrypt_entropy.service import access, issuance, pool, tokens
from qrypt_entropy.service.status import default_reporter
from qrypt_entropy.storage.db import close_pools

log = get_logger("mcp")

# The role each tool allows (AZ-1). The authorization matrix reads this table.
TOOL_ROLES: dict[str, set[str]] = {
    "get_random_bytes": {"client"},
    "random_int": {"client"},
    "generate_token": {"client"},
}


class EntropyServer(MCPServer):
    """Turns every tool failure, including argument validation, into an ErrorBody (CT-2, CT-4, RL-5)."""

    async def call_tool(self, name, arguments, context=None):
        started = time.perf_counter()
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ToolError):  # a deliberate AppError body raised by our own wrapper
                raise ToolError(str(cause)) from exc
            if isinstance(cause, AppError):  # any module's anticipated failure keeps its code (CT-4)
                raise ToolError(cause.json()) from exc
            if isinstance(cause, ValidationError):
                fields = sorted({".".join(str(p) for p in e["loc"]) for e in cause.errors()})
                log.info("tool_call", tool=name, error="invalid_argument", duration_ms=_ms(started))
                issuance.record_error("invalid_argument", _ms(started))
                raise ToolError(InvalidArgument(f"invalid arguments: {', '.join(fields)}").json()) from exc
            if name not in TOOL_ROLES:
                raise ToolError(InvalidArgument("unknown tool").json()) from exc
            raise ToolError(TemporarilyUnavailable("the call could not be completed").json()) from exc


STARTUP_WAIT_S = 5.0  # how long `initialize` may wait for the startup check; clients give up after about 10 s


@asynccontextmanager
async def _pool_lifespan(_server: MCPServer) -> AsyncIterator[None]:
    """The process owns one Pool: the 1 KiB startup check runs when the server starts (DE-5), the pool stops with it.
    The server waits for the check for at most STARTUP_WAIT_S (a 429 backoff can take minutes); a slower check
    finishes in the background, and calls meanwhile wait on the pool, which has its own 2 s bound."""
    pool.reopen()
    starting = asyncio.create_task(pool.get_pool().start())
    reporter = default_reporter()  # the 5 s status report (OB-9) runs while the server does
    reporter.start()
    try:
        try:
            await asyncio.wait_for(asyncio.shield(starting), STARTUP_WAIT_S)
        except TimeoutError:
            log.warning("pool_startup_check_slow", waited_s=STARTUP_WAIT_S)
        yield
    finally:
        await reporter.stop()
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        await pool.shutdown()


def build_server(key_provider: Callable[[], str | None], *, run_pool: bool = True) -> EntropyServer:
    """`key_provider` returns the client key the process was launched with; it is checked on every call.
    `run_pool=False` leaves the process-wide Pool alone (a caller that installed its own with `pool.use_pool`)."""
    server = EntropyServer("qrypt-entropy", lifespan=_pool_lifespan if run_pool else None)

    async def call(name: str, run: Callable[[Principal], Awaitable[IssuedValue]]) -> IssuedValue:
        started = time.perf_counter()
        try:
            principal = access.authenticate(key_provider(), TOOL_ROLES[name])
            result = await run(principal)
        except AppError as err:
            log.info("tool_call", tool=name, error=err.code, duration_ms=_ms(started))
            raise ToolError(err.json()) from err
        except Exception as err:
            log.error("tool_call_crashed", tool=name, error_type=type(err).__name__, duration_ms=_ms(started))
            raise ToolError(TemporarilyUnavailable("the call could not be completed").json()) from err
        log.info("tool_call", tool=name, client_id=principal.client_id, duration_ms=_ms(started))
        return result

    settings = get_settings()
    max_bytes, max_expiry, safe = settings.per_call_max_bytes, settings.token_expiry_max_seconds, issuance.SAFE_INT
    length_f = Field(ge=1, le=max_bytes, description=f"Number of random bytes to return, 1 to {max_bytes}.")
    encoding_f = Field(description="How the bytes are written in the reply: hex or base64.")
    min_f = Field(ge=-safe, le=safe, description="Smallest integer that may be returned (inclusive).")
    max_f = Field(ge=-safe, le=safe, description="Largest integer that may be returned (inclusive), not below min.")
    kind_f = Field(
        description="Kind of token: api_key (64 hex characters), otp_6 (six digits) or uuid_v4 (a version 4 UUID)."
    )
    owner_f = Field(
        max_length=tokens.MAX_OWNER_LENGTH,
        description="Who the token is for. When given, only a keyed hash of the token is stored, so the owner can "
        "redeem it once before it expires. Omit for a token that is not stored.",
    )
    expiry_f = Field(
        ge=1,
        le=max_expiry,
        description=f"Seconds until the stored token expires, 1 to {max_expiry}. Needs owner; defaults if omitted.",
    )

    @server.tool()
    async def get_random_bytes(
        length: Annotated[int, length_f], encoding: Annotated[Literal["hex", "base64"], encoding_f]
    ) -> IssuedValue:
        """Return cryptographically strong random bytes from pooled Qrypt quantum entropy.

        Each byte is served once and never again. The reply carries the encoded value and metadata
        (source, batch id, bytes consumed) that ties the call to the audit log.
        """
        return await call("get_random_bytes", lambda p: issuance.get_random_bytes(p, length, encoding))

    @server.tool()
    async def random_int(min: Annotated[int, min_f], max: Annotated[int, max_f]) -> IssuedValue:  # noqa: A002
        """Return a uniformly distributed random integer between min and max, both included.

        The integer is drawn from pooled Qrypt entropy with rejection sampling, so there is no modulo bias.
        Both bounds must lie within plus or minus 2^53 - 1.
        """
        return await call("random_int", lambda p: issuance.random_int(p, min, max))

    @server.tool()
    async def generate_token(
        kind: Annotated[Literal["api_key", "otp_6", "uuid_v4"], kind_f],
        owner: Annotated[str | None, owner_f] = None,
        expiry_seconds: Annotated[int | None, expiry_f] = None,
    ) -> IssuedValue:
        """Generate a token from pooled Qrypt entropy: an API key, a six-digit one-time code or a UUID v4.

        With an owner the token is stored hashed with an expiry and can be redeemed once; the audit log
        never holds the token itself.
        """
        return await call("generate_token", lambda p: issuance.generate_token(p, kind, owner, expiry_seconds))

    return server


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_file or None)
    for secret in settings.secret_values():
        register_secret(secret)
    try:
        settings.require(MCP_SERVER_SECRETS)
        settings.bind_role("mcp_server")  # not the owner string; prod refuses to start without MCP_SERVER_URL
    except ConfigError as err:
        log.error("start_refused", cause=str(err))
        return 1
    try:
        if not settings.mcp_client_key:
            raise Unauthorized("no client key: set MCP_CLIENT_KEY in the server's environment")
        access.authenticate(settings.mcp_client_key, {"client"})
    except AppError as err:
        log.error("connect_refused", error=err.code, cause=err.message)
        return 1
    log.info("server_started", env=settings.app_env)
    try:
        build_server(lambda: get_settings().mcp_client_key).run("stdio")
    finally:
        access.close_pools()  # a pool left open prints a traceback at exit, and a log line must always be JSON
        close_pools()
    return 0


if __name__ == "__main__":
    sys.exit(main())
