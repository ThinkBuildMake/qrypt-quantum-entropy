"""S38 leak scan (docs/stories/S38-robust-privacy.md): `make leak-scan`, and a gate case in `make gate`.

One full run of the real system (stdio MCP server processes, a uvicorn HTTP process, the scripted agent, one Postgres,
the committed mock Qrypt) with every secret and every returned value known to this process as a marker. The markers
are generated here and live in memory only: they reach the servers through their environment and are never written to
a file or printed. The system's footprint is then searched in raw, hex and base64 form: every file the processes
wrote, their logs, every HTTP and tool response except the one that deliberately carries a value, the tables, a
database dump, the repository working tree and its full git history.
"""

import asyncio
import base64
import json
import logging
import os
import random
import secrets
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from tests.conftest import GATE_STATS
from tests.gate.test_matrix_and_fuzz import http_routes
from tests.gate.test_robust_surface_gate import route_inputs, tool_inputs
from tests.leakscan import SKIP_DIRS, Finder, Marker
from tests.mock_qrypt import MockQrypt

ROOT = Path(__file__).resolve().parents[2]
SERVER_CMD = 'exec "$0" -m qrypt_entropy.transport.mcp_server 2>>"$STDERR_FILE"'  # stderr kept for the scan
AUDIT_TABLES = ("issuances", "issuance_batches", "redemption_outcomes", "server_status")


@dataclass
class Footprint:
    """What the run left behind, as searchable text; and what the run knew."""

    markers: list[Marker] = field(default_factory=list)
    responses: list[tuple[str, bytes]] = field(default_factory=list)
    logs: dict[str, bytes] = field(default_factory=dict)
    tables: dict[str, bytes] = field(default_factory=dict)
    scratch: dict[str, bytes] = field(default_factory=dict)  # every file the processes wrote
    dump: bytes = b""
    git_history: bytes = b""
    tree: dict[str, bytes] = field(default_factory=dict)
    issued: int = 0
    restart_error: str = ""
    server_logs_exist: bool = False
    pooled_windows: list[bytes] = field(default_factory=list)


