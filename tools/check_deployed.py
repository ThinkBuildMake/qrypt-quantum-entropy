"""S46: checks against the deployed service, from outside (stdlib only).

`make check-deployed URL=https://host`  AC2, AC3: POST /verify, GET /admin/usage and GET /health answer with their
                                          contract shape over TLS; plain http:// and TLS 1.1 are refused or redirected.
`make check-routes URL=https://host`    AC4: the read-only hosted routes return no entropy-derived value.

Keys come from the environment (`CLIENT_KEY`, `ADMIN_KEY`), never from arguments. Nothing here prints a key.
Exit 0: every check passed. 1: a check failed. 2: nothing failed but a check could not be made ("not checked").
"""

import argparse
import http.client
import json
import os
import socket
import ssl
import sys
import time
import urllib.parse
import uuid
import warnings

TIMEOUT = 20.0
VERIFY_OUTCOMES = {"redeemed", "unknown", "already_used", "expired", "rate_limited"}
ENTROPY_KEYS = {"value", "token", "bytes", "random", "randomInt", "int", "otp", "secret"}
# Hosted routes that change nothing: (method, path, role, allowed top-level keys; None = Prometheus text).
READ_ONLY_ROUTES = [
    ("GET", "/health", None, {"reporting", "reportAgeSeconds", "pool", "upstream", "status", "ok", "error", "message"}),
    ("GET", "/metrics", "admin", None),
    ("POST", "/verify", "client", {"outcome", "error", "message"}),
    ("GET", "/admin/usage", "admin", {"budget", "perClient"}),
    ("POST", "/admin/trace-token", "admin", {"error", "message"}),
    ("GET", "/admin/issuances/{reference}", "admin", {"error", "message"}),
]


class Result:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.lines.append((status, name, detail))
        print(f"{status:<11} {name}" + (f": {detail}" if detail else ""), flush=True)

    @property
    def code(self) -> int:
        statuses = {s for s, _, _ in self.lines}
        return 1 if "FAIL" in statuses else 2 if "NOT CHECKED" in statuses else 0


def context(cafile: str | None) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=cafile)


def request(
    url: str, method: str = "GET", key: str | None = None, body: dict | None = None, cafile: str | None = None
) -> tuple[int, dict[str, str], str]:
    """One request, no redirects followed. Returns (status, lowercased headers, body text)."""
    parts = urllib.parse.urlsplit(url)
    make = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    extra = {"context": context(cafile)} if parts.scheme == "https" else {}
    conn = make(parts.hostname, parts.port, timeout=TIMEOUT, **extra)
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, parts.path + (f"?{parts.query}" if parts.query else ""), payload, headers)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def as_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def wait_for_health(url: str, wait: float, cafile: str | None) -> tuple[int, dict | None, str]:
    """GET /health, retrying until it answers 200 or `wait` seconds pass (a free service wakes in ~1 minute)."""
    deadline = time.monotonic() + wait
    while True:
        try:
            status, _, text = request(f"{url}/health", cafile=cafile)
            if status == 200:
                return status, as_json(text), text
            last = f"HTTP {status}"
        except ssl.SSLCertVerificationError as err:
            return 0, None, f"certificate not trusted: {err.reason}"  # waiting will not fix it
        except (OSError, http.client.HTTPException) as err:
            last = type(err).__name__
        if time.monotonic() >= deadline:
            return 0, None, last
        time.sleep(3)


def check_health(res: Result, url: str, wait: float, cafile: str | None) -> None:
    status, data, text = wait_for_health(url, wait, cafile)
    if status != 200:
        res.add("FAIL", "GET /health", f"no 200 within {wait:.0f} s ({text})")
    elif not isinstance(data, dict) or "reporting" not in data:
        res.add("FAIL", "GET /health", "200 but the body is not the health shape (no `reporting`)")
    else:
        res.add("PASS", "GET /health", f"200, reporting={data['reporting']}")


def check_verify(res: Result, url: str, key: str, cafile: str | None) -> None:
    status, _, text = request(f"{url}/verify", "POST", key, {"owner": "check-deployed", "value": "000000"}, cafile)
    data = as_json(text)
    if not isinstance(data, dict) or data.get("outcome") not in VERIFY_OUTCOMES:
        res.add("FAIL", "POST /verify", f"HTTP {status}, body is not a verdict ({text[:80]!r})")
    else:
        res.add("PASS", "POST /verify", f"HTTP {status}, outcome={data['outcome']}")


def check_usage(res: Result, url: str, key: str, cafile: str | None) -> None:
    status, _, text = request(f"{url}/admin/usage", "GET", key, cafile=cafile)
    data = as_json(text)
    budget = data.get("budget") if isinstance(data, dict) else None
    ok = (
        status == 200
        and isinstance(budget, dict)
        and type(budget.get("capKib")) is int
        and type(budget.get("usedKib")) is int
        and isinstance(data.get("perClient"), list)
    )
    if not ok:
        res.add("FAIL", "GET /admin/usage", f"HTTP {status}, not the usage shape ({text[:80]!r})")
        return
    res.add("PASS", "GET /admin/usage", f"200, capKib={budget['capKib']} usedKib={budget['usedKib']}")
    status, _, _ = request(f"{url}/admin/usage", cafile=cafile)
    res.add("PASS" if status == 401 else "FAIL", "GET /admin/usage without a key", f"HTTP {status}, expected 401")


