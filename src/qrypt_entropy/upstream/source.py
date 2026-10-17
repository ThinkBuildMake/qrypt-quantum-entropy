"""Entropy source: the swappable upstream client (CH-1). Owns nothing.

`fetch` asks the active source for bytes and raises on any failure; no other source is ever tried (RL-2, FR-6.1).
Retry, backoff and the 30-per-10-s limit are S09's. Nothing here logs the token or any returned byte.
"""

import asyncio
import base64
import binascii
import time
from collections import deque
from datetime import UTC, datetime
from typing import Protocol

import httpx

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AllotmentUsed, InvalidArgument, Misconfigured, Unavailable
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import Fetched

log = get_logger("qrypt_entropy.upstream")
MIN_KIB, MAX_KIB, WINDOW_S = 1, 512, 10.0


class Source(Protocol):
    label: str

    async def fetch(self, size_kib: int) -> Fetched: ...

    def request_stats(self) -> dict: ...


def _check_size(size_kib: int) -> None:
    if isinstance(size_kib, bool) or not isinstance(size_kib, int) or not MIN_KIB <= size_kib <= MAX_KIB:
        raise InvalidArgument("size must be 1 to 512 KiB")


class _Requests:
    """Timestamps of HTTP requests made, for requestStats."""

    def __init__(self) -> None:
        self._times: deque[float] = deque()

    def record(self) -> None:
        self._times.append(time.monotonic())

    def last_10s(self) -> int:
        while self._times and time.monotonic() - self._times[0] >= WINDOW_S:
            self._times.popleft()
        return len(self._times)


class QryptSource:
    label = "qrypt"

    def __init__(self, base_url: str, token: str | None, timeout_s: float = 5.0) -> None:
        self._base_url, self._token, self._timeout_s = base_url.rstrip("/"), token, timeout_s
        self._requests = _Requests()

    def request_stats(self) -> dict:
        return {"requestsLast10s": self._requests.last_10s()}

    async def fetch(self, size_kib: int) -> Fetched:
        _check_size(size_kib)
        if not self._token:
            log.error("upstream_misconfigured", cause="QRYPT_API_TOKEN is not set")
            raise Misconfigured("Qrypt access token is not configured")
        self._requests.record()
        try:
            async with asyncio.timeout(self._timeout_s), httpx.AsyncClient(timeout=self._timeout_s) as client:
                reply = await client.get(
                    f"{self._base_url}/api/v1/quantum-entropy",
                    params={"size": size_kib},
                    headers={"Accept": "application/json", "Authorization": f"Bearer {self._token}"},
                )
        except (TimeoutError, httpx.HTTPError) as exc:
            log.warning("upstream_unavailable", size_kib=size_kib, reason=type(exc).__name__)
            raise Unavailable("entropy source did not answer") from None
        return self._interpret(reply, size_kib)

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
