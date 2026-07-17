import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from owrb.stats import (
    bootstrap_confidence_interval,
    build_comparison,
    load_trials,
    pareto_frontier,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC).isoformat()

# scenario -> (system-a quality, system-b quality); a wins s1, loses s2, ties s3.
QUALITIES = {
    "s1": {"system-a": 80.0, "system-b": 60.0},
    "s2": {"system-a": 70.0, "system-b": 95.0},
    "s3": {"system-a": 70.0, "system-b": 70.0},
}
TEMPLATES = {"s1": "t-alpha", "s2": "t-beta", "s3": "t-alpha"}
COSTS = {"system-a": 0.01, "system-b": 0.002}


def write_run_set(tmp_path: Path, include_manual: bool = True) -> Path:
    run_set = tmp_path / "run-set"
    scenarios_directory = run_set / "scenarios"
    scenarios_directory.mkdir(parents=True)
    for scenario_id, template_id in TEMPLATES.items():
        (scenarios_directory / f"{scenario_id}.json").write_text(
            json.dumps({"id": scenario_id, "template_id": template_id}), encoding="utf-8"
        )

    def write_trial(
        scenario_id: str,
        system_id: str,
        quality: float | None,
        status: str = "completed",
        cost: float | None = None,
    ) -> None:
        trial_directory = run_set / scenario_id / system_id / "t01"
        trial_directory.mkdir(parents=True)
        result = {
            "schema_version": 1,
            "scenario_instance_id": scenario_id,
            "system_id": system_id,
            "trial_id": "t01",
            "status": status,
            "started_at": NOW,
            "completed_at": NOW,
            "answer": "answer",
            "citations": [],
            "metrics": {
                "latency_ms": 1200,
                "input_tokens": 900,
                "output_tokens": 100,
                "cost_usd": cost,
            },
            "trace": [],
            "warnings": [],
            "provider_metadata": {},
        }
        (trial_directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
        if quality is not None:
            evaluation = {
                "run_id": f"{scenario_id}/{system_id}/t01",
                "quality_score": quality,
                "hard_constraint_cap_applied": quality <= 49,
                "dimension_scores": {"citation_support": quality / 100},
            }
            (trial_directory / "evaluation.json").write_text(
                json.dumps(evaluation), encoding="utf-8"
            )

    for scenario_id, by_system in QUALITIES.items():
        for system_id, quality in by_system.items():
            write_trial(scenario_id, system_id, quality, cost=COSTS[system_id])
    if include_manual:
        write_trial("s1", "manual-product", 85.0, status="manual")
    return run_set


def test_load_trials_joins_results_and_evaluations(tmp_path: Path) -> None:
    records = load_trials(write_run_set(tmp_path))
    assert len(records) == 7
    first = next(r for r in records if r.scenario_id == "s1" and r.system_id == "system-a")
    assert first.quality == 80.0
    assert first.template_id == "t-alpha"
    assert first.cost_usd == 0.01
    manual = next(r for r in records if r.system_id == "manual-product")
    assert manual.manual is True


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean() -> None:
    values = [60.0, 70.0, 80.0, 90.0, 75.0, 65.0]
    first = bootstrap_confidence_interval(values)
    second = bootstrap_confidence_interval(values)
    assert first == second, "same seed must reproduce the same interval"
    low, high = first
    assert low <= 73.4 <= high
    assert bootstrap_confidence_interval([50.0]) == (50.0, 50.0)


def test_comparison_summaries_and_pairwise(tmp_path: Path) -> None:
    comparison = build_comparison(write_run_set(tmp_path))
    by_id = {system["system_id"]: system for system in comparison["systems"]}

    system_a = by_id["system-a"]
    assert system_a["quality"]["mean"] == pytest.approx(73.33, abs=0.01)
    assert system_a["completion_rate"] == 1.0
    assert system_a["per_template"] == {"t-alpha": 75.0, "t-beta": 70.0}
    assert system_a["dimensions"]["citation_support"] == pytest.approx(0.7333, abs=0.001)

    pair = comparison["pairwise"][0]
    assert {pair["system_a"], pair["system_b"]} <= {"system-a", "system-b", "manual-product"}
    ab_pair = next(
        p
        for p in comparison["pairwise"]
        if p["system_a"] == "system-a" and p["system_b"] == "system-b"
    )
    assert ab_pair["shared_scenarios"] == 3
    assert ab_pair["wins"] == 1
    assert ab_pair["losses"] == 1
    assert ab_pair["ties"] == 1
    assert ab_pair["mean_difference"] == pytest.approx(-1.67, abs=0.01)


def test_manual_systems_have_no_efficiency_aggregates(tmp_path: Path) -> None:
    comparison = build_comparison(write_run_set(tmp_path))
    by_id = {system["system_id"]: system for system in comparison["systems"]}
    assert by_id["manual-product"]["efficiency"] == {}
    assert by_id["manual-product"]["quality"]["mean"] == 85.0
    assert by_id["system-a"]["efficiency"]["latency_ms"]["mean"] == 1200
    assert by_id["system-a"]["efficiency"]["quality_per_dollar"] == pytest.approx(
        73.33 / 0.01, rel=0.001
    )


def test_pareto_frontier_drops_dominated_systems() -> None:
    summaries = [
        {
            "system_id": "cheap-good",
            "quality": {"mean": 75.0},
            "efficiency": {"cost_usd": {"mean": 0.002}},
        },
        {
            "system_id": "pricey-best",
            "quality": {"mean": 85.0},
            "efficiency": {"cost_usd": {"mean": 0.02}},
        },
        {
            "system_id": "pricey-worse",
            "quality": {"mean": 70.0},
            "efficiency": {"cost_usd": {"mean": 0.03}},
        },
        {"system_id": "no-cost-data", "quality": {"mean": 90.0}, "efficiency": {}},
    ]
    assert pareto_frontier(summaries) == ["cheap-good", "pricey-best"]


def test_unevaluated_trials_produce_a_warning(tmp_path: Path) -> None:
    run_set = write_run_set(tmp_path, include_manual=False)
    evaluation = run_set / "s1" / "system-a" / "t01" / "evaluation.json"
    evaluation.unlink()
    comparison = build_comparison(run_set)
    assert any("owrb evaluate" in warning for warning in comparison["warnings"])


def test_empty_run_set_raises(tmp_path: Path) -> None:
    (tmp_path / "scenarios").mkdir()
    with pytest.raises(ValueError, match="no trials"):
        build_comparison(tmp_path)
