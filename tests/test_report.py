import os
import subprocess
import sys

from tests.test_migrations import migrate

PANELS = [
    "codes never issued per day",
    "replies with an unbacked value",
    "tokens redeemed before expiry",
    "tool-call p95",
    "server-caused errors",
    "Qrypt requests worst 10 s",
    "KiB per Qrypt request",
    "Qrypt use against the cap",
    "time to unavailable",
]


def test_ac11_report_prints_nine_empty_panels(empty_db):
    migrate(empty_db)
    env = {**os.environ, "DATABASE_URL": empty_db}
    run = subprocess.run(
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr
    panel_lines = [ln for ln in run.stdout.splitlines() if ln.startswith("PANEL")]
    assert len(panel_lines) == 9
    for name, line in zip(PANELS, panel_lines, strict=True):
        assert name in line
        assert line.endswith("no data")
