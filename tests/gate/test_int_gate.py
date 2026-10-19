"""S14 gate cases: random_int and its unbiased rejection sampler (docs/stories/S14-iss-int.md AC1-AC7).

Fixtures come from the S13 gate file: a real `Pool` holding seeded bytes, a seeded credential, a migrated database.
The statistical cases drive `issuance.sample_int` (the sampler the tool uses) from a seeded uniform source.
"""

import math
import random
import uuid

import pytest

from qrypt_entropy.errors import InvalidArgument, TemporarilyUnavailable
from qrypt_entropy.models import BatchSlice, Taken, ToolMetadata
from qrypt_entropy.service import issuance, pool
from tests.gate.test_bytes_gate import CLIENT, PRINCIPAL, bdb, rows, seed_batch, stub_pool  # noqa: F401

SAFE = 2**53 - 1
Z_ALPHA_0_001 = 3.0902  # upper-tail normal quantile for alpha = 0.001


def chi_square_fails(counts: list[int], draws: int) -> bool:
    """True when the counts reject uniformity at alpha 0.001 (Wilson-Hilferty approximation of the chi-square tail)."""
    expected = draws / len(counts)
    statistic = sum((c - expected) ** 2 / expected for c in counts)
    df = len(counts) - 1
    critical = df * (1 - 2 / (9 * df) + Z_ALPHA_0_001 * math.sqrt(2 / (9 * df))) ** 3
    return statistic > critical


def uniform_source(seed: int):
    rng = random.Random(seed)

    async def draw(n: int) -> bytes:
        return rng.randbytes(n)

    return draw


async def modulo_sampler(span: int, draw) -> int:
    """The biased sampler the proof must catch: one draw, reduced with a modulo."""
    return int.from_bytes(await draw(1)) % span


async def histogram(sampler, span: int, draws: int, seed: int = 20260101) -> list[int]:
    draw, counts = uniform_source(seed), [0] * span
    for _ in range(draws):
        counts[await sampler(span, draw)] += 1
    return counts


@pytest.fixture
def random_pool(stub_pool):  # noqa: F811
    """Plenty of random bytes in one batch for the 10,000-call loops."""
    stub_pool.serve(random.Random(7).randbytes(400_000))
    return stub_pool


async def test_int_range(bdb, random_pool):  # noqa: F811
    results = [(await issuance.random_int(PRINCIPAL, 1, 6)).value for _ in range(10_000)]
    assert all(1 <= r <= 6 for r in results) and set(results) == {1, 2, 3, 4, 5, 6}
    assert (await issuance.random_int(PRINCIPAL, 5, 5)).value == 5
    assert rows(bdb, "SELECT count(*) FROM issuances WHERE tool = 'random_int'") == [(10_001,)]
    assert rows(
        bdb,
        "SELECT count(*) FROM issuances i WHERE bytes_consumed <> "
        "(SELECT sum(byte_count) FROM issuance_batches WHERE issuance_id = i.id)",
    ) == [(0,)]


async def test_int_wide(bdb, random_pool, monkeypatch):  # noqa: F811
    audited = []  # the audit path is proven by the range case; skipping its commits keeps 10,000 wide calls fast
    monkeypatch.setattr(
        issuance,
        "_audit",
        lambda principal, tool, n, slices: (
            audited.append(n)
            or ToolMetadata(source="qrypt", batch_id="b", batch_ids=["b"], bytes_consumed=n, reference="r")
        ),
    )
    results = [(await issuance.random_int(PRINCIPAL, -SAFE, SAFE)).value for _ in range(10_000)]
    assert all(type(r) is int and -SAFE <= r <= SAFE for r in results)
    assert len(audited) == 10_000 and all(n >= 7 for n in audited)
    assert min(results) < -SAFE // 2 and max(results) > SAFE // 2  # spread over the whole range


@pytest.mark.parametrize(("lo", "hi"), [(7, 3), (0, SAFE + 1), (-SAFE - 1, 0)])
async def test_int_invalid(bdb, stub_pool, lo, hi):  # noqa: F811
    stub_pool.serve(bytes(4096))
    with pytest.raises(InvalidArgument):
        await issuance.random_int(PRINCIPAL, lo, hi)
    assert stub_pool.level == 4096
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]
    assert issuance.call_stats()["errorsByCode"] == {"invalid_argument": 1}


async def test_int_chi_square():
    counts = await histogram(issuance.sample_int, 1000, 1_000_000)
    assert not chi_square_fails(counts, 1_000_000)
    # the same sampler on a span that does not divide the byte space is also uniform
    assert not chi_square_fails(await histogram(issuance.sample_int, 171, 1_000_000), 1_000_000)


async def test_int_mutation_modulo():
    counts = await histogram(modulo_sampler, 171, 1_000_000)  # random_int(0, 170) on single bytes
    assert chi_square_fails(counts, 1_000_000)


async def test_int_rejection_counted(bdb, stub_pool):  # noqa: F811
    batch = stub_pool.serve(bytes([0xFF, 0x05]))
    result = await issuance.random_int(PRINCIPAL, 0, 170)
    assert result.value == 5 and result.metadata.bytes_consumed == 2
    assert rows(bdb, "SELECT bytes_consumed FROM issuances") == [(2,)]
    assert rows(bdb, "SELECT batch_id::text, byte_offset, byte_count FROM issuance_batches") == [
        (batch, 0, 1),
        (batch, 1, 1),
    ]
    assert stub_pool.level == 0


async def test_int_multidraw_failure(bdb, monkeypatch):  # noqa: F811
    served = []

    async def take(n):
        if served:
            raise TemporarilyUnavailable("the pool ran dry")
        served.append(n)
        return Taken(b"\xff", [BatchSlice(seed_batch(bdb), 0, 1)])  # rejected for span 171, so a second draw follows

    monkeypatch.setattr(pool, "take", take)
    with pytest.raises(TemporarilyUnavailable):
        await issuance.random_int(PRINCIPAL, 0, 170)
    assert len(served) == 1
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]
    assert rows(bdb, "SELECT count(*) FROM issuance_batches") == [(0,)]
    assert issuance.call_stats()["errorsByCode"] == {"temporarily_unavailable": 1}


async def test_int_min_equals_max_still_audited(bdb, stub_pool):  # noqa: F811
    stub_pool.serve(bytes([9]))
    result = await issuance.random_int(PRINCIPAL, 5, 5)
    assert (result.value, result.metadata.bytes_consumed) == (5, 1)
    assert uuid.UUID(result.metadata.reference) and CLIENT
