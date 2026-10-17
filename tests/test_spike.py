import os
import subprocess
import sys


def test_ac7_spike_roles_passes_on_local_postgres():
    env = {**os.environ}
    run = subprocess.run([sys.executable, "scripts/spike_roles.py"], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    for step in ("create", "grant", "insert", "denied-select"):
        assert f"PASS {step}" in run.stdout
