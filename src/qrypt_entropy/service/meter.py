"""Meter: the budget and the upstream request record. Owns `budget`, `batches`.

S01 STUB: synthetic numbers, no database. Real behaviour: S07 (reserve/mark) and S24 (usage, setBudget).
"""

import uuid

from qrypt_entropy.models import Batch
from qrypt_entropy.stubs import stub

_STUB_CAP_KIB = 10240


@stub("S07")
def reserve(wanted_kib: int, purpose: str, source: str) -> Batch:
    return Batch(str(uuid.uuid4()), max(1, min(wanted_kib, _STUB_CAP_KIB)))


@stub("S07")
def mark_batch(batch_id: str, outcome: str) -> None:
    return None


@stub("S24")
def get_usage() -> dict:
    return {
        "budget": {"capKib": _STUB_CAP_KIB, "usedKib": 0},
        "perClient": [{"clientId": "00000000-0000-4000-8000-0000000000c1", "name": "stub-client", "bytesConsumed": 0}],
    }


@stub("S24")
def set_budget(cap_kib: int) -> dict:
    return {"budget": {"capKib": cap_kib, "usedKib": 0}}
