"""S41 gate cases: the coverage check, the results generator and the results check (no database needed)."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from tools import coverage_check, results_check

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_results", ROOT / "scripts" / "make_results.py")
make_results = importlib.util.module_from_spec(spec)
sys.modules["make_results"] = make_results
spec.loader.exec_module(make_results)


def report(percent_by_file: dict[str, int]) -> dict:
    files = {}
    for names in coverage_check.AREAS.values():
        for name in names:
            covered = percent_by_file.get(name, 100)
            files[coverage_check.PREFIX + name] = {
                "summary": {"covered_lines": covered, "num_statements": 100, "covered_branches": 0, "num_branches": 0}
            }
    return {"files": files, "totals": {"percent_covered": 91.234}}


def run_check(tmp_path, data, minimum):
    (tmp_path / "r.json").write_text(json.dumps(data))
    argv = ["--min", str(minimum), "--report", str(tmp_path / "r.json"), "--out", str(tmp_path / "out.json")]
    return coverage_check.main(argv)


def test_coverage_check_passes_at_80_and_fails_when_lifted(tmp_path):
    """AC1: a report at 90% passes 80 and fails 99; a failing run does not write the figures."""
    data = report({name: 90 for names in coverage_check.AREAS.values() for name in names})
    assert run_check(tmp_path, data, 80) == 0
    assert json.loads((tmp_path / "out.json").read_text())["areas"]["pool accounting"]["percent"] == 90.0
    (tmp_path / "out.json").unlink()
    assert run_check(tmp_path, data, 99) == 1
    assert not (tmp_path / "out.json").exists()


def test_coverage_check_fails_for_one_weak_area_or_missing_file(tmp_path):
    assert run_check(tmp_path, report({"service/pool.py": 79}), 80) == 1
    data = report({})
    del data["files"][coverage_check.PREFIX + "service/meter.py"]
    assert run_check(tmp_path, data, 80) == 1
    assert coverage_check.main(["--report", str(tmp_path / "none.json")]) == 1


def tree(tmp_path, eval_doc=None):
    """A copy of results/ the generator can read, optionally with a results/eval/ document."""
    results = tmp_path / "results"
    results.mkdir()
    for name in ("load_latency.json", "load_concurrent.json"):
        shutil.copy(ROOT / "results" / name, results / name)
    (results / "coverage.json").write_text(
        json.dumps(
            {
                "threshold_percent": 80.0,
                "core_total_percent": 91.0,
                "suite_total_percent": 88.0,
                "areas": {"pool accounting": {"files": ["service/pool.py"], "percent": 91.0}},
            }
        )
    )
    if eval_doc:
        (results / "eval").mkdir()
        for suite in ("golden", "adversarial"):
            (results / "eval" / f"{suite}.json").write_text(json.dumps(eval_doc[suite]))
    return tmp_path


def real_docs(real=True):
    case = {"id": "G01", "prompt": "p", "tool": "random_int", "attack": "x", "pass": True, "reason": ""}
    common = {
        "real_model": real,
        "model": "vendor/real-model:free",
        "date": "2026-10-18",
        "model_requests": 7,
        "free_tier_limit": "20 requests a minute, 1000 a day",
        "passes": 1,
        "total": 1,
        "cases": [case],
    }
    golden = common | {
        "fabrication": 0,
        "audit_missing": 0,
        "reply_p95_s": 2.5,
        "reply_max_s": 3.0,
        "outbound_capture": {"matches": 0, "values_searched": 1},
    }
    return {"golden": golden, "adversarial": common}


def test_results_say_not_run_when_there_is_no_real_model_result(tmp_path):
    """The stub directory is never read; absent real results are stated, not filled in."""
    root = tree(tmp_path)
    shutil.copytree(ROOT / "results" / "eval_stub", root / "results" / "eval_stub")
    text = make_results.build(root)
    assert text.count("NOT RUN with the real model") == 2
    assert "llm_stub" not in text and "phrase-rule" not in text
    assert "Model requests used: 0" in text and "Docker Compose Postgres" in text
    assert results_check.check(text) == []


def test_results_hold_real_model_name_limit_and_requests(tmp_path):
    text = make_results.build(tree(tmp_path, real_docs()))
    assert "Model: vendor/real-model:free" in text and "Model requests used: 14" in text
    assert "20 requests a minute, 1000 a day" in text and "NOT RUN" not in text
    assert results_check.check(text) == []


def test_results_refuse_a_stub_run_in_the_real_directory(tmp_path):
    with pytest.raises(SystemExit):
        make_results.build(tree(tmp_path, real_docs(real=False)))


def test_results_check_names_a_missing_section(tmp_path):
    """AC4: remove one section, the check names it."""
    text = make_results.build(tree(tmp_path))
    for section in [
        "Coverage",
        "Throughput and error rate",
        "Latency",
        "KiB per upstream request",
        "Golden set",
        "Adversarial set",
    ]:
        kept = [part for part in text.split("\n## ") if not part.startswith(section)]
        assert results_check.check("\n## ".join(kept)) == [f"missing section: {section}"]


def test_results_check_exit_code_names_the_section(tmp_path, capsys):
    broken = tmp_path / "broken.md"
    broken.write_text(make_results.build(tree(tmp_path)).split("## Adversarial set")[0])
    assert results_check.main([str(broken)]) == 1
    assert "missing section: Adversarial set" in capsys.readouterr().out
