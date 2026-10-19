"""Alembic environment: raw-SQL migrations as the owner (MIGRATOR_URL, else DATABASE_URL)."""

import os

from alembic import context
from sqlalchemy import create_engine


def _url() -> str:
    url = os.environ.get("MIGRATOR_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("MIGRATOR_URL or DATABASE_URL is not set")
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
