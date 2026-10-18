"""Meter: the budget and the upstream request record. Owns `budget`, `batches`.

S07: reserve and mark_batch (MCP side writes used_kib). S24: get_usage and set_budget (HTTP side writes cap_kib).
"""

import uuid

from qrypt_entropy.errors import BudgetExhausted, InvalidArgument, NotFound
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import Batch
from qrypt_entropy.storage import db

_STUB_CAP_KIB = 10240
_MAX_BATCH_KIB = 512
_MAX_CAP_KIB = 2**63 - 1  # the column is a bigint
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
    """Record received or failed on a batch that is still `requested`; the first mark stands (racing marks cannot
    flip it). Never changes used_kib."""
    if outcome not in ("received", "failed"):
        raise InvalidArgument("outcome must be received or failed")
    with db.connection(url) as conn:
        done = conn.execute(
            "UPDATE batches SET outcome = %s, settled_at = now() WHERE id = %s AND outcome = 'requested'",
            (outcome, batch_id),
        ).rowcount
        if not done and conn.execute("SELECT 1 FROM batches WHERE id = %s", (batch_id,)).fetchone() is None:
            raise NotFound("no such batch")


STALE_AFTER_SECONDS = 600  # longer than any fetch can take (about 2 minutes of 429 backoff)


def settle_stale(*, url: str | None = None) -> int:
    """Mark upstream batches a stopped process left `requested` as failed (one UPDATE). Returns how many."""
    with db.connection(url) as conn:
        return conn.execute(
            "UPDATE batches SET outcome = 'failed', settled_at = now() WHERE outcome = 'requested'"
            " AND purpose IN ('startup_check', 'refill') AND requested_at < now() - make_interval(secs => %s)",
            (STALE_AFTER_SECONDS,),
        ).rowcount


def get_usage(*, url: str | None = None) -> dict:
    """The cap, the KiB used, and bytes consumed per client, summed from the audit rows (FR-5.1, CH-3)."""
    with db.connection(url) as conn:
        cap, used = conn.execute("SELECT cap_kib, used_kib FROM budget WHERE id = 1").fetchone()
        rows = conn.execute(
            "SELECT c.id, c.name, sum(i.bytes_consumed)::bigint FROM issuances i "
            "JOIN credentials c ON c.id = i.client_id GROUP BY c.id, c.name ORDER BY c.name, c.id"
        ).fetchall()
    return {
        "budget": {"capKib": cap, "usedKib": used},
        "perClient": [{"clientId": str(cid), "name": name, "bytesConsumed": n} for cid, name, n in rows],
    }


def set_budget(cap_kib: int, *, url: str | None = None) -> dict:
    """Set the cap. Lowering it below use is accepted: the budget is then exhausted until it is raised (A-4)."""
    if type(cap_kib) is not int or not 0 <= cap_kib <= _MAX_CAP_KIB:
        raise InvalidArgument("capKib must be a whole number from 0 to 2^63-1")
    with db.connection(url) as conn:
        used = conn.execute(
            "UPDATE budget SET cap_kib = %s, updated_at = now() WHERE id = 1 RETURNING used_kib", (cap_kib,)
        ).fetchone()[0]
    _fired.discard((url or "", 100))  # a new cap starts a new episode: the next exhaustion is logged again
    return {"budget": {"capKib": cap_kib, "usedKib": used}}
