"""Storage layer: the connection pool. Nothing here imports a higher layer."""

from psycopg_pool import ConnectionPool

from qrypt_entropy.config import get_settings


def open_pool(url: str | None = None, **kwargs) -> ConnectionPool:
    """A pool that checks connections before use so it survives a database restart (RL-7)."""
    return ConnectionPool(
        url or get_settings().database_url,
        check=ConnectionPool.check_connection,
        open=True,
        **kwargs,
    )
