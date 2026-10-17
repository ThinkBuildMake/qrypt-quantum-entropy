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
