import pytest
from mcp.client import Client

from qrypt_entropy.transport.mcp_server import build_server
from tests.conftest import client_key


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