class Run:
    """The scenario: builds the world, drives it, and records markers and responses."""

    def __init__(self, db: str, scratch: Path):
        self.db, self.scratch = db, scratch
        for noisy in ("httpx", "httpcore", "mcp", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        self.fp = Footprint()
        self.secrets = {
            name: secrets.token_hex(16) for name in ("pepper", "key_hash", "qrypt", "openrouter", "bad_key")
        }
        self.keys: dict[str, str] = {}
        self.refs: list[tuple[str, str]] = []  # (owner, value) of OTPs not yet redeemed
        self.references: list[str] = []
        self.windows: list[bytes] = []
        self.blocks = 0
        for label, value in self.secrets.items():
            self.mark(f"secret:{label}", value)

    # -- markers and recorded responses ------------------------------------------------------------------------

    def mark(self, label: str, value: str | bytes) -> None:
        self.fp.markers.append(Marker.of(label, value))

    def record(self, where: str, text: str | bytes) -> None:
        """A response that is not the one deliberately carrying a value."""
        self.fp.responses.append((where, text if isinstance(text, bytes) else text.encode("utf-8", "surrogatepass")))

    def entropy(self) -> bytes:
        """One KiB of mock entropy; a few of its windows become markers (pooled bytes), nothing is stored."""
        block, self.blocks = os.urandom(1024), self.blocks + 1
        if self.blocks % 4 == 1 and len(self.windows) < 40:
            self.windows += [block[:32], block[500:532]]
        return block

    # -- the world ---------------------------------------------------------------------------------------------

    def env(self, name: str, **extra: str) -> dict[str, str]:
        logs = self.scratch / "logs"
        logs.mkdir(exist_ok=True)
        return {
            "PATH": os.environ["PATH"],
            "HOME": str(self.scratch),
            "TMPDIR": str(self.scratch),
            "DATABASE_URL": self.db,
            "KEY_HASH_SECRET": self.secrets["key_hash"],
            "PEPPER": self.secrets["pepper"],
            "TOKEN_PEPPER": self.secrets["pepper"],
            "QRYPT_BASE_URL": self.mock.url,
            "QRYPT_API_KEY": self.secrets["qrypt"],
            "OPENROUTER_API_KEY": self.secrets["openrouter"],  # no process but the agent should even look at it
            "LOG_FILE": str(logs / f"{name}.log"),
            "STDERR_FILE": str(logs / f"{name}.stderr"),
            **extra,
        }

    def mcp(self, key: str, name: str, **extra: str) -> Client:
        env = self.env(name, MCP_CLIENT_KEY=key, **extra)
        params = StdioServerParameters(
            command="/bin/sh", args=["-c", SERVER_CMD, sys.executable], env=env, cwd=str(self.scratch)
        )
        return Client(params)

    def run_script(self, name: str, args: list[str], **extra: str) -> subprocess.CompletedProcess:
        env = self.env(name, AGENT_LOG_FILE=str(self.scratch / "logs" / f"{name}.log"), **extra)
        done = subprocess.run(  # noqa: S603
            [sys.executable, *args], env=env, cwd=self.scratch, capture_output=True, text=True, timeout=120
        )
        self.record(f"{name} output", done.stderr if name == "seed" else done.stdout + done.stderr)  # seed prints keys
        return done

    async def start(self) -> None:
        self.mock = await MockQrypt(token=self.secrets["qrypt"], enforce_limit=False).__aenter__()
        self.mock.entropy = self.entropy
        with psycopg.connect(self.db) as conn:
            conn.execute("UPDATE budget SET cap_kib = 1000000000")
        seeded = self.run_script("seed", [str(ROOT / "scripts/seed.py")])
        assert seeded.returncode == 0, "seed failed"
        for line in seeded.stdout.splitlines():  # keys and seed-token values: printed once, markers from now on
            fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
            if "key" in fields:
                self.keys[fields["name"]] = fields["key"]
                self.mark(f"key:{fields['name']}", fields["key"])
            if "value" in fields:
                self.mark(f"seed-token:{fields['owner']}", fields["value"])
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.web_out = (self.scratch / "logs" / "uvicorn.out").open("wb")
        self.web = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(port)],
            env=self.env("http"), cwd=self.scratch, stdout=self.web_out, stderr=self.web_out,
        )  # fmt: skip
        self.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30)
        for _ in range(200):
            try:
                await self.http.get("/health")
                return
            except httpx.TransportError:
                await asyncio.sleep(0.1)
        pytest.fail("the HTTP service did not start")

    async def stop(self) -> None:
        await self.http.aclose()
        self.web.terminate()
        self.web.wait(timeout=15)
        self.web_out.close()
        await self.mock.__aexit__(None, None, None)

    # -- driving -----------------------------------------------------------------------------------------------

    def issued(self, tool: str, args: dict, result) -> None:
        """A tool result: its value is the one intended response, so it is not searched; it becomes a marker."""
        if result.is_error:
            self.record(f"error {tool}", result.content[0].text)
            return
        value = result.structured_content["value"]
        self.fp.issued += 1
        label = f"issued:{tool}:{args.get('kind') or args.get('encoding') or ''}#{self.fp.issued}"
        if tool == "get_random_bytes":
            raw = bytes.fromhex(value) if args["encoding"] == "hex" else base64.b64decode(value)
            if len(raw) >= 8:  # a shorter value is searched nowhere: it would match by chance
                self.mark(label, raw)
        elif isinstance(value, str) and len(value) >= 6:
            self.mark(label, value)
            if args.get("kind") == "otp_6":
                self.refs.append((args["owner"], value))
        elif isinstance(value, int) and len(str(value)) >= 6:
            self.mark(label, str(value))
        self.references.append(result.structured_content["metadata"]["reference"])

    async def call(self, client: Client, tool: str, args: dict) -> None:
        try:
            json.dumps(args, ensure_ascii=False, allow_nan=False).encode()
        except ValueError:
            return  # the client cannot put a lone surrogate or NaN on the wire (the in-process fuzz sends them)
        try:
            self.issued(tool, args, await client.call_tool(tool, args))
        except Exception as err:  # noqa: BLE001  a protocol-level failure is a response too
            self.record(f"exception {tool}", f"{type(err).__name__}: {err}")

    async def get(self, method: str, path: str, key: str | None = None, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        resp = await self.http.request(method, path, headers=headers, **kw)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if method == "POST" and path == "/admin/clients" and resp.status_code < 300 and "key" in body:
            self.mark("key:created-client", body["key"])  # the one intended response of this route
        else:
            self.record(f"{method} {path.split('?')[0]} {resp.status_code}", resp.text + str(dict(resp.headers)))
        return resp

    async def issue_and_redeem(self) -> None:
        client_key = self.keys["billing-agent"]
        async with self.mcp(client_key, "issue") as client:
            for i in range(4):
                for encoding in ("hex", "base64"):
                    await self.call(client, "get_random_bytes", {"length": 16 * (i + 1), "encoding": encoding})
            await self.call(client, "get_random_bytes", {"length": 1024, "encoding": "hex"})
            await self.call(client, "random_int", {"min": 100000, "max": 999999999})
            for i in range(6):
                for kind in ("otp_6", "uuid_v4", "api_key"):
                    await self.call(client, "generate_token", {"kind": kind, "owner": f"owner-{i}"})
            await self.call(client, "generate_token", {"kind": "no_such_kind", "owner": "x"})
            await self.call(client, "get_random_bytes", {"length": 5000, "encoding": "hex"})
        for owner, value in self.refs[:3]:
            await self.get("POST", "/verify", client_key, json={"owner": owner, "value": value})
        owner, value = self.refs[0]
        await self.get("POST", "/verify", client_key, json={"owner": owner, "value": value})  # already used
        await self.get("POST", "/verify", client_key, json={"owner": owner, "value": "000000"})  # wrong
        await self.get("POST", "/verify", self.secrets["bad_key"], json={"owner": owner, "value": value})
        await self.get("POST", "/verify", client_key, json={"owner": owner, "value": self.keys["admin"]})
        admin = self.keys["admin"]
        for reference in self.references[:3]:
            await self.get("GET", f"/admin/issuances/{reference}", admin)
        await self.get("GET", f"/admin/issuances/{self.references[0]}", client_key)
        await self.get("GET", "/admin/usage", admin)
        await self.get("GET", "/metrics", admin)
        await self.get("GET", "/health")
        await self.get("POST", "/admin/clients", admin, json={"name": "leak-scan-client"})
        await self.get("PUT", "/admin/budget", admin, json={"capKib": 1000000000})

    async def fuzz_and_load(self) -> None:
        rng = random.Random(38)
        async with self.mcp(self.keys["billing-agent"], "fuzz") as client:
            for name, args in list(tool_inputs(rng, 150)):
                await self.call(client, name, args)
            await self.call(client, "get_random_bytes", {"length": 8, "encoding": "hex"})
        async with (
            self.mcp(self.keys["billing-agent"], "load-a") as a,
            self.mcp(self.keys["support-agent"], "load-b") as b,
        ):
            work = [
                (
                    a if i % 2 else b,
                    "generate_token",
                    {"kind": ("api_key", "uuid_v4", "otp_6")[i % 3], "owner": f"load-{i % 7}"},
                )
                for i in range(90)
            ] + [(a, "get_random_bytes", {"length": 32, "encoding": "hex"}) for _ in range(30)]
            for start in range(0, len(work), 20):
                await asyncio.gather(*(self.call(c, t, args) for c, t, args in work[start : start + 20]))

    async def failures(self) -> None:
        """A Qrypt that rejects the configured token (the token must not reach an error or a log), and a restart
        with Qrypt down: the new process has an empty pool, so nothing can be served from before."""
        async with MockQrypt(token="some-other-token", enforce_limit=False) as wrong:  # noqa: S106
            env = {"QRYPT_BASE_URL": wrong.url}
            async with self.mcp(self.keys["billing-agent"], "wrong-qrypt-token", **env) as client:
                await self.call(client, "get_random_bytes", {"length": 8, "encoding": "hex"})
        self.mock.sticky = 503
        async with self.mcp(self.keys["billing-agent"], "restart") as client:
            result = await client.call_tool("get_random_bytes", {"length": 8, "encoding": "hex"})
            self.fp.restart_error = json.loads(result.content[0].text)["error"] if result.is_error else ""
            self.record("restart call", result.content[0].text)
        self.mock.sticky = None
        agent = self.run_script("agent", ["-m", "qrypt_entropy.agent", "scripted", "give me a code"])
        assert agent.returncode == 0, "the scripted agent run failed"

    async def route_fuzz(self) -> None:
        rng = random.Random(39)
        routes = [r for r, _ in http_routes()]
        keys = {"admin": self.keys["admin"], "client": self.keys["billing-agent"]}
        for method, path, headers, content in route_inputs(rng, routes, 150, keys):
            try:
                resp = await self.http.request(method, path, headers=headers, content=content)
            except httpx.HTTPError as err:
                self.record("route fuzz", f"{type(err).__name__}")
                continue
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if method == "POST" and path == "/admin/clients" and resp.status_code < 300 and "key" in body:
                self.mark("key:fuzz-client", body["key"])
            else:
                self.record(f"fuzz {method} {resp.status_code}", resp.text + str(dict(resp.headers)))

    # -- collecting what the run left ---------------------------------------------------------------------------

    def collect(self) -> None:
        for i, window in enumerate(self.windows):
            self.mark(f"pooled:{i}", window)
        self.fp.pooled_windows = self.windows
        for path in sorted(self.scratch.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                self.fp.scratch[str(path.relative_to(self.scratch))] = data
                if path.suffix in (".log", ".stderr", ".out"):
                    self.fp.logs[path.name] = data
        dump = subprocess.run(["pg_dump", "--no-owner", self.db], capture_output=True, check=True)  # noqa: S603, S607
        self.fp.dump = dump.stdout
        with psycopg.connect(self.db) as conn:
            names = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
            for name in names:
                rows = conn.execute(f'SELECT row_to_json(t)::text FROM "{name}" t').fetchall()  # noqa: S608
                self.fp.tables[name] = "\n".join(r[0] for r in rows).encode()
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in filenames:
                path = Path(dirpath, filename)
                if path.is_file() and not path.is_symlink() and path.stat().st_size < 20_000_000:
                    self.fp.tree[str(path.relative_to(ROOT))] = path.read_bytes()
        history = subprocess.run(  # noqa: S603
            ["git", "log", "--all", "-p", "--no-color"],
            cwd=ROOT,
            capture_output=True,
            check=True,  # noqa: S607
        )
        self.fp.git_history = history.stdout


_RUN: dict[str, Footprint] = {}


async def full_run(db: str, scratch: Path) -> Footprint:
    """The whole scenario, once per process (the cases for each acceptance criterion share it)."""
    if "fp" not in _RUN:
        run = Run(db, scratch)
        await run.start()
        try:
            await run.issue_and_redeem()
            await run.fuzz_and_load()
            await run.failures()
            await run.route_fuzz()
        finally:
            await run.stop()
        run.collect()
        _RUN["fp"] = run.fp
    return _RUN["fp"]


def report(findings: list[str], what: str) -> None:
    """Labels and places only: a failure message must never hold a marker."""
    if findings:
        pytest.fail(f"{what}: {len(findings)} match(es): " + "; ".join(sorted(findings)[:12]), pytrace=False)


def hits(finder: Finder, corpus: dict[str, bytes] | list[tuple[str, bytes]], **kw) -> list[str]:
    items = corpus.items() if isinstance(corpus, dict) else corpus
    return [f"{where}: {label} as {form}" for where, data in items for label, form in finder.search(data, **kw)]


@pytest.fixture
async def fp(db, tmp_path_factory) -> Footprint:
    return await full_run(db, tmp_path_factory.mktemp("leakscan"))


def test_ac1_no_issued_value_in_logs_errors_or_responses(fp):
    finder = Finder(fp.markers)
    issued = [m for m in fp.markers if m.label.startswith("issued:")]
    assert len(issued) >= 60, "the run must have issued many values"
    report(hits(finder, fp.logs), "marker in a log")
    report(hits(finder, fp.responses), "marker in a response other than the intended one")
    GATE_STATS["leak scan: values issued and searched (raw, hex, base64)"] = f"{fp.issued}, markers {len(fp.markers)}"
    GATE_STATS["leak scan: logs and responses searched"] = (
        f"{len(fp.logs)} logs, {len(fp.responses)} responses, 0 matches"
    )


def test_ac2_no_value_in_the_audit_tables(fp):
    finder = Finder(fp.markers)
    assert set(AUDIT_TABLES) <= set(fp.tables)
    assert all(fp.tables[name] for name in AUDIT_TABLES[:3]), "the audit tables must hold rows from the run"
    report(hits(finder, fp.tables), "marker in a table")
    report(hits(finder, {"database dump": fp.dump}), "marker in the database dump")
    GATE_STATS["leak scan: tables and dump searched"] = f"{len(fp.tables)} tables + dump, 0 matches"


def test_ac3_no_pooled_byte_on_disk_and_a_restarted_pool_is_empty(fp):
    finder = Finder(fp.markers)
    assert len(fp.pooled_windows) >= 20
    assert fp.scratch, "the run wrote files (logs) that can be searched"
    report(hits(finder, fp.scratch), "marker in a file the application wrote")
    pooled = Finder([m for m in fp.markers if m.label.startswith("pooled:")][:8])
    report(hits(pooled, fp.tree, include_short=False), "pooled bytes in the working tree")
    report(hits(pooled, {"git history": fp.git_history}, include_short=False), "pooled bytes in git history")
    assert fp.restart_error == "temporarily_unavailable", f"a restarted pool must be empty, got {fp.restart_error!r}"
    GATE_STATS["leak scan: files searched"] = f"{len(fp.scratch)} written + {len(fp.tree)} in the tree, 0 matches"


def test_secrets_not_in_repository_tree_or_history(fp):
    """CP-1/CP-2: no marker secret or long value in the working tree (logs/, results/, backups/) or any commit."""
    finder = Finder(fp.markers)
    report(hits(finder, fp.tree, include_short=False), "marker in the working tree")
    report(hits(finder, {"git history": fp.git_history}, include_short=False), "marker in git history")
    GATE_STATS["leak scan: git history searched"] = f"{len(fp.git_history)} bytes, 0 matches"


def test_ac4_every_log_line_is_json_and_tool_calls_have_a_duration(fp):
    lines = bad = tool_calls = 0
    problems = []
    for name, data in fp.logs.items():
        for number, line in enumerate(data.decode(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            lines += 1
            try:
                event = json.loads(line)
            except ValueError:
                bad += 1
                problems.append(f"{name}:{number} is not JSON")
                continue
            if str(event.get("event", "")).startswith("tool_call"):
                tool_calls += 1
                if "duration_ms" not in event:
                    problems.append(f"{name}:{number} tool call without duration_ms")
    print(f"\nleak-scan log lines parsed: {lines - bad} of {lines} ({tool_calls} tool-call lines with duration_ms)")
    assert lines > 100 and tool_calls > 100
    report(problems, "log lines")
    GATE_STATS["leak scan: log lines parsed as JSON"] = f"{lines} of {lines}, {tool_calls} tool calls with duration_ms"


# -- the scanner itself: it must find what it looks for, and must not leak ---------------------------------------------


def test_the_scanner_finds_every_form_of_a_marker():
    raw = os.urandom(24)
    text = secrets.token_hex(12)
    finder = Finder([Marker.of("bytes", raw), Marker.of("text", text), Marker.of("code", "731905")])
    for form in (
        raw,
        raw.hex().encode(),
        raw.hex().upper().encode(),
        base64.b64encode(raw),
        base64.urlsafe_b64encode(raw).rstrip(b"="),
    ):
        assert ("bytes", "raw") in finder.search(b"x " + raw + b" y") and finder.search(b"..." + form + b"...")
    assert (
        finder.search(text.encode())
        and finder.search(text.encode().hex().encode())
        and finder.search(base64.b64encode(text.encode()))
    )
    assert finder.search(b"a code 731905 here") and finder.search(b'{"code": "731905"}')
    assert not finder.search(b"2026-10-18T10:20:31.731905Z 1731905 x731905 731905x 7319051 731905.5")
    assert not finder.search(os.urandom(4096))


def test_a_failed_scan_names_labels_not_values():
    finder = Finder([Marker.of("secret:pepper", "p3pper-value-0123456789")])
    with pytest.raises(pytest.fail.Exception) as failure:
        report(hits(finder, {"a.log": b"leaked p3pper-value-0123456789 here"}), "marker in a log")
    assert "secret:pepper" in str(failure.value) and "p3pper-value" not in str(failure.value)
