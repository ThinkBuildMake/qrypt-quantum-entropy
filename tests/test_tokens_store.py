"""S16: token hashing and the store write path, against a real migrated Postgres (no Qrypt)."""

import hashlib
import hmac
import re
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import InvalidArgument
from qrypt_entropy.service import tokens
from tests.test_migrations import migrate

PEPPER = "test-pepper-not-a-secret"
VALUE = "123456"


@pytest.fixture(autouse=True)
def pepper(monkeypatch):
    monkeypatch.setenv("TOKEN_PEPPER", PEPPER)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db(empty_db, monkeypatch):
    assert migrate(empty_db).returncode == 0
    monkeypatch.setenv("DATABASE_URL", empty_db)
    get_settings.cache_clear()
    with psycopg.connect(empty_db) as conn:
        yield conn


def make_issuance(conn) -> str:
    client, issuance = uuid.uuid4(), uuid.uuid4()
    conn.execute("INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, %s, 'client', %s)",
                 (client, f"c-{client}", client.bytes))  # fmt: skip
    conn.execute("INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, 'generate_token', 4)",
                 (issuance, client))  # fmt: skip
    return str(issuance)


def test_ac1_hash_is_bound_to_owner():
    a, b, other = (tokens.hash_token("user-42", VALUE), tokens.hash_token("user-42", VALUE),
                   tokens.hash_token("user-43", VALUE))  # fmt: skip
    assert a == b and a != other


def test_ac2_hash_is_hmac_sha256_under_the_pepper(monkeypatch):
    expected = hmac.new(PEPPER.encode(), b"user-42\x00123456", hashlib.sha256).digest()
    assert tokens.hash_token("user-42", VALUE) == expected
    monkeypatch.setenv("TOKEN_PEPPER", "another-pepper")
    get_settings.cache_clear()
    assert tokens.hash_token("user-42", VALUE) != expected


def test_missing_pepper_is_refused(monkeypatch):
    monkeypatch.delenv("TOKEN_PEPPER")
    get_settings.cache_clear()
    with pytest.raises(tokens.AppError):
        tokens.hash_token("u", VALUE)


def test_ac3_only_the_hash_is_stored(db, empty_db):
    iid = make_issuance(db)
    tokens.store_token(db, iid, "user-42", "otp_6", VALUE, 600)
    db.commit()
    row = db.execute("SELECT token_hash, owner, kind, expires_at IS NOT NULL, used_at, is_seed FROM tokens").fetchone()
    assert (bytes(row[0]), *row[1:]) == (tokens.hash_token("user-42", VALUE), "user-42", "otp_6", True, None, False)
    dump = subprocess.run(["pg_dump", empty_db], capture_output=True, text=True)
    text = dump.stdout if dump.returncode == 0 else repr(db.execute("SELECT t::text FROM tokens t").fetchall())
    assert VALUE not in text and PEPPER not in text


def test_ac4_store_rolls_back_with_the_caller(db):
    tokens.store_token(db, make_issuance(db), "user-42", "otp_6", VALUE, 600)
    db.rollback()
    assert db.execute("SELECT count(*) FROM tokens").fetchone() == (0,)


def test_ac5_expiry_uses_the_database_clock(db):
    tokens.store_token(db, make_issuance(db), "user-42", "otp_6", VALUE, 600)
    (delta,) = db.execute(
        "SELECT abs(extract(epoch FROM expires_at - (now() + interval '600 seconds'))) FROM tokens"
    ).fetchone()
    assert delta < 1  # fmt: skip


def test_ac6_seed_token_is_flagged_and_unflagged_null_issuance_is_rejected(db):
    tokens.store_seed_token("seed-user", "otp_6", VALUE, 600)
    assert db.execute("SELECT is_seed, issuance_id FROM tokens WHERE owner = 'seed-user'").fetchone() == (True, None)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO tokens (id, owner, kind, token_hash, expires_at, is_seed)"
            " VALUES (gen_random_uuid(), 'x', 'otp_6', '\\x00', now() + interval '1 minute', false)"
        )


def test_ac7_owner_over_256_characters_is_refused_and_nothing_is_written(db):
    with pytest.raises(InvalidArgument) as err:
        tokens.store_token(db, make_issuance(db), "o" * 257, "otp_6", VALUE, 600)
    assert err.value.code == "invalid_argument"
    assert db.execute("SELECT count(*) FROM tokens").fetchone() == (0,)


def test_ac8_exactly_one_constant_time_hash_comparison_site():
    src = Path(__file__).parent.parent / "src" / "qrypt_entropy"
    sites = []
    for path in src.rglob("*.py"):
        for no, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#")[0]
            if "compare_digest" in code or re.search(r"(token_)?hash\w*\s*(==|!=)|(==|!=)\s*\w*hash", code):
                sites.append(f"{path.name}:{no}:{code.strip()}")
    token_sites = [s for s in sites if "key_hash" not in s]
    assert len(token_sites) == 1 and "compare_digest" in token_sites[0], token_sites
    assert tokens.hashes_equal(b"a", b"a") and not tokens.hashes_equal(b"a", b"b")
