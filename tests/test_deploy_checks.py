"""S46: the deployed-URL, budget-log and submission scripts pass on a good target and fail on each kind of bad one."""

import http.server
import json
import re
import subprocess
import threading
from pathlib import Path

import pytest

from tools import budget_log, check_deployed, submission_check

ROOT = Path(__file__).resolve().parents[1]


class Stub:
    """A tiny HTTP server answering like the service; `bad` swaps in a wrong answer for one route."""

    def __init__(self, bad: str | None = None):
        self.bad, self.cap, self.used, self.calls = bad, 10240, 5, []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, headers=None):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self):
                stub.calls.append((self.command, self.path))
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                authed = self.headers.get("Authorization") == "Bearer admin-key"
                if self.path == "/health":
                    return self.reply(200, {"nope": 1} if stub.bad == "health" else {"reporting": False})
                if self.path == "/verify":
                    return self.reply(404, {"oops": 1} if stub.bad == "verify" else {"outcome": "unknown"})
                if self.path == "/admin/usage" and (authed or stub.bad == "open"):
                    shape = {"budget": {"capKib": stub.cap, "usedKib": stub.used}, "perClient": []}
                    return self.reply(200, {"budget": 1} if stub.bad == "usage" else shape)
                if self.path == "/admin/budget" and authed:
                    stub.cap = body["capKib"] + (7 if stub.bad == "cap" else 0)
                    return self.reply(200, {"budget": {"capKib": stub.cap, "usedKib": stub.used}})
                if self.path == "/metrics" and authed:
                    return self.reply(200, b"# HELP\nqrypt_up 1\n")
                if stub.bad == "leak" and self.path.startswith("/admin/trace"):
                    return self.reply(404, {"error": "unknown", "token": "abc"})
                if self.path.startswith("/admin/"):
                    return self.reply(401 if not authed else 404, {"error": "unauthorized", "message": "no"})
                return self.reply(404, {"error": "not_found"})

            do_GET = do_POST = do_PUT = handle_any

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub(request):
    s = Stub(getattr(request, "param", None))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "admin-key")
    monkeypatch.setenv("CLIENT_KEY", "client-key")


def statuses(run, *args):
    res = check_deployed.Result()
    run(res, *args)
    return [s for s, _, _ in res.lines]


def test_good_service_passes_the_three_route_checks(stub):
    assert statuses(check_deployed.check_health, stub.url, 1, None) == ["PASS"]
    assert statuses(check_deployed.check_verify, stub.url, "client-key", None) == ["PASS"]
    assert statuses(check_deployed.check_usage, stub.url, "admin-key", None) == ["PASS", "PASS"]


@pytest.mark.parametrize(
    ("stub", "run", "key"),
    [
        ("health", check_deployed.check_health, None),
        ("verify", check_deployed.check_verify, "client-key"),
        ("usage", check_deployed.check_usage, "admin-key"),
        ("open", check_deployed.check_usage, "admin-key"),  # usage answers without a key
    ],
    indirect=["stub"],
)
def test_wrong_shape_fails(stub, run, key):
    args = (stub.url, 1, None) if run is check_deployed.check_health else (stub.url, key, None)
    assert "FAIL" in statuses(run, *args)


def test_health_that_never_answers_fails_after_the_wait():
    assert statuses(check_deployed.check_health, "http://127.0.0.1:9", 0, None) == ["FAIL"]


def test_plain_http_target_is_not_tls(stub):
    assert statuses(check_deployed.check_plain_http, stub.url) == ["FAIL"]
    assert statuses(check_deployed.check_tls11, stub.url, None) == ["FAIL"]


def test_plain_http_answering_200_fails_and_a_redirect_or_refusal_passes(monkeypatch):
    def answer(status, headers):
        monkeypatch.setattr(check_deployed, "request", lambda *a, **k: (status, headers, ""))
        return statuses(check_deployed.check_plain_http, "https://svc.example")

    assert answer(200, {}) == ["FAIL"]
    assert answer(301, {"location": "http://elsewhere"}) == ["FAIL"]
    assert answer(308, {"location": "https://svc.example/health"}) == ["PASS"]

    def refuse(*a, **k):
        raise ConnectionRefusedError

    monkeypatch.setattr(check_deployed, "request", refuse)
    assert statuses(check_deployed.check_plain_http, "https://svc.example") == ["PASS"]


def test_routes_pass_on_a_clean_service_and_fail_on_an_entropy_key(stub):
    assert check_deployed.main(["routes", stub.url, "--wait", "1"]) == 0


@pytest.mark.parametrize("stub", ["leak"], indirect=True)
def test_routes_fail_on_an_entropy_like_key(stub):
    assert check_deployed.main(["routes", stub.url, "--wait", "1"]) == 1


def test_missing_keys_fail(monkeypatch, stub):
    monkeypatch.delenv("ADMIN_KEY")
    assert check_deployed.main(["routes", stub.url]) == 1


def test_budget_cap_sets_and_reads_back(stub):
    assert budget_log.main(["cap", stub.url, "--spent", "97"]) == 0
    assert stub.cap == 10240 - 97


@pytest.mark.parametrize("stub", ["cap"], indirect=True)
def test_budget_cap_that_does_not_read_back_fails(stub):
    assert budget_log.main(["cap", stub.url, "--spent", "97"]) == 1


def test_budget_reading_checks_the_total(stub, capsys):
    assert budget_log.main(["reading", stub.url, "--spent", "100"]) == 0
    assert "| production (final reading) | 5 | 105 |" in capsys.readouterr().out
    assert budget_log.main(["reading", stub.url, "--spent", "10240"]) == 1  # 5 + 10240 is over the limit
    assert budget_log.main(["cap", stub.url, "--spent", "20000"]) == 1  # not a possible amount


