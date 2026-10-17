"""S17 gate cases: seed-no-entropy, seed-token-hash (and the row counts, idempotency and names)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access, tokens

ROOT = Path(__file__).resolve().parents[2]
PEPPER = "test-pepper-not-a-secret"


@pytest.fixture
def seeded_env(db, monkeypatch):
    monkeypatch.setenv("TOKEN_PEPPER", PEPPER)
    get_settings.cache_clear()
    return {
        **os.environ,
        "DATABASE_URL": db,
        "TOKEN_PEPPER": PEPPER,
        # no network: any attempt to reach Qrypt would fail
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "QRYPT_API_KEY": "",
    }


def run(env, *args):
    return subprocess.run(
        [sys.executable, "scripts/seed.py", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )


def counts(url):
    with psycopg.connect(url) as conn:
        roles = dict(conn.execute("SELECT role, count(*) FROM credentials GROUP BY role").fetchall())
        seeds = conn.execute("SELECT count(*) FROM tokens WHERE is_seed AND issuance_id IS NULL").fetchone()[0]
    return roles, seeds


def test_seed_creates_rows_and_prints_each_key_once(seeded_env, db):
    out = run(seeded_env)
    assert out.returncode == 0, out.stderr
    assert counts(db) == ({"admin": 1, "client": 2}, 3)
    printed = re.findall(r"^credential role=(\S+) name=(\S+) key=(\S+)$", out.stdout, re.M)
    assert sorted(name for _, name, _ in printed) == ["admin", "billing-agent", "support-agent"]
    for role, name, key in printed:
        assert access.authenticate(key, {role}).name == name
    assert len(re.findall(r"^seed_token .* value=\S+$", out.stdout, re.M)) == 3


def test_seed_twice_changes_nothing_and_prints_no_key(seeded_env, db):
    assert run(seeded_env).returncode == 0
    again = run(seeded_env)
    assert again.returncode == 0, again.stderr
    assert again.stdout == ""
    assert counts(db) == ({"admin": 1, "client": 2}, 3)


def test_gate_seed_no_entropy(seeded_env, db):
    assert run(seeded_env).returncode == 0
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT cap_kib, used_kib FROM budget").fetchall() == [(10240, 0)]
        assert conn.execute("SELECT count(*) FROM batches").fetchone()[0] == 0


def test_gate_seed_token_hash(seeded_env, db):
    out = run(seeded_env).stdout
    printed = re.findall(r"^seed_token owner=(\S+) kind=\S+ value=(\S+)$", out, re.M)
    assert len(printed) == 3
    with psycopg.connect(db) as conn:
        stored = {bytes(h) for (h,) in conn.execute("SELECT token_hash FROM tokens")}
    for owner, value in printed:
        assert tokens.hash_token(owner, value) in stored


def test_seed_names_are_the_pinned_synthetic_ones(seeded_env, db):
    assert run(seeded_env).returncode == 0
    listed = run(seeded_env, "list-names").stdout.split("\n")
    assert [line for line in listed if line] == [
        "admin admin",
        "client billing-agent",
        "client support-agent",
        "seed-token-owner seed-owner-1",
        "seed-token-owner seed-owner-2",
        "seed-token-owner seed-owner-3",
    ]
