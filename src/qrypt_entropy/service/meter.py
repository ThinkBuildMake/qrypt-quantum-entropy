"""Meter: the budget and the upstream request record. Owns `budget`, `batches`.

S01 STUB: synthetic numbers, no database. Real behaviour: S07 (reserve/mark) and S24 (usage, setBudget).
"""

import uuid

from qrypt_entropy.errors import BudgetExhausted, InvalidArgument, NotFound
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import Batch
from qrypt_entropy.storage import db
from qrypt_entropy.stubs import stub

_STUB_CAP_KIB = 10240
_MAX_BATCH_KIB = 512
_log = get_logger("qrypt_entropy.meter")
_fired: set[tuple[str, int]] = set()  # (database url, threshold) already logged by this process


def close_pools() -> None:
    """Close every pool this process opened (tests, shutdown)."""
    db.close_pools()
    _fired.clear()


def _alert(url: str | None, threshold: int, used_kib: int, cap_kib: int) -> None:
    _log.warning("budget_alert", threshold_percent=threshold, used_kib=used_kib, cap_kib=cap_kib)
    _fired.add((url or "", threshold))


def reserve(wanted_kib: int, purpose: str, source: str, *, url: str | None = None) -> Batch:
    """Atomically count up to `wanted_kib` against the cap and record the batch (ARCHITECTURE: Meter)."""
    if not 1 <= wanted_kib <= _MAX_BATCH_KIB:
        raise InvalidArgument(f"wantedKib must be between 1 and {_MAX_BATCH_KIB}")
    batch_id = uuid.uuid4()
    with db.connection(url) as conn:
        # The row lock serializes every caller in every process, so the cap check and the add are one step.
        used, cap = conn.execute("SELECT used_kib, cap_kib FROM budget WHERE id = 1 FOR UPDATE").fetchone()
        granted = min(wanted_kib, cap - used)
        if granted < 1:
            exhausted = True
        else:
            exhausted = False
            conn.execute("UPDATE budget SET used_kib = used_kib + %s, updated_at = now() WHERE id = 1", (granted,))
            conn.execute(
                "INSERT INTO batches (id, source, purpose, kib) VALUES (%s, %s, %s, %s)",
                (batch_id, source, purpose, granted),
            )
    if exhausted:
        if (url or "", 100) not in _fired:
            _alert(url, 100, used, cap)
        raise BudgetExhausted("the entropy budget is spent")
    new_used = used + granted
    for threshold in (80, 100):
        if used * 100 < cap * threshold <= new_used * 100:
            _alert(url, threshold, new_used, cap)
    return Batch(str(batch_id), granted)


def mark_batch(batch_id: str, outcome: str, *, url: str | None = None) -> None:
    """Record received or failed on a batch. Never changes used_kib."""
    if outcome not in ("received", "failed"):
        raise InvalidArgument("outcome must be received or failed")
    with db.connection(url) as conn:
        done = conn.execute(
            "UPDATE batches SET outcome = %s, settled_at = now() WHERE id = %s", (outcome, batch_id)
        ).rowcount
    if not done:
        raise NotFound("no such batch")


@stub("S24")
def get_usage() -> dict:
    return {
        "budget": {"capKib": _STUB_CAP_KIB, "usedKib": 0},
        "perClient": [{"clientId": "00000000-0000-4000-8000-0000000000c1", "name": "stub-client", "bytesConsumed": 0}],
    }


@stub("S24")
def set_budget(cap_kib: int) -> dict:
    return {"budget": {"capKib": cap_kib, "usedKib": 0}}