def check_plain_http(res: Result, url: str) -> None:
    """AC3: the http:// form of the same host is refused (no connection) or redirected to https."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        res.add("FAIL", "plain http refused or redirected", "the target is not https, so there is no TLS")
        return
    plain = f"http://{parts.hostname}" + (f":{parts.port}" if parts.port else "") + "/health"
    try:
        status, headers, _ = request(plain)
    except (OSError, http.client.HTTPException) as err:
        res.add("PASS", "plain http refused or redirected", f"refused ({type(err).__name__})")
        return
    if status in (301, 302, 307, 308) and headers.get("location", "").startswith("https://"):
        res.add("PASS", "plain http refused or redirected", f"HTTP {status} to {headers['location']}")
    else:
        res.add("FAIL", "plain http refused or redirected", f"{plain} answered HTTP {status} over plain http")


def check_tls11(res: Result, url: str, cafile: str | None) -> None:
    """AC3: a client that offers only TLS 1.1 cannot complete a handshake."""
    name = "TLS 1.1 refused"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        res.add("FAIL", name, "the target is not https")
        return
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_1
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        ctx.load_verify_locations(cafile) if cafile else ctx.load_default_certs()
    except (ValueError, ssl.SSLError) as err:
        res.add("NOT CHECKED", name, f"this machine's OpenSSL cannot offer TLS 1.1 ({err})")
        return
    try:
        with socket.create_connection((parts.hostname, parts.port or 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=parts.hostname) as tls:
                res.add("FAIL", name, f"handshake completed with {tls.version()}")
    except ssl.SSLCertVerificationError as err:
        res.add("NOT CHECKED", name, f"certificate not trusted, so the handshake result is unclear ({err.reason})")
    except (ssl.SSLError, ConnectionError, EOFError) as err:
        res.add("PASS", name, f"handshake refused ({type(err).__name__})")
    except OSError as err:
        res.add("NOT CHECKED", name, f"no connection to make the attempt ({type(err).__name__})")


def entropy_leaves(node, path: str = "") -> list[str]:
    """Paths of keys that name an entropy-derived value, anywhere in a JSON body."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}"
            if k in ENTROPY_KEYS:
                found.append(here)
            found += entropy_leaves(v, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += entropy_leaves(v, f"{path}[{i}]")
    return found


def check_routes(res: Result, url: str, keys: dict[str, str], cafile: str | None) -> None:
    """AC4: each read-only hosted route called once with the right key; no response holds an entropy-derived value."""
    ids = {"{reference}": str(uuid.uuid4())}
    bodies = {
        "/verify": {"owner": "check-routes", "value": "000000"},
        "/admin/trace-token": {"owner": "x", "value": "000000"},
    }
    for method, path, role, allowed in READ_ONLY_ROUTES:
        target = path
        for token, value in ids.items():
            target = target.replace(token, value)
        name = f"{method} {path}"
        try:
            status, _, text = request(url + target, method, keys.get(role) if role else None, bodies.get(path), cafile)
        except (OSError, http.client.HTTPException) as err:
            res.add("FAIL", name, f"no answer ({type(err).__name__})")
            continue
        if status not in (200, 404, 409, 410, 429):
            res.add("FAIL", name, f"HTTP {status}")
        elif allowed is None:
            bad = [ln for ln in text.splitlines() if ln and not ln.startswith("#") and not ln.startswith("qrypt_")]
            res.add(
                "FAIL" if bad else "PASS",
                name,
                f"{len(bad)} non-metric lines" if bad else f"HTTP {status}, metrics only",
            )
        else:
            data = as_json(text)
            extra = sorted(set(data) - allowed) if isinstance(data, dict) else ["<not an object>"]
            leaves = entropy_leaves(data)
            if extra or leaves:
                res.add("FAIL", name, f"unexpected keys {extra} entropy-like keys {leaves}")
            else:
                res.add("PASS", name, f"HTTP {status}, no entropy-derived value")


def need_keys(res: Result, roles: tuple[str, ...]) -> dict[str, str] | None:
    names = {"client": "CLIENT_KEY", "admin": "ADMIN_KEY"}
    keys = {r: os.environ.get(names[r], "") for r in roles}
    missing = [names[r] for r, v in keys.items() if not v]
    if missing:
        print(f"FAIL        set {' and '.join(missing)} in your shell (the keys `make seed` printed)", file=sys.stderr)
        return None
    return keys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["deployed", "routes"])
    ap.add_argument("url", help="https://host of the service")
    ap.add_argument("--wait", type=float, default=90.0, help="seconds to keep retrying /health (cold start)")
    ap.add_argument("--cafile", default=os.environ.get("CHECK_CAFILE") or None, help="trust this CA (local tests only)")
    args = ap.parse_args(argv)
    url = args.url.rstrip("/")
    res = Result()
    if args.mode == "deployed":
        keys = need_keys(res, ("client", "admin"))
        if keys is None:
            return 1
        check_health(res, url, args.wait, args.cafile)  # first: it also wakes a sleeping service
        for check in (check_verify, check_usage):
            try:
                check(res, url, keys["client" if check is check_verify else "admin"], args.cafile)
            except (OSError, http.client.HTTPException) as err:
                res.add("FAIL", check.__name__, f"no answer ({type(err).__name__}: {err})")
        check_plain_http(res, url)
        check_tls11(res, url, args.cafile)
    else:
        keys = need_keys(res, ("client", "admin"))
        if keys is None:
            return 1
        wait_for_health(url, args.wait, args.cafile)
        check_routes(res, url, keys, args.cafile)
    verdict = {0: "PASSED", 1: "FAILED", 2: "NOT FULLY CHECKED"}[res.code]
    print(f"check-{args.mode}: {verdict}")
    return res.code


if __name__ == "__main__":
    sys.exit(main())
