"""Entropy source: the swappable upstream client (CH-1). Owns nothing.

`fetch` asks the active source for bytes and raises on any failure; no other source is ever tried (RL-2, FR-6.1).
One fetch can take about 2 minutes of backoff, so callers wait on the Pool (2 s bound, RL-1), never on fetch.
A 429 is retried with jittered exponential backoff (DE-3) and every HTTP request, retries included, passes the
30-per-10-s limiter (DE-1). Nothing here logs the token or any returned byte.
"""

import asyncio
import base64
import binascii
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AllotmentUsed, InvalidArgument, Misconfigured, RateLimited, Unavailable
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import Fetched

log = get_logger("qrypt_entropy.upstream")
MIN_KIB, MAX_KIB, WINDOW_S = 1, 512, 10.0
LIMIT = 30
BACKOFF_FIRST_S, BACKOFF_CAP_S, MAX_RETRIES = 0.5, 60.0, 8


Sleep = Callable[[float], Awaitable[None]]


class Source(Protocol):
    label: str

    async def fetch(self, size_kib: int) -> Fetched: ...

    def request_stats(self) -> dict: ...


def _check_size(size_kib: int) -> None:
    if isinstance(size_kib, bool) or not isinstance(size_kib, int) or not MIN_KIB <= size_kib <= MAX_KIB:
        raise InvalidArgument("size must be 1 to 512 KiB")


class _Requests:
    """Requests made: feeds requestStats and enforces at most 30 per 10 s (DE-1).

    A slot is held while a request is in flight and is stamped when the request comes back, so a new request
    cannot start until 10 s after the 30th-previous one finished, which is after the server saw it. No margin needed.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Sleep = asyncio.sleep) -> None:
        self._clock, self._sleep = clock, sleep
        self._done: deque[float] = deque()  # completion times within the window
        self._in_flight = 0
        self._waiters: list[asyncio.Future[None]] = []

    def _prune(self) -> None:
        while self._done and self._clock() - self._done[0] >= WINDOW_S:
            self._done.popleft()

    def record(self) -> None:
        """A request that needs no slot (the stub source)."""
        self._done.append(self._clock())

    def last_10s(self) -> int:
        self._prune()
        return self._in_flight + len(self._done)

    async def acquire(self) -> None:
        """Wait until a request may start, and take its slot. Pair with release()."""
        while True:
            self._prune()
            if self._in_flight + len(self._done) < LIMIT:
                self._in_flight += 1
                return
            if self._done:
                await self._sleep(self._done[0] + WINDOW_S - self._clock())
            else:  # all 30 are in flight: wait for one to come back
                waiter = asyncio.get_running_loop().create_future()
                self._waiters.append(waiter)
                await waiter

    def release(self) -> None:
        self._in_flight -= 1
        self._done.append(self._clock())
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()


class QryptSource:
    label = "qrypt"

    def __init__(
        self,
        base_url: str,
        token: str | None,
        timeout_s: float = 5.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._base_url, self._token, self._timeout_s = base_url.rstrip("/"), token, timeout_s
        self._http: httpx.AsyncClient | None = None  # one client per event loop, made on first use
        self._http_loop: asyncio.AbstractEventLoop | None = None
        self._requests = _Requests(clock, sleep)
        self._sleep, self._rng = sleep, rng or random.Random()  # noqa: S311 (jitter, not security)

    def request_stats(self) -> dict:
        return {"requestsLast10s": self._requests.last_10s()}

    async def fetch(self, size_kib: int) -> Fetched:
        _check_size(size_kib)
        if not self._token:
            log.error("upstream_misconfigured", cause="QRYPT_API_KEY is not set")
            raise Misconfigured("Qrypt access token is not configured")
        for attempt in range(1, MAX_RETRIES + 2):
            reply = await self._get(size_kib)
            if reply.status_code != 429:
                return self._interpret(reply, size_kib)
            if attempt > MAX_RETRIES:
                break
            nominal = min(BACKOFF_FIRST_S * 2 ** (attempt - 1), BACKOFF_CAP_S)
            wait = nominal * self._rng.uniform(0.5, 1.0)
            log.warning("upstream_rate_limited", attempt=attempt, wait_s=round(wait, 3))
            await self._sleep(wait)
        log.error("upstream_rate_limited_exhausted", attempt=attempt)
        raise RateLimited("entropy source is rate limiting")

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._http is None or self._http_loop is not loop:  # never reuse a client bound to a finished loop
            self._http, self._http_loop = httpx.AsyncClient(timeout=self._timeout_s), loop
        return self._http

    async def _get(self, size_kib: int) -> httpx.Response:
        await self._requests.acquire()
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._client().get(
                    f"{self._base_url}/api/v1/quantum-entropy",
                    params={"size": size_kib},
                    headers={"Accept": "application/json", "Authorization": f"Bearer {self._token}"},
                )
        except (TimeoutError, httpx.HTTPError) as exc:
            log.warning("upstream_unavailable", size_kib=size_kib, reason=type(exc).__name__)
            raise Unavailable("entropy source did not answer") from None
        finally:
            self._requests.release()

    def _interpret(self, reply: httpx.Response, size_kib: int) -> Fetched:
        status = reply.status_code
        if status == 401:
            log.error("upstream_misconfigured", cause="Qrypt access token is invalid or expired", status=status)
            raise Misconfigured("Qrypt access token is invalid or expired")
        if status == 403:
            log.error("upstream_allotment_used", status=status)
            raise AllotmentUsed("Qrypt entropy allotment is used")
        if status != 200:
            log.warning("upstream_unavailable", size_kib=size_kib, status=status)
            raise Unavailable(f"entropy source answered {status}")
        try:
            data = b"".join(base64.b64decode(block, validate=True) for block in reply.json()["random"])
        except ValueError, KeyError, TypeError, binascii.Error:
            data = b""
        if len(data) != size_kib * 1024:
            log.warning("upstream_unavailable", size_kib=size_kib, reason="malformed reply")
            raise Unavailable("entropy source sent a malformed reply")
        return Fetched(self.label, data, datetime.now(UTC).isoformat())


class TestSource:
    """Second source for CH-1: deterministic bytes under the label test_source."""

    __test__ = False
    label = "test_source"

    def __init__(self) -> None:
        self._requests = _Requests()

    async def fetch(self, size_kib: int) -> Fetched:
        _check_size(size_kib)
        self._requests.record()
        return Fetched(self.label, bytes(range(256)) * 4 * size_kib, datetime.now(UTC).isoformat())

    def request_stats(self) -> dict:
        return {"requestsLast10s": self._requests.last_10s()}


_active: Source | None = None


def use_source(source: Source | None) -> None:
    """Swap the active source; None returns to Qrypt built from the settings."""
    global _active
    _active = source


def _source() -> Source:
    global _active
    if _active is None:
        s = get_settings()
        token = s.qrypt_api_token.get_secret_value() if s.qrypt_api_token else None
        _active = QryptSource(s.qrypt_base_url, token, s.qrypt_timeout_seconds)
    return _active


async def fetch(size_kib: int) -> Fetched:
    _check_size(size_kib)
    return await _source().fetch(size_kib)


def request_stats() -> dict:
    return _source().request_stats()
