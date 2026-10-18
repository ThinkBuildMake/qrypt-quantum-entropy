import uuid

import pytest
from mcp.client import Client

from qrypt_entropy.errors import BudgetExhausted, InvalidArgument
from qrypt_entropy.models import Principal
from qrypt_entropy.service import issuance, pool
from qrypt_entropy.service.access import authenticate, create_credential, hash_key
from qrypt_entropy.transport.mcp_server import build_server
from qrypt_entropy.upstream import source
from tests.conftest import client_key

P = Principal("c", "client", "n")


async def test_source_bounds_and_the_pool_status_stub():
    source.use_source(source.TestSource())  # the real client needs a network; S08 owns its tests
    try:
        assert len((await source.fetch(1)).bytes) == 1024
    finally:
        source.use_source(None)
    with pytest.raises(InvalidArgument):
        await source.fetch(513)
    assert pool.status().high_mark_bytes == 131072
    assert BudgetExhausted("x").http_status == 503


def test_access_is_real():
    assert len(hash_key("k")) == 32
    assert create_credential("client", f"x-{uuid.uuid4().hex}")[0].role == "client"
    assert authenticate(client_key(), {"client"}).name.startswith("test-client")


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
    async with Client(build_server(lambda: client_key(), run_pool=False)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert '"error": "invalid_argument"' in result.content[0].text


async def test_issuance_covers_all_encodings_and_kinds():
    client = authenticate(client_key(), {"client"})  # a principal the gate database knows
    assert (await issuance.get_random_bytes(client, 3, "base64")).value == "AAEC"  # the mock's first bytes
    assert len((await issuance.generate_token(client, "api_key")).value) == 64
    assert issuance.call_stats()["callsByTool"]["get_random_bytes"] >= 1
