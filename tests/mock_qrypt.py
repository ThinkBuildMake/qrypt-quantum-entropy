"""Committed mock of the Qrypt entropy service (docs/Qrypt - Quantum Entropy MCP Server.md).

A real local HTTP server so timeouts and dropped connections behave like the network. It answers in Qrypt's shape,
enforces 30 requests per 10 s (429), checks size 1 to 512 (400), adds latency, injects a fault from a queue, and
records every request. Entropy here is deterministic and synthetic.

Also runnable on its own (`make mock-qrypt`, S20) so QRYPT_BASE_URL can point at it. While running,
`GET /__mock/status?code=503` makes every later request answer that status (`code=200` restores normal service);
control requests are not logged.
"""

import argparse
import asyncio
import base64
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

LIMIT, WINDOW_S = 30, 10.0
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 429: "Too Many Requests",
           500: "Internal Server Error", 503: "Service Unavailable"}  # fmt: skip


@dataclass(frozen=True)
class Logged:
    at: float
    path: str
    size: str | None
    authorization: str | None
    status: int | None  # None: connection dropped


class MockQrypt:
    def __init__(
        self,
        token: str = "mock-token-0123456789",  # noqa: S107
        clock: Callable[[], float] = time.monotonic,
        port: int = 0,
        enforce_limit: bool = True,
    ) -> None:
        self.token = token
        self.enforce_limit = enforce_limit  # a mock shared by many test processes cannot honour the per-client limit
        self.sticky: int | None = None  # every request answers this status while it is set
        self.clock = clock  # a test may scale time; the 10 s window and the log use it
        self.log: list[Logged] = []
        self.faults: list[int | str] = []  # consumed one per request: a status code or "drop"
        self.delay_s = 0.0
        self.override: Callable[[int], int | None] | None = None  # request index -> status in place of a 200
        self._server: asyncio.Server | None = None
        self.port = port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def __aenter__(self) -> MockQrypt:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    def sizes(self) -> list[str | None]:
        return [r.size for r in self.log]

    def _decide(self, size: str | None, auth: str | None) -> int | str:
        if self.faults:
            return self.faults.pop(0)
        if self.sticky:
            return self.sticky
        now = self.clock()
        if self.enforce_limit and sum(1 for r in self.log[:-1] if now - r.at < WINDOW_S) >= LIMIT:
            return 429
        if auth != f"Bearer {self.token}":
            return 401
        if size is None or not size.isdigit() or not 1 <= int(size) <= 512:
            return 400
        forced = self.override(len(self.log) - 1) if self.override else None
        return forced or 200

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
            target = head[0].split(" ")[1]
            headers = {k.lower(): v for k, _, v in (line.partition(": ") for line in head[1:] if line)}
            parts = urlsplit(target)
            size = (parse_qs(parts.query).get("size") or [None])[0]
            auth = headers.get("authorization")
            if parts.path == "/__mock/status":
                code = (parse_qs(parts.query).get("code") or ["200"])[0]
                self.sticky = int(code) if code.isdigit() and int(code) != 200 else None
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            entry = Logged(self.clock(), parts.path, size, auth, None)
            index = len(self.log)
            self.log.append(entry)
            outcome = self._decide(size, auth)
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if outcome == "drop":
                writer.transport.abort()
                return
            body = {"error": REASONS.get(outcome, "error")}
            if outcome == 200:
                block = base64.b64encode(bytes(range(256)) * 4).decode()
                body = {"random": [block] * int(size), "size": int(size)}
            self.log[index] = Logged(entry.at, entry.path, size, auth, int(outcome))
            data = json.dumps(body).encode()
            writer.write(
                f"HTTP/1.1 {outcome} {REASONS.get(outcome, 'Error')}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        except ConnectionError, asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()


async def serve(port: int, token: str) -> None:
    async with MockQrypt(token, port=port):
        print(f"mock Qrypt listening on http://127.0.0.1:{port} (token {token})", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local mock of the Qrypt entropy service")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--token", default=os.environ.get("MOCK_QRYPT_TOKEN", "mock-token-0123456789"))
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.port, args.token))
    except KeyboardInterrupt:
        pass
