# ruff: noqa: F811  (the fixtures are imported from the S13 gate file and used by name)
"""S15 gate cases: generate_token and the one-transaction guarantee (docs/stories/S15-iss-token.md AC1-AC9)."""

import base64
import logging
import math
import random
import re
import uuid

import psycopg
import pytest

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, TemporarilyUnavailable
from qrypt_entropy.models import BatchSlice, Taken, ToolMetadata
from qrypt_entropy.service import issuance, pool, tokens
from tests.gate.test_bytes_gate import CLIENT, PRINCIPAL, bdb, rows, seed_batch, stub_pool  # noqa: F401


@pytest.fixture(autouse=True)
def pepper(monkeypatch):
    monkeypatch.setenv("TOKEN_PEPPER", "test-pepper-not-a-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def uniform_pool(monkeypatch, bdb, draws):
    """A pool whose successive takes return the given byte strings (a mocked uniform source), one batch."""
    batch, queue, used = seed_batch(bdb), list(draws), [0]

    async def take(n):
        data = queue.pop(0)
        assert len(data) == n
        slice_ = BatchSlice(batch, used[0], n)
        used[0] += n
        return Taken(data, [slice_])

    monkeypatch.setattr(pool, "take", take)
    return queue


async def test_token_otp_leading_zeros(bdb, monkeypatch):
    uniform_pool(monkeypatch, bdb, [b"\x00\x00\x07"])
    assert (await issuance.generate_token(PRINCIPAL, "otp_6")).value == "000007"


async def test_token_otp_rejection_counted(bdb, monkeypatch):
    uniform_pool(monkeypatch, bdb, [b"\xff\xff\xff", b"\x00\x00\x07"])
    result = await issuance.generate_token(PRINCIPAL, "otp_6")
    assert (result.value, result.metadata.bytes_consumed) == ("000007", 6)
    assert rows(bdb, "SELECT bytes_consumed FROM issuances") == [(6,)]
    assert rows(bdb, "SELECT sum(byte_count)::int FROM issuance_batches") == [(6,)]


async def test_token_otp_limit_edge(bdb, monkeypatch):
    # 15,999,999 is accepted (value 999999); 16,000,000 is rejected
    uniform_pool(monkeypatch, bdb, [(16_000_000).to_bytes(3, "big"), (15_999_999).to_bytes(3, "big")])
    assert (await issuance.generate_token(PRINCIPAL, "otp_6")).value == "999999"


async def test_token_formats(monkeypatch):
    counter = [0]

    async def take(n):  # a mocked uniform source: pseudo-random from a seeded generator, no database
        import random

        counter[0] += 1
        return Taken(random.Random(counter[0]).randbytes(n), [BatchSlice("b", 0, n)])

    monkeypatch.setattr(pool, "take", take)
    meta = ToolMetadata(source="qrypt", batch_id="b", batch_ids=["b"], bytes_consumed=0, reference="r")
    monkeypatch.setattr(issuance, "_audit", lambda *args: meta)  # the audit transaction has its own cases
    for _ in range(10_000):
        otp = await issuance.generate_token(PRINCIPAL, "otp_6")
        assert re.fullmatch(r"\d{6}", otp.value)
        u = (await issuance.generate_token(PRINCIPAL, "uuid_v4")).value
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", u)
        assert re.fullmatch(r"[0-9a-f]{64}", (await issuance.generate_token(PRINCIPAL, "api_key")).value)


async def test_token_api_key_is_the_32_bytes_in_hex(bdb, stub_pool):
    data = bytes(range(32))
    stub_pool.serve(data)
    result = await issuance.generate_token(PRINCIPAL, "api_key")
    assert result.value == data.hex() and result.metadata.bytes_consumed == 32


async def test_token_uuid_sets_version_and_variant(bdb, stub_pool):
    stub_pool.serve(b"\xff" * 16)
    result = await issuance.generate_token(PRINCIPAL, "uuid_v4")
    assert result.value == "ffffffff-ffff-4fff-bfff-ffffffffffff" and result.metadata.bytes_consumed == 16


async def test_token_one_transaction(bdb, stub_pool, monkeypatch):
    stub_pool.serve(b"\x00\x00\x07")
    seen = []
    real = tokens.store_token

    def spy(tx, issuance_id, owner, kind, value, expiry_seconds):
        # inside the transaction: the issuance and batch rows are visible on tx, but not yet to another connection
        seen.append((owner, expiry_seconds, tx.execute("SELECT count(*) FROM issuance_batches").fetchone()[0]))
        assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]
        real(tx, issuance_id, owner, kind, value, expiry_seconds)

    monkeypatch.setattr(tokens, "store_token", spy)
    result = await issuance.generate_token(PRINCIPAL, "otp_6", "user-42", 600)
    assert seen == [("user-42", 600, 1)]
    ref = result.metadata.reference
    expected_hash = tokens.hash_token("user-42", "000007")
    stored = rows(bdb, "SELECT issuance_id::text, owner, kind, token_hash FROM tokens")
    assert [(r[0], r[1], r[2], bytes(r[3])) for r in stored] == [(ref, "user-42", "otp_6", expected_hash)]
    assert rows(bdb, "SELECT count(*) FROM issuances WHERE id = %s", ref) == [(1,)]


