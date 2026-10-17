"""MCP transport (stdio). Thin: authenticate, call the service, map errors. No logic of its own."""

import sys
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from qrypt_entropy.config import MCP_SERVER_SECRETS, ConfigError, get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, TemporarilyUnavailable, Unauthorized
from qrypt_entropy.logs import configure_logging, get_logger, register_secret
from qrypt_entropy.models import IssuedValue, Principal
from qrypt_entropy.service import access, issuance
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


def build_server(key_provider: Callable[[], str | None]) -> EntropyServer:
    """`key_provider` returns the client key the process was launched with; it is checked on every call."""
    server = EntropyServer("qrypt-entropy")

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

    @server.tool()
    async def get_random_bytes(length: int, encoding: Literal["hex", "base64"]) -> IssuedValue:
        return await call("get_random_bytes", lambda p: issuance.get_random_bytes(p, length, encoding))

    @server.tool()
    async def random_int(min: int, max: int) -> IssuedValue:  # noqa: A002
        return await call("random_int", lambda p: issuance.random_int(p, min, max))

    @server.tool()
    async def generate_token(
        kind: Literal["api_key", "otp_6", "uuid_v4"],
        owner: str | None = None,
        expiry_seconds: int | None = None,
    ) -> IssuedValue:
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
