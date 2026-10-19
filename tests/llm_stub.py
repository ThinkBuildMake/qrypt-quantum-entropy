"""A local OpenAI-compatible chat-completions server that plays a tool-calling model by fixed rules (S32).

It stands in for OpenRouter so `OpenRouterModel` (the openai SDK) is exercised over real HTTP with no credentials.
It is NOT a language model: it matches phrases, picks a tool and repeats the placeholder it is shown. It records every
request body it receives (`requests`), which is what OpenRouter would have seen (CP-5). Setting `fail_status` makes it
answer that HTTP status instead. Also runnable on its own: `python -m tests.llm_stub --port 8098`.
"""

import argparse
import asyncio
import json
import re
import uuid

TOOL_PHRASES = [  # (pattern on the user's text, tool name, builder of the arguments from the text)
    (
        r"random bytes",
        "get_random_bytes",
        lambda t: {
            "length": _int(r"(\d+)\s*random bytes", t, 16),
            "encoding": "base64" if "base64" in t else "hex",
        },
    ),
    (r"roll a die", "random_int", lambda t: {"min": 1, "max": 6}),
    (
        r"random (whole )?(number|integer)",
        "random_int",
        lambda t: dict(zip(("min", "max"), _two(t, (1, 100)), strict=True)),
    ),
    (r"uuid", "generate_token", lambda t: {"kind": "uuid_v4"}),
    (r"api key", "generate_token", lambda t: _code(t, "api_key")),
    (r"(reset|one-time|otp|code)", "generate_token", lambda t: _code(t)),
]
REFUSAL_PHRASES = r"ignore the budget|raise the cap|admin|secret|pepper|system prompt"
REFUSE = (
    "I can't do that: I can't change the budget or my role, and I don't reveal secrets or my instructions. "
    "I can issue a real code, key or number with my tools if you like."
)
DECLINE = (
    "I can't make up a code, number or key myself, because it would not be real. "
    "I can issue a real one for you with my tools if you like."
)
NO_TOKENS = "I can't show other users' tokens or a list of tokens issued: I have no way to read stored tokens."
HELP = "I can issue one-time codes, random bytes, random numbers and API keys. What do you need?"


def _int(pattern: str, text: str, default: int) -> int:
    found = re.search(pattern, text)
    return int(found.group(1)) if found else default


def _two(text: str, default: tuple[int, int]) -> tuple[int, int]:
    found = re.search(r"(?:between|from) (\d+) (?:and|to) (\d+)", text)
    return (int(found.group(1)), int(found.group(2))) if found else default


def _code(text: str, kind: str = "otp_6") -> dict:
    args: dict = {"kind": kind}
    if user := re.search(r"user[ -]?(\w+)", text):
        args["owner"] = f"user-{user.group(1)}"
    elif owner := re.search(r"([\w.]+@[\w.]+\w)|owned by ([\w-]+)", text):
        args["owner"] = owner.group(1) or owner.group(2)
    if amount := re.search(r"(\d+)\s*(minute|hour)s?", text):
        args["expiry_seconds"] = int(amount.group(1)) * (60 if amount.group(2) == "minute" else 3600)
    return args


def decide(messages: list[dict]) -> dict:
    """The next assistant message for this conversation: a tool call, or text that repeats the placeholder."""
    last = messages[-1]
    if last["role"] == "tool":
        body = json.loads(last["content"])
        if "error" in body:
            return {"role": "assistant", "content": "I could not issue that."}
        return {"role": "assistant", "content": f"Here it is: {body['value']}"}
    text = (last.get("content") or "").lower()
    if re.search(REFUSAL_PHRASES, text):
        return {"role": "assistant", "content": REFUSE}
    if re.search(r"make up|invent|guess|pretend", text):
        return {"role": "assistant", "content": DECLINE}
    if re.search(r"every token|all tokens|other users|everyone's", text):
        return {"role": "assistant", "content": NO_TOKENS}
    for pattern, tool, arguments in TOOL_PHRASES:
        if re.search(pattern, text):
            call = {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function"}
            call["function"] = {"name": tool, "arguments": json.dumps(arguments(text))}
            return {"role": "assistant", "content": None, "tool_calls": [call]}
    return {"role": "assistant", "content": HELP}


class LlmStub:
    def __init__(self, port: int = 0) -> None:
        self.port = port
        self.requests: list[dict] = []
        self.fail_status: int | None = None
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()  # keep-alive connections, closed on exit

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    async def __aenter__(self) -> LlmStub:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        for writer in self._writers:
            writer.close()
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while head := await reader.readuntil(b"\r\n\r\n"):
                lines = head.decode().split("\r\n")
                headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:] if line)}
                body = json.loads(await reader.readexactly(int(headers.get("content-length", 0))) or b"{}")
                status, payload = self._answer(body)
                data = json.dumps(payload).encode()
                head = f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n"
                writer.write(head.encode() + data)
                await writer.drain()
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            writer.close()

    def _answer(self, body: dict) -> tuple[int, dict]:
        self.requests.append(body)
        if self.fail_status:
            return self.fail_status, {"error": {"message": "stub failure", "code": self.fail_status}}
        message = decide(body["messages"])
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        return 200, {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "stub"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }


async def serve(port: int) -> None:
    async with LlmStub(port) as stub:
        print(f"listening {stub.url}", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8098)
    asyncio.run(serve(parser.parse_args().port))
