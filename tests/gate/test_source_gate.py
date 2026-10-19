"""S08 gate cases: the Qrypt client against the committed mock (docs/stories/S08-source.md AC1-AC9)."""

import logging
import time

import pytest

from qrypt_entropy.errors import AllotmentUsed, InvalidArgument, Misconfigured, Unavailable
from qrypt_entropy.upstream import source
from tests.mock_qrypt import MockQrypt


@pytest.fixture
async def mock():
    async with MockQrypt() as m:
        yield m


def client(mock, token=None, timeout=5.0):
    return source.QryptSource(mock.url, token or mock.token, timeout)


async def test_fetch_shape(mock):
    got = await client(mock).fetch(64)
    assert len(got.bytes) == 65536
    assert got.label == "qrypt"
    assert mock.sizes() == ["64"]
    assert mock.log[0].authorization == f"Bearer {mock.token}"


async def test_fetch_bounds(mock):
    c = client(mock)
    for bad in (0, 513):
        with pytest.raises(InvalidArgument):
            await c.fetch(bad)
    assert mock.log == []
    assert len((await c.fetch(1)).bytes) == 1024
    assert len((await c.fetch(512)).bytes) == 512 * 1024
    assert mock.sizes() == ["1", "512"]


async def test_fetch_401(mock, caplog):
    with pytest.raises(Misconfigured), caplog.at_level(logging.INFO):
        await client(mock, token="expired-token").fetch(1)
    assert "invalid or expired" in caplog.text


async def test_fetch_403(mock, caplog):
    mock.faults = [403]
    with pytest.raises(AllotmentUsed), caplog.at_level(logging.INFO):
        await client(mock).fetch(1)
    assert "upstream_allotment_used" in caplog.text


async def test_fetch_5xx(mock, caplog):
    mock.faults = [500, 503, "drop"]
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            with pytest.raises(Unavailable):
                await client(mock).fetch(1)
    assert caplog.text.count("upstream_unavailable") == 3


async def test_fetch_timeout(mock):
    mock.delay_s = 6
    started = time.monotonic()
    with pytest.raises(Unavailable):
        await client(mock).fetch(1)
    assert 4.5 <= time.monotonic() - started < 5.8


async def test_fetch_no_leak(mock, caplog, monkeypatch):
    mock.faults = [500, 403]
    c = client(mock)
    with caplog.at_level(logging.DEBUG):
        for _ in range(2):
            with pytest.raises(Exception):  # noqa: B017, PT011
                await c.fetch(1)
        await c.fetch(1)
        with pytest.raises(Misconfigured):
            await client(mock, token="wrong-token-value").fetch(1)
    assert mock.token not in caplog.text
    assert "wrong-token-value" not in caplog.text
    assert "AAECAwQFBgcI" not in caplog.text  # base64 of the bytes the mock returns
    assert not [name for name in type(source.get_settings()).model_fields if "source" in name]


async def test_stub_source(mock):
    source.use_source(source.TestSource())
    try:
        got = await source.fetch(2)
    finally:
        source.use_source(None)
    assert got.label == "test_source"
    assert len(got.bytes) == 2048


async def test_request_stats(mock):
    c = client(mock)
    for _ in range(5):
        await c.fetch(1)
    assert c.request_stats() == {"requestsLast10s": 5}
