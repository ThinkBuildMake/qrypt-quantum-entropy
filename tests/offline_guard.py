"""pytest plugin for `make offline-triple` (EV-4): block every connection that leaves this machine and report.

Loaded with `-p tests.offline_guard`. A connection to a loopback address or a unix socket is allowed (the committed mock
Qrypt and the local Postgres are both on this machine); any other address is refused and counted, and so is a name
lookup that is not for the local machine. At the end of the run it writes OFFLINE_REPORT (a JSON file): the outcome of
every test, the number of refused attempts and the model and Qrypt key variables that were set.
It guards this process only; the MCP servers some tests launch get an explicit environment without the keys.
"""

import hashlib
import ipaddress
import json
import os
import socket

attempts: list[str] = []
# read when this plugin loads, before tests/conftest.py sets the mock's token as QRYPT_API_KEY
keys_at_start = [k for k in ("QRYPT_API_KEY", "OPENROUTER_API_KEY") if os.environ.get(k)]
outcomes: dict[str, str] = {}
_connect, _getaddrinfo = socket.socket.connect, socket.getaddrinfo
LOCAL_NAMES = {"localhost", "", "ip6-localhost"}


def local(address) -> bool:
    if isinstance(address, (str, bytes)):  # a unix socket path
        return True
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return address[0] in LOCAL_NAMES


def connect(self, address):
    if not local(address):
        attempts.append(f"connect {address[0]}")
        raise OSError(f"offline guard: connection to {address[0]} refused")
    return _connect(self, address)


def getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if name is not None and name not in LOCAL_NAMES:
        try:
            ipaddress.ip_address(name)
        except ValueError:
            attempts.append(f"lookup {name}")
            raise socket.gaierror(f"offline guard: lookup of {name} refused") from None
    return _getaddrinfo(host, *args, **kwargs)


def pytest_configure(config):
    socket.socket.connect = connect
    socket.getaddrinfo = getaddrinfo


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        outcomes[report.nodeid] = report.outcome


def pytest_sessionfinish(session):
    socket.socket.connect, socket.getaddrinfo = _connect, _getaddrinfo
    path = os.environ.get("OFFLINE_REPORT")
    if path:
        digest = hashlib.sha256(json.dumps(sorted(outcomes.items())).encode()).hexdigest()
        with open(path, "w") as out:
            json.dump(
                {
                    "tests": len(outcomes),
                    "failed": sum(v == "failed" for v in outcomes.values()),
                    "digest": digest,
                    "network_attempts": attempts,
                    "keys_set": keys_at_start,
                    "outcomes": outcomes,
                },
                out,
            )