def test_spent_comes_from_the_log_and_skips_production_rows():
    log = (
        "| 2026-10-17 | start | 0 | 0 | x |\n| 2026-10-18 | spike | 97 | 97 | x |\n"
        "| 2026-10-20 | production | 3 | 100 | x |\n"
    )
    assert budget_log.spent_from_log(log) == 97
    assert budget_log.spent_from_log((ROOT / "docs/DEPLOYMENT_GUIDE.md").read_text()) == 0


# ------------------------------------------------------------------------------------------------ submission-check


def repo(tmp_path, when="2026-10-19T12:00:00+00:00", author="A <a@x.org>", files=True):
    def git(*a, env=None):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, env=env)

    tmp_path.mkdir(parents=True, exist_ok=True)
    git("init", "-q")
    if files:
        for _, rel in submission_check.REQUIRED.items():
            target = tmp_path / rel
            (target if rel == "src/qrypt_entropy" else target.parent).mkdir(parents=True, exist_ok=True)
            if rel == "src/qrypt_entropy":
                target.joinpath("__init__.py").write_text("")
            else:
                target.write_text("A short description\n" if rel == "README.md" else "x\n")
    (tmp_path / "keep").write_text("x")
    git("add", "-A")
    name, email = re.match(r"(.*) <(.*)>", author).groups()
    env = {
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "PATH": "/usr/bin:/bin",
    }  # fmt: skip
    git("commit", "-q", "-m", "m", env=env)
    return tmp_path


def test_files_present_and_missing(tmp_path):
    root = repo(tmp_path)
    assert submission_check.check_files(root)[0] == "PASS"
    (root / "AI_USAGE.md").unlink()
    status, detail = submission_check.check_files(root)
    assert status == "FAIL" and "AI_USAGE.md" in detail


def test_description_limit(tmp_path):
    (tmp_path / "README.md").write_text("x" * 119 + "\nmore\n")
    assert submission_check.check_description(tmp_path)[0] == "PASS"
    (tmp_path / "README.md").write_text("x" * 120 + "\nmore\n")
    assert submission_check.check_description(tmp_path)[0] == "FAIL"


def test_deadline_is_inclusive_at_the_cutoff(tmp_path):
    assert submission_check.check_deadline(repo(tmp_path / "a", when="2026-10-21T04:59:00+00:00"))[0] == "PASS"
    assert submission_check.check_deadline(repo(tmp_path / "b", when="2026-10-21T05:00:00+00:00"))[0] == "FAIL"
    # 23:59 CDT is 04:59 UTC the next day; one minute later in CDT is past it
    assert submission_check.check_deadline(repo(tmp_path / "c", when="2026-10-20T23:59:00-05:00"))[0] == "PASS"
    assert submission_check.check_deadline(repo(tmp_path / "d", when="2026-10-21T00:00:00-05:00"))[0] == "FAIL"


def test_authors_and_dirty_tree(tmp_path):
    root = repo(tmp_path)
    assert submission_check.check_authors(root)[0] == "PASS"
    assert submission_check.check_clean(root)[0] == "PASS"
    (root / "new").write_text("x")
    assert submission_check.check_clean(root)[0] == "FAIL"
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=B", "-c", "user.email=b@x.org", "commit", "-qm", "two"], check=True
    )
    status, detail = submission_check.check_authors(root)
    assert status == "FAIL" and "2 authors" in detail


def test_last_push_is_not_checked_without_gh(tmp_path, monkeypatch):
    root = repo(tmp_path)
    monkeypatch.setattr(submission_check.shutil, "which", lambda name: None)
    status, detail = submission_check.check_last_push(root)
    assert status == "NOT CHECKED" and "gh" in detail


def test_last_push_reads_gh(tmp_path, monkeypatch):
    root = repo(tmp_path / "repo")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    run = {"headSha": head, "status": "completed", "conclusion": "success", "workflowName": "gate", "url": "u"}

    def gh(runs, code=0):
        fake.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(runs)}\nEOF\nexit {code}\n")
        fake.chmod(0o755)

    gh([run])
    assert submission_check.check_last_push(root)[0] == "PASS"
    gh([{**run, "conclusion": "failure"}])
    assert submission_check.check_last_push(root)[0] == "FAIL"
    gh([{**run, "headSha": "0" * 40}])
    assert submission_check.check_last_push(root)[0] == "NOT CHECKED"
    gh([], code=1)
    assert submission_check.check_last_push(root)[0] == "NOT CHECKED"


def test_exit_codes(tmp_path, monkeypatch):
    ok = ("PASS", "")
    monkeypatch.setattr(submission_check, "CHECKS", [("a", lambda r: ok)])
    assert submission_check.run(tmp_path) == 0
    monkeypatch.setattr(submission_check, "CHECKS", [("a", lambda r: ok), ("b", lambda r: ("NOT CHECKED", ""))])
    assert submission_check.run(tmp_path) == 2
    monkeypatch.setattr(
        submission_check, "CHECKS", [("b", lambda r: ("NOT CHECKED", "")), ("c", lambda r: ("FAIL", ""))]
    )
    assert submission_check.run(tmp_path) == 1


def test_secret_scan_check_reports_a_secret(tmp_path):
    root = repo(tmp_path)
    (root / "tools").symlink_to(ROOT / "tools")  # the check runs `python -m tools.secret_scan` in the repository
    (root / "leak.txt").write_text("".join(["AKIA", "ABCDEFGHIJKLMNOP"]) + "\n")
    assert submission_check.check_secrets(root)[0] == "FAIL"
