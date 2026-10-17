"""Storage layer: the shared connection layer. Nothing here imports a higher layer.

One pool per database URL, checked before every use so it reconnects after a database restart (RL-7). A database
that cannot be reached surfaces as `TemporarilyUnavailable`, never as a driver error, so no caller can return a value
for a record it could not write (RL-6).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import TemporarilyUnavailable
from qrypt_entropy.logs import get_logger

POOL_WAIT_SECONDS = 5.0  # how long a call waits for a usable connection before failing plainly
_log = get_logger("qrypt_entropy.db")
_pools: dict[str, ConnectionPool] = {}
_lock = Lock()


def open_pool(url: str | None = None, **kwargs) -> ConnectionPool:
    """A pool that checks connections before use so it survives a database restart (RL-7)."""
    kwargs.setdefault("timeout", POOL_WAIT_SECONDS)
    # A half-open connection (server gone without a FIN) must not outlast the fail-fast wait.
    kwargs.setdefault(
        "kwargs",
        {
            "connect_timeout": 5,
            "keepalives": 1,
            "keepalives_idle": 5,
            "keepalives_interval": 2,
            "keepalives_count": 2,
            "tcp_user_timeout": 5000,
        },
    )
    return ConnectionPool(
        url or get_settings().database_url, check=ConnectionPool.check_connection, open=True, **kwargs
    )


def get_pool(url: str | None = None) -> ConnectionPool:
    """The process-wide pool for `url` (default: the configured database), opened on first use."""
    url = url or get_settings().database_url
    with _lock:
        if url not in _pools:
            _pools[url] = open_pool(url, min_size=1, max_size=10)
        return _pools[url]


def close_pools() -> None:
    """Close every pool this module opened (tests, shutdown)."""
    with _lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


@contextmanager
def connection(url: str | None = None) -> Iterator[psycopg.Connection]:
    """A pooled connection in a transaction (commits on exit). An unreachable database raises TemporarilyUnavailable."""
    try:
        with get_pool(url).connection() as conn:
            yield conn
    except (psycopg.OperationalError, PoolTimeout) as exc:
        _log.error("database_unavailable", error=type(exc).__name__)
        raise TemporarilyUnavailable("the database is unavailable") from exc