async def test_token_without_owner_stores_no_token(bdb, stub_pool):
    stub_pool.serve(b"\x00\x00\x07")
    await issuance.generate_token(PRINCIPAL, "otp_6")
    assert rows(bdb, "SELECT count(*) FROM tokens") == [(0,)]


async def test_token_default_expiry(bdb, stub_pool, monkeypatch):
    stub_pool.serve(b"\x00\x00\x07")
    calls = []
    monkeypatch.setattr(tokens, "store_token", lambda *args: calls.append(args[1:]))
    await issuance.generate_token(PRINCIPAL, "otp_6", "user-42")
    assert [(a[1], a[4]) for a in calls] == [("user-42", 600)]


async def test_token_default_expiry_is_stored(bdb, stub_pool):
    stub_pool.serve(b"\x00\x00\x07")
    await issuance.generate_token(PRINCIPAL, "otp_6", "user-42")
    assert rows(bdb, "SELECT (expires_at - created_at) = interval '600 seconds' FROM tokens") == [(True,)]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"kind": "pin"}, "kind"),
        ({"kind": "otp_6", "owner": "u", "expiry_seconds": 0}, "expiry"),
        ({"kind": "otp_6", "owner": "u", "expiry_seconds": 86401}, "expiry"),
        ({"kind": "otp_6", "expiry_seconds": 60}, "owner"),
        ({"kind": "otp_6", "owner": "x" * 257}, "owner"),
    ],
)
async def test_token_invalid(bdb, stub_pool, kwargs, match):
    stub_pool.serve(bytes(4096))
    with pytest.raises(InvalidArgument, match=match):
        await issuance.generate_token(PRINCIPAL, **kwargs)
    assert stub_pool.level == 4096
    assert rows(bdb, "SELECT used_kib FROM budget") == [(0,)]
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]


async def test_token_insert_fails(bdb, stub_pool, monkeypatch, caplog, capfd):
    stub_pool.serve(b"\x00\x00\x07\x00\x00\x08")
    caplog.set_level(logging.DEBUG)

    def boom(*args):
        raise psycopg.errors.UniqueViolation("injected")

    monkeypatch.setattr(tokens, "store_token", boom)
    with pytest.raises(TemporarilyUnavailable) as caught:
        await issuance.generate_token(PRINCIPAL, "otp_6", "user-42")
    assert "000007" not in caught.value.message
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]
    assert rows(bdb, "SELECT count(*) FROM issuance_batches") == [(0,)]
    assert rows(bdb, "SELECT count(*) FROM tokens") == [(0,)]
    assert issuance.call_stats()["errorsByCode"] == {"temporarily_unavailable": 1}
    out = capfd.readouterr()
    assert "000007" not in caplog.text + out.out + out.err  # OB-3: the value is in no log line


async def test_token_pepper_unset_fails_closed(bdb, stub_pool, monkeypatch):
    stub_pool.serve(b"\x00\x00\x07")
    monkeypatch.delenv("PEPPER", raising=False)
    monkeypatch.setenv("TOKEN_PEPPER", "")
    get_settings.cache_clear()
    with pytest.raises(AppError) as caught:
        await issuance.generate_token(PRINCIPAL, "otp_6", "user-42")
    assert caught.value.code == "temporarily_unavailable" and "000007" not in caught.value.message
    for table in ("issuances", "issuance_batches", "tokens"):
        assert rows(bdb, f"SELECT count(*) FROM {table}") == [(0,)]


