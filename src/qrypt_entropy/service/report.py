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


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _per_day(rows, header: str, line) -> str:
    """A panel reading: the first line goes on the panel's header, one indented line per UTC day follows."""
    if not rows:
        return NO_DATA
    return "\n".join([header, *(f"  {day}: {line(*rest)}" for day, *rest in rows)])


def _unknown_share(conn):
    rows = conn.execute(
        """SELECT (attempted_at AT TIME ZONE 'UTC')::date AS day,
                  count(*) FILTER (WHERE outcome = 'unknown'),
                  count(*) FILTER (WHERE outcome <> 'rate_limited')
           FROM redemption_outcomes GROUP BY day ORDER BY day"""
    ).fetchall()
    return _per_day(
        rows,
        "unknown codes of attempts except rate_limited, per UTC day",
        lambda unknown, attempts: f"{unknown} of {attempts}" if attempts else "no attempts",
    )


def _redeemed_before_expiry(conn):
    rows = conn.execute(
        """SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                  count(*) FILTER (WHERE used_at <= expires_at), count(*)
           FROM tokens WHERE NOT is_seed GROUP BY day ORDER BY day"""
    ).fetchall()
    return _per_day(
        rows,
        "tokens issued, redeemed once before they expired, per UTC day of issue",
        lambda redeemed, issued: f"{redeemed} of {issued}",
    )


def _kib_per_request(conn):
    rows = conn.execute(
        """WITH req AS (SELECT (requested_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n
                        FROM batches WHERE purpose <> 'startup_check' GROUP BY day),
                srv AS (SELECT (created_at AT TIME ZONE 'UTC')::date AS day, sum(bytes_consumed) / 1024.0 AS kib
                        FROM issuances GROUP BY day)
           SELECT day, coalesce(srv.kib, 0) / req.n
           FROM req LEFT JOIN srv USING (day) ORDER BY day"""
    ).fetchall()
    return _per_day(
        rows,
        "KiB served per upstream request, startup check excluded, per UTC day",
        lambda v: f"{_num(v)} KiB per request",
    )


def _cap_use(conn):
    used, cap = conn.execute("SELECT used_kib, cap_kib FROM budget").fetchone()
    if used == 0 and conn.execute("SELECT count(*) FROM batches").fetchone()[0] == 0:
        return NO_DATA
    alert = cap * 8 // 10
    side = "at or above" if used * 100 >= cap * 80 else "within"
    percent = f"{used * 100 // cap}%" if cap else "n/a"
    return f"{used:,} of {cap:,} KiB ({percent}), {side} the {alert:,} KiB alert level"


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
