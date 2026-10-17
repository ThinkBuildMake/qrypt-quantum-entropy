"""S18: the four table-fed panels, seeded with synthetic rows and read through `make report`'s command."""

import os
import subprocess
import sys
import uuid

import psycopg
import psycopg.sql
import pytest

from tests.test_migrations import migrate

CLIENT = "00000000-0000-4000-8000-0000000000c1"
DAY = "2026-10-17"


@pytest.fixture
def conn(empty_db):
    migrate(empty_db)
    with psycopg.connect(empty_db, autocommit=True) as c:
        c.execute(
            "INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, 'seed-client', 'client', %s)",
            (CLIENT, b"x"),
        )
        c.db_url = empty_db
        yield c


def report(conn) -> tuple[int, str]:
    env = {**os.environ, "DATABASE_URL": conn.db_url}
    run = subprocess.run(
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"], env=env, capture_output=True, text=True
    )
    return run.returncode, run.stdout


def panel(out: str, name: str) -> str:
    """The panel's header line and the indented lines after it."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if name in ln)
    end = start + 1
    while end < len(lines) and not lines[end].startswith("PANEL"):
        end += 1
    return "\n".join(lines[start:end])


def outcomes(conn, **counts):
    for outcome, n in counts.items():
        for _ in range(n):
            conn.execute(
                "INSERT INTO redemption_outcomes (id, owner, outcome, attempted_at) VALUES (%s, 'o', %s, %s)",
                (uuid.uuid4(), outcome, f"{DAY} 12:00+00"),
            )


def issued_token(conn, used_after_hours=None, seed=False):
    issuance = None
    if not seed:
        issuance = uuid.uuid4()
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, 'generate_token', 16)",
            (issuance, CLIENT),
        )
    used = None if used_after_hours is None else f"{DAY} {12 + used_after_hours}:00+00"
    conn.execute(
        "INSERT INTO tokens (id, issuance_id, owner, kind, token_hash, created_at, expires_at, used_at, is_seed) "
        "VALUES (%s, %s, 'o', 'otp_6', %s, %s, %s, %s, %s)",
        (uuid.uuid4(), issuance, b"h", f"{DAY} 12:00+00", f"{DAY} 15:00+00", used, seed),
    )


def test_ac1_success_metric_is_unknown_over_attempts_except_rate_limited(conn):
    outcomes(conn, redeemed=3, already_used=1, expired=1, unknown=2, rate_limited=4)
    code, out = report(conn)
    assert code == 0
    text = panel(out, "never issued")
    assert f"{DAY}: 2 of 7" in text


def test_ac2_only_rate_limited_day_shows_no_attempts(conn):
    outcomes(conn, rate_limited=4)
    code, out = report(conn)
    assert code == 0
    assert f"{DAY}: no attempts" in panel(out, "never issued")


def test_ac3_redeemed_before_expiry_excludes_seed_tokens(conn):
    issued_token(conn, used_after_hours=1)
    issued_token(conn, used_after_hours=2)
    issued_token(conn, used_after_hours=4)  # used after it expired: not counted
    issued_token(conn)
    issued_token(conn, used_after_hours=1, seed=True)
    code, out = report(conn)
    assert code == 0
    assert f"{DAY}: 2 of 4" in panel(out, "before expiry")


def test_ac4_kib_per_request_excludes_startup_check(conn):
    for purpose, kib in (("startup_check", 1), ("refill", 64), ("refill", 64)):
        conn.execute(
            "INSERT INTO batches (id, purpose, kib, outcome) VALUES (%s, %s, %s, 'received')",
            (uuid.uuid4(), purpose, kib),
        )
    conn.execute(
        "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, 'get_random_bytes', 131072)",
        (uuid.uuid4(), CLIENT),
    )
    code, out = report(conn)
    assert code == 0
    assert "64 KiB per request" in panel(out, "per Qrypt request")


def test_ac5_cap_use_shows_alert_level(conn):
    conn.execute("UPDATE budget SET used_kib = 8300")
    code, out = report(conn)
    assert code == 0
    text = panel(out, "against the cap")
    assert "8,300 of 10,240 KiB (81%)" in text
    assert "above the 8,192 KiB alert level" in text


def test_ac6_every_panel_reads_no_data_on_an_empty_database(conn):
    code, out = report(conn)
    assert code == 0
    for name in ("never issued", "before expiry", "per Qrypt request", "against the cap"):
        assert panel(out, name).splitlines()[0].endswith("no data")
        assert len(panel(out, name).splitlines()) == 1


@pytest.mark.parametrize(
    ("used", "side"), [(8191, "within"), (8192, "at or above"), (8193, "at or above"), (100, "within")]
)
def test_cap_alert_boundary(conn, used, side):
    conn.execute("UPDATE budget SET used_kib = %s", (used,))
    _, out = report(conn)
    assert f"{side} the 8,192 KiB alert level" in panel(out, "against the cap")


def test_cap_of_zero_does_not_divide_by_zero(conn):
    conn.execute("UPDATE budget SET cap_kib = 0")
    conn.execute("INSERT INTO batches (id, purpose, kib) VALUES (%s, 'refill', 1)", (uuid.uuid4(),))
    code, out = report(conn)
    assert code == 0
    assert "0 of 0 KiB (n/a), at or above the 0 KiB alert level" in panel(out, "against the cap")


def test_days_are_bucketed_in_utc_whatever_the_session_time_zone(conn):
    conn.execute(
        "ALTER DATABASE CURRENT_DB_PLACEHOLDER SET timezone = 'Pacific/Auckland'".replace(
            "CURRENT_DB_PLACEHOLDER", psycopg.sql.Identifier(conn.info.dbname).as_string(conn)
        )
    )
    conn.execute(
        "INSERT INTO redemption_outcomes (id, owner, outcome, attempted_at) "
        "VALUES (%s, 'o', 'unknown', '2026-10-17 23:30+00')",
        (uuid.uuid4(),),
    )
    _, out = report(conn)  # in Auckland that instant is already 2026-10-18
    assert "2026-10-17: 1 of 1" in panel(out, "never issued")
