"""S40 gate cases: the evaluation harness (scripts/eval_agent.py) end to end against the local stub model.

The stub is not a language model, so these cases prove the harness (sets, verdicts, fabrication scan, capture search,
request counting, results files) and not tool selection, refusals or reply times by the real model: that is the human
run in docs/DEPLOY.md, section S40 (`make eval-golden` with OPENROUTER_API_KEY set).
"""

import json
import subprocess
import sys

from qrypt_entropy.agent.reply_check import VALUE
from tests.conftest import GATE_STATS, base_url


def harness(tmp_path, *args, **env):
    return subprocess.run(
        [sys.executable, "scripts/eval_agent.py", *args],
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": base_url(), "VIRTUAL_ENV": sys.prefix, "EVAL_OUT": str(tmp_path)}
        | {"EVAL_STUB": "1"}
        | env,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_eval_stub_run_end_to_end(tmp_path):
    """AC2 to AC7 on the stub: results files, request counts, no value in any results file, summary."""
    golden = harness(tmp_path, "golden")
    assert golden.returncode == 0, golden.stdout + golden.stderr[-2000:]
    adversarial = harness(tmp_path, "adversarial")
    assert adversarial.returncode == 0, adversarial.stdout + adversarial.stderr[-2000:]
    g, a = (json.loads((tmp_path / f"{s}.json").read_text()) for s in ("golden", "adversarial"))
    assert (g["passes"], g["total"], g["fabrication"], g["audit_missing"]) == (15, 15, 0, 0)
    assert g["outbound_capture"]["matches"] == 0 and g["outbound_capture"]["values_searched"] >= 15
    assert (a["passes"], a["total"]) == (5, 5) and a["model_requests"] == 5
    assert g["real_model"] is False and "NOT a language model" in g["model"]
    assert g["reply_p95_s"] <= 10 and g["free_tier_limit"] and g["model_requests"] >= 15
    for name in ("golden.json", "golden.md", "adversarial.json", "adversarial.md"):
        assert not VALUE.search((tmp_path / name).read_text()), f"{name} holds a value-shaped string"
    summary = harness(tmp_path, "summary")
    assert summary.returncode == 0 and f"= {g['model_requests'] + a['model_requests']} " in summary.stdout
    assert "NOT VERIFIED" in summary.stdout
    GATE_STATS["eval stub run: golden passes, requests"] = (
        f"{g['passes']}/15, {g['model_requests'] + a['model_requests']}"
    )


def test_eval_request_limit_stops_the_run(tmp_path):
    """AC6: a run never goes past its request limit."""
    done = harness(tmp_path, "golden", EVAL_REQUEST_LIMIT="3")
    assert done.returncode != 0 and "RequestLimitReached" in done.stderr
    assert not (tmp_path / "golden.json").exists()
