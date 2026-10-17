"""Marks contract stubs so none can be mistaken for real behaviour (see docs/STUBS.md)."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class StubInfo:
    qualname: str
    module: str
    replaced_by: str


STUBS: list[StubInfo] = []


def stub(replaced_by: str) -> Callable:
    """Decorator: this function returns synthetic, contract-shaped data until story `replaced_by` lands."""

    def mark(fn):
        STUBS.append(StubInfo(fn.__qualname__, fn.__module__, replaced_by))
        fn.__stub__ = replaced_by
        return fn

    return mark


STUB_BATCH_ID = "00000000-0000-4000-8000-0000000000b1"


def seed_stub_batch(url: str) -> None:
    """Insert the batch row the stub pool's slices point at, so audit rows can reference it (S20 removes)."""
    import psycopg

    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO batches (id, source, purpose, kib, outcome) VALUES (%s, 'stub', 'refill', 1, 'received') "
            "ON CONFLICT DO NOTHING",
            (STUB_BATCH_ID,),
        )