async def test_token_unexpected_insert_error_also_fails_closed(bdb, stub_pool, monkeypatch):
    stub_pool.serve(b"\x00\x00\x07")

    def boom(*args):
        raise RuntimeError("injected")

    monkeypatch.setattr(tokens, "store_token", boom)
    with pytest.raises(TemporarilyUnavailable):
        await issuance.generate_token(PRINCIPAL, "otp_6", "user-42")
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]


async def test_bytes_consumed_sums(bdb, stub_pool, monkeypatch):
    batch = stub_pool.serve(bytes(53) + bytes([255, 255, 255, 0, 0, 7]) + bytes([255, 1]))
    await issuance.get_random_bytes(PRINCIPAL, 5, "hex")
    await issuance.generate_token(PRINCIPAL, "api_key", "u", 60)
    await issuance.generate_token(PRINCIPAL, "uuid_v4")
    await issuance.generate_token(PRINCIPAL, "otp_6")
    await issuance.random_int(PRINCIPAL, 0, 99)  # draws 255 (masked to 127, rejected), then 1
    got = rows(
        bdb,
        "SELECT i.tool, i.bytes_consumed, sum(b.byte_count)::int FROM issuances i "
        "JOIN issuance_batches b ON b.issuance_id = i.id GROUP BY i.id, i.tool ORDER BY i.bytes_consumed",
    )
    assert all(consumed == total for _, consumed, total in got) and len(got) == 5
    assert batch  # drawn from the one batch


async def test_audit_has_no_values(bdb, stub_pool, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    stub_pool.serve(bytes(range(256)) * 20)
    values = []
    for i in range(100):
        kind = ("otp_6", "uuid_v4", "api_key")[i % 3]
        values.append((await issuance.generate_token(PRINCIPAL, kind, f"u{i}" if i % 2 else None)).value)
    # Everything the tables hold, as text; and only the columns that could hold a 6-digit value (a timestamp's
    # microseconds or a uuid would match a code by chance).
    full = "\n".join(
        r[0] for r in rows(bdb, "SELECT i::text FROM issuances i UNION ALL SELECT b::text FROM issuance_batches b")
    )
    counters = "\n".join(
        f"{r[0]} {r[1]}"
        for r in rows(
            bdb,
            "SELECT tool, bytes_consumed FROM issuances "
            "UNION ALL SELECT byte_offset::text, byte_count FROM issuance_batches",
        )
    )
    for value in values:
        raw = value.encode()
        forms = {raw.hex(), base64.b64encode(raw).decode()}
        if re.fullmatch(r"[0-9a-f]{64}", value):
            forms |= {bytes.fromhex(value).hex(), base64.b64encode(bytes.fromhex(value)).decode()}
        for form in forms:
            assert form not in full, (form, "found in the audit tables")
        assert value not in (counters if re.fullmatch(r"\d{6}", value) else full)
    out = capfd.readouterr()
    logged = caplog.text + out.out + out.err
    assert not [v for v in values if v in logged]  # OB-3: no issued value in any log line
    assert uuid.UUID(values[1])  # a real UUID was issued
    assert CLIENT


# VR-2: otp_6 is unbiased. 1,000,000 draws into 1,000 bins (value // 1000), alpha = 0.001.
def chi_square_limit(df: int = 999, z: float = 3.0902) -> float:
    """Wilson-Hilferty approximation of the chi-square quantile at alpha = 0.001 (about 1143.9 for 999 degrees)."""
    return df * (1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))) ** 3


def chi_square(values) -> float:
    bins = [0] * 1000
    for v in values:
        bins[v // 1000] += 1
    return sum((b - 1000) ** 2 / 1000 for b in bins)


def uniform_draws(count: int, seed: int = 1):
    data = random.Random(seed).randbytes(3 * count)
    return [data[i : i + 3] for i in range(0, len(data), 3)]


async def test_token_otp_is_unbiased():
    queue = iter(uniform_draws(1_050_000))
    served = []

    async def draw(n):
        served.append(n)
        return next(queue)

    codes = [int(await issuance._draw_otp(draw)) for _ in range(1_000_000)]
    assert chi_square(codes) < chi_square_limit()
    assert len(served) > 1_000_000  # 4.6% of draws were rejected and drawn again, not wrapped around


def test_chi_square_harness_catches_a_modulo_sampler():
    modulo = [int.from_bytes(d, "big") % 1_000_000 for d in uniform_draws(1_000_000)]
    assert chi_square(modulo) > chi_square_limit()
