"""Metrics report: one panel per KEY_METRICS metric.

S01: each panel reads its source and prints "no data" while the source is empty. Panels fed from logs have no
reader yet (S19 adds them); table-fed panels read the database (S18 refines the queries).
"""

from collections.abc import Callable
from dataclasses import dataclass

import psycopg

NO_DATA = "no data"


@dataclass(frozen=True)
class Panel:
    name: str
    source: str
    reading: str


def _unknown_share(conn):
    row = conn.execute(
        """SELECT count(*) FILTER (WHERE outcome = 'unknown')::float / nullif(count(*), 0)
           FROM redemption_outcomes WHERE outcome <> 'rate_limited'"""
    ).fetchone()
    return NO_DATA if row[0] is None else f"{row[0]:.4f} of attempts"


def _redeemed_before_expiry(conn):
    row = conn.execute(
        """SELECT count(*) FILTER (WHERE used_at IS NOT NULL AND used_at <= expires_at)::float / nullif(count(*), 0)
           FROM tokens WHERE NOT is_seed"""
    ).fetchone()
    return NO_DATA if row[0] is None else f"{row[0]:.4f} of issued tokens"


def _kib_per_request(conn):
    row = conn.execute(
        """SELECT (SELECT coalesce(sum(bytes_consumed), 0) FROM issuances)::float / 1024
                  / nullif(count(*), 0)
           FROM batches WHERE purpose <> 'startup_check'"""
    ).fetchone()
    return NO_DATA if row[0] is None else f"{row[0]:.2f} KiB per request"


def _cap_use(conn):
    if conn.execute("SELECT count(*) FROM batches").fetchone()[0] == 0:
        return NO_DATA
    used, cap = conn.execute("SELECT used_kib, cap_kib FROM budget").fetchone()
    return f"{used} of {cap} KiB"


def _no_reader_yet(conn):
    """Log- and report-fed panels have no reader until S19 and S28 land."""
    return NO_DATA


PANELS: list[tuple[str, str, Callable]] = [
    ("codes never issued per day", "redemption_outcomes", _unknown_share),
    ("replies with an unbacked value", "agent log (S19)", _no_reader_yet),
    ("tokens redeemed before expiry", "tokens", _redeemed_before_expiry),
    ("tool-call p95", "server log (S19)", _no_reader_yet),
    ("server-caused errors", "server_status.snapshot (S28)", _no_reader_yet),
    ("Qrypt requests worst 10 s", "server_status.snapshot (S28)", _no_reader_yet),
    ("KiB per Qrypt request", "batches, issuances", _kib_per_request),
    ("Qrypt use against the cap", "budget, batches", _cap_use),
    ("time to unavailable", "server log (S19)", _no_reader_yet),
]


def build_panels(conn: psycopg.Connection) -> list[Panel]:
    return [Panel(name, source, reader(conn)) for name, source, reader in PANELS]
