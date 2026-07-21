import csv
import json
import re
from pathlib import Path

from test_stats import write_run_set
from typer.testing import CliRunner

from owrb.cli import app
from owrb.reporting import write_reports

runner = CliRunner()


def test_write_reports_produces_offline_dashboard_and_exports(tmp_path: Path) -> None:
    run_set = write_run_set(tmp_path)
    summary = write_reports(run_set)
    assert summary["trial_reports"] == 7

    index = (run_set / "report" / "index.html").read_text("utf-8")
    assert "system-a" in index and "system-b" in index
    assert "Paired comparison" in index
    assert "manual-product" in index
    assert "Quality by strategy" in index and "native_search" in index

    # Offline requirement: no scripts and no external assets.
    assert "<script" not in index
    assert not re.search(r'(?:src|href)="https?://', index)

    comparison = json.loads((run_set / "report" / "comparison.json").read_text("utf-8"))
    assert comparison["trial_count"] == 7

    with (run_set / "report" / "summary.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert {row["system_id"] for row in rows} == {"system-a", "system-b", "manual-product"}
    system_a = next(row for row in rows if row["system_id"] == "system-a")
    assert float(system_a["quality_mean"]) > 70
    assert system_a["strategy"] == "native_search"

    with (run_set / "report" / "by-strategy.csv").open() as handle:
        strategy_rows = list(csv.DictReader(handle))
    assert {row["strategy"] for row in strategy_rows} >= {
        "native_search",
        "private_index",
        "unspecified",
    }

    with (run_set / "report" / "pairwise.csv").open() as handle:
        pairwise_rows = list(csv.DictReader(handle))
    assert pairwise_rows, "pairwise export must not be empty"

    with (run_set / "report" / "per-template.csv").open() as handle:
        template_rows = list(csv.DictReader(handle))
    assert "t-alpha" in template_rows[0]


def test_trial_reports_escape_untrusted_answer_content(tmp_path: Path) -> None:
    run_set = write_run_set(tmp_path, include_manual=False)
    result_path = run_set / "s1" / "system-a" / "t01" / "result.json"
    result = json.loads(result_path.read_text("utf-8"))
    result["answer"] = 'Try this <script>alert("xss")</script> walk.'
    result_path.write_text(json.dumps(result), encoding="utf-8")

    write_reports(run_set)
    report = (run_set / "s1" / "system-a" / "t01" / "report.html").read_text("utf-8")
    assert "<script>alert" not in report
    assert "&lt;script&gt;" in report


def test_trial_report_shows_criteria_and_claims(tmp_path: Path) -> None:
    run_set = write_run_set(tmp_path, include_manual=False)
    evaluation_path = run_set / "s2" / "system-b" / "t01" / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_text("utf-8"))
    evaluation["criteria"] = [
        {
            "criterion_id": "radius",
            "dimension": "constraint_satisfaction",
            "score": 0.0,
            "passed": False,
            "explanation": "outside the stated radius",
            "confidence": 0.9,
            "hard_failure": True,
        }
    ]
    evaluation["claims"] = [
        {
            "id": "c1",
            "text": "The lookout closes at 5pm",
            "claim_type": "operational",
            "time_sensitive": True,
            "citation_ids": ["c1"],
            "verdict": "contradicted",
            "explanation": "the cited page says open 24 hours",
        }
    ]
    evaluation["citation_metrics"] = {"citation_precision": 0.0}
    evaluation["warnings"] = []
    evaluation["review_status"] = "required"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

    write_reports(run_set)
    report = (run_set / "s2" / "system-b" / "t01" / "report.html").read_text("utf-8")
    assert "hard-constraint cap applied" not in report  # cap flag comes from the field
    assert "outside the stated radius" in report
    assert "contradicted" in report
    assert "open 24 hours" in report


def test_compare_and_report_cli(tmp_path: Path) -> None:
    run_set = write_run_set(tmp_path)
    compare_result = runner.invoke(app, ["compare", "--run-set", str(run_set), "--json"])
    assert compare_result.exit_code == 0, compare_result.output
    payload = json.loads(compare_result.output)
    assert payload["pairwise"]

    report_result = runner.invoke(app, ["report", "--run-set", str(run_set)])
    assert report_result.exit_code == 0, report_result.output
    assert (run_set / "report" / "index.html").is_file()

    missing = runner.invoke(app, ["compare", "--run-set", str(tmp_path / "nope")])
    assert missing.exit_code == 1
