"""Shared types from ARCHITECTURE.md (Shared types)."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "client"]


@dataclass(frozen=True)
class Principal:
    client_id: str
    role: str
    name: str


@dataclass(frozen=True)
class BatchSlice:
    batch_id: str
    byte_offset: int
    byte_count: int


@dataclass(frozen=True)
class Taken:
    bytes: bytes = field(repr=False)  # never stored or logged
    slices: list[BatchSlice] = field(default_factory=list)


@dataclass(frozen=True)
class PoolStatus:
    level_bytes: int
    low_mark_bytes: int
    high_mark_bytes: int
    upstream_status: str
    last_success_at: str | None


@dataclass(frozen=True)
class Fetched:
    label: str
    bytes: bytes = field(repr=False)
    received_at: str = ""


@dataclass(frozen=True)
class Batch:
    batch_id: str
    kib: int


class ToolMetadata(BaseModel):
    source: str
    batch_id: str
    batch_ids: list[str]
    bytes_consumed: int
    reference: str


class IssuedValue(BaseModel):
    value: str | int
    metadata: ToolMetadata
