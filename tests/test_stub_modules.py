import pytest
from mcp.client import Client

from qrypt_entropy.errors import BudgetExhausted, InvalidArgument, TemporarilyUnavailable
from qrypt_entropy.models import Principal
from qrypt_entropy.service import issuance, meter, pool, status, tokens
from qrypt_entropy.service.access import STUB_CLIENT_KEY, authenticate, create_credential, hash_key
from qrypt_entropy.transport.mcp_server import build_server
from qrypt_entropy.upstream import source

P = Principal("c", "client", "n")


async def test_pool_and_source_stubs_keep_their_contract_bounds():
    taken = await pool.take(4)
    assert len(taken.bytes) == 4 and taken.slices[0].byte_count == 4
    with pytest.raises(TemporarilyUnavailable):
        await pool.take(0)
    source.use_source(source.TestSource())  # the real client needs a network; S08 owns its tests
    try:
        assert len((await source.fetch(1)).bytes) == 1024
    finally:
        source.use_source(None)
    with pytest.raises(InvalidArgument):
        await source.fetch(513)
    assert pool.status().high_mark_bytes == 131072
    assert meter.get_usage()["budget"] == {"capKib": 10240, "usedKib": 0}
    assert BudgetExhausted("x").http_status == 503


def test_status_and_token_stubs():
    assert "qrypt_pool_level_bytes" in status.get_metrics()
    assert tokens.redeem("u", "1") == "redeemed"
    assert len(hash_key("k")) == 32
    assert create_credential("client", "x")[0].role == "client"
    assert authenticate(STUB_CLIENT_KEY, {"client"}).name == "stub-client"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_random_bytes", {"length": 0, "encoding": "hex"}),
        ("get_random_bytes", {"length": 5000, "encoding": "hex"}),
        ("random_int", {"min": 5, "max": 1}),
        ("generate_token", {"kind": "otp_6", "expiry_seconds": 5}),
        ("generate_token", {"kind": "nope"}),
        ("random_int", {"min": "a", "max": 1}),
    ],
)
async def test_invalid_tool_input_is_invalid_argument(tool, args):
    async with Client(build_server(lambda: STUB_CLIENT_KEY)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert '"error": "invalid_argument"' in result.content[0].text


async def test_issuance_stubs_cover_all_encodings_and_kinds():
    assert (await issuance.get_random_bytes(P, 3, "base64")).value == "AAEC"
    assert (await issuance.generate_token(P, "uuid_v4", "o", 60)).value.endswith("0000")
    assert issuance.call_stats()["callsByTool"]["get_random_bytes"] >= 1
