"""Comparison statistics for a run set (SPEC.md 16.10).

Pure computation over the artefacts written by the runner and evaluator:
per-system quality aggregates with bootstrap confidence intervals, paired
win/tie/loss comparisons on shared scenario instances, per-template
breakdowns, and efficiency aggregates kept separate from quality.

Manual imports count for quality but are excluded from efficiency
aggregates (SPEC.md 13.5). The bootstrap uses a fixed seed so reports are
reproducible from the same artefacts.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 20260716
_TIE_EPSILON = 1e-6


@dataclass(frozen=True)
class TrialRecord:
    scenario_id: str
    template_id: str
    system_id: str
    strategy: str | None
    trial_id: str
    status: str
    manual: bool
    quality: float | None
    hard_cap_applied: bool
    dimension_scores: dict[str, float]
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    searches: int | None
    cost_usd: float | None


def load_trials(run_set_directory: Path) -> list[TrialRecord]:
    """Read every trial's result.json and evaluation.json into flat records."""
    records: list[TrialRecord] = []
    scenarios_directory = run_set_directory / "scenarios"
    template_by_scenario: dict[str, str] = {}
    for scenario_path in sorted(scenarios_directory.glob("*.json")):
        scenario = json.loads(scenario_path.read_text("utf-8"))
        template_by_scenario[scenario["id"]] = scenario.get("template_id", "unknown")

    for result_path in sorted(run_set_directory.glob("*/*/*/result.json")):
        result = json.loads(result_path.read_text("utf-8"))
        scenario_id = result["scenario_instance_id"]
        if scenario_id not in template_by_scenario:
            continue
        config_path = result_path.parent / "config.json"
        strategy: str | None = None
        if config_path.is_file():
            config = json.loads(config_path.read_text("utf-8"))
            strategy = (config.get("system") or {}).get("strategy")
        evaluation_path = result_path.parent / "evaluation.json"
        quality: float | None = None
        hard_cap = False
        dimensions: dict[str, float] = {}
        if evaluation_path.is_file():
            evaluation = json.loads(evaluation_path.read_text("utf-8"))
            quality = float(evaluation["quality_score"])
            hard_cap = bool(evaluation.get("hard_constraint_cap_applied", False))
            dimensions = {
                key: float(value)
                for key, value in evaluation.get("dimension_scores", {}).items()
            }
        metrics = result.get("metrics", {})
        records.append(
            TrialRecord(
                scenario_id=scenario_id,
                template_id=template_by_scenario[scenario_id],
                system_id=result["system_id"],
                strategy=strategy,
                trial_id=result["trial_id"],
                status=result["status"],
                manual=result["status"] == "manual",
                quality=quality,
                hard_cap_applied=hard_cap,
                dimension_scores=dimensions,
                latency_ms=metrics.get("latency_ms"),
                input_tokens=metrics.get("input_tokens"),
                output_tokens=metrics.get("output_tokens"),
                searches=metrics.get("searches"),
                cost_usd=metrics.get("cost_usd"),
            )
        )
    return records


def bootstrap_confidence_interval(
    values: list[float],
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of the mean; deterministic for a given seed."""
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    generator = Random(seed)
    size = len(values)
    means = sorted(
        statistics.fmean(generator.choices(values, k=size)) for _ in range(iterations)
    )
    lower = means[int(0.025 * iterations)]
    upper = means[min(int(0.975 * iterations), iterations - 1)]
    return (round(lower, 2), round(upper, 2))


def _scored(records: list[TrialRecord]) -> list[TrialRecord]:
    return [
        record
        for record in records
        if record.quality is not None and record.status in ("completed", "manual")
    ]


def summarize_system(system_id: str, records: list[TrialRecord]) -> dict[str, Any]:
    """Quality and efficiency aggregates for one system (kept separate)."""
    scored = _scored(records)
    qualities = [record.quality for record in scored if record.quality is not None]
    total = len(records)
    completed = sum(1 for record in records if record.status in ("completed", "manual"))

    summary: dict[str, Any] = {
        "system_id": system_id,
        "strategy": next((record.strategy for record in records if record.strategy), None),
        "trials": total,
        "scored_trials": len(scored),
        "completion_rate": round(completed / total, 4) if total else 0.0,
        "failure_rate": round((total - completed) / total, 4) if total else 0.0,
        "hard_failure_rate": (
            round(sum(1 for record in scored if record.hard_cap_applied) / len(scored), 4)
            if scored
            else 0.0
        ),
        "manual": all(record.manual for record in records) if records else False,
    }
    if qualities:
        summary["quality"] = {
            "mean": round(statistics.fmean(qualities), 2),
            "median": round(statistics.median(qualities), 2),
            "stdev": round(statistics.stdev(qualities), 2) if len(qualities) > 1 else 0.0,
            "ci95": list(bootstrap_confidence_interval(qualities)),
        }
        dimension_totals: dict[str, list[float]] = {}
        for record in scored:
            for dimension, value in record.dimension_scores.items():
                dimension_totals.setdefault(dimension, []).append(value)
        summary["dimensions"] = {
            dimension: round(statistics.fmean(values), 4)
            for dimension, values in sorted(dimension_totals.items())
        }
        per_template: dict[str, list[float]] = {}
        for record in scored:
            if record.quality is not None:
                per_template.setdefault(record.template_id, []).append(record.quality)
        summary["per_template"] = {
            template: round(statistics.fmean(values), 2)
            for template, values in sorted(per_template.items())
        }

    # Efficiency: automated trials only (SPEC.md 13.5).
    automated = [record for record in scored if not record.manual]
    efficiency: dict[str, Any] = {}
    for field, label in (
        ("latency_ms", "latency_ms"),
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("searches", "searches"),
        ("cost_usd", "cost_usd"),
    ):
        values = [
            getattr(record, field)
            for record in automated
            if getattr(record, field) is not None
        ]
        if values:
            efficiency[label] = {
                "mean": round(statistics.fmean(values), 4),
                "median": round(statistics.median(values), 4),
            }
    quality_mean = summary.get("quality", {}).get("mean")
    if quality_mean is not None and automated:
        cost = efficiency.get("cost_usd", {}).get("mean")
        if cost:
            efficiency["quality_per_dollar"] = round(quality_mean / cost, 2)
        tokens = efficiency.get("output_tokens", {}).get("mean")
        if tokens:
            input_tokens = efficiency.get("input_tokens", {}).get("mean") or 0
            efficiency["quality_per_10k_tokens"] = round(
                quality_mean / ((tokens + input_tokens) / 10_000), 2
            )
        latency = efficiency.get("latency_ms", {}).get("mean")
        if latency:
            efficiency["quality_per_minute"] = round(quality_mean / (latency / 60_000), 2)
    summary["efficiency"] = efficiency
    return summary


def summarize_strategy(
    strategy: str, records: list[TrialRecord], system_ids: list[str]
) -> dict[str, Any]:
    """Pool trials across every system sharing one strategy (the comparison axis)."""
    scored = _scored(records)
    qualities = [record.quality for record in scored if record.quality is not None]
    automated = [record for record in scored if not record.manual]
    summary: dict[str, Any] = {
        "strategy": strategy,
        "systems": sorted(system_ids),
        "system_count": len(set(system_ids)),
        "scored_trials": len(scored),
    }
    if qualities:
        summary["quality"] = {
            "mean": round(statistics.fmean(qualities), 2),
            "median": round(statistics.median(qualities), 2),
            "stdev": round(statistics.stdev(qualities), 2) if len(qualities) > 1 else 0.0,
            "ci95": list(bootstrap_confidence_interval(qualities)),
        }
    efficiency: dict[str, float] = {}
    for field, digits in (("cost_usd", 4), ("latency_ms", 1)):
        values = [
            getattr(record, field)
            for record in automated
            if getattr(record, field) is not None
        ]
        if values:
            efficiency[f"{field}_mean"] = round(statistics.fmean(values), digits)
    summary["efficiency"] = efficiency
    return summary


def _scenario_means(records: list[TrialRecord]) -> dict[str, float]:
    by_scenario: dict[str, list[float]] = {}
    for record in _scored(records):
        if record.quality is not None:
            by_scenario.setdefault(record.scenario_id, []).append(record.quality)
    return {
        scenario: statistics.fmean(values) for scenario, values in by_scenario.items()
    }


def paired_comparison(
    system_a: str,
    records_a: list[TrialRecord],
    system_b: str,
    records_b: list[TrialRecord],
) -> dict[str, Any] | None:
    """Paired difference on shared scenarios (SPEC.md 16.10)."""
    means_a = _scenario_means(records_a)
    means_b = _scenario_means(records_b)
    shared = sorted(set(means_a) & set(means_b))
    if not shared:
        return None
    differences = [means_a[scenario] - means_b[scenario] for scenario in shared]
    wins = sum(1 for difference in differences if difference > _TIE_EPSILON)
    losses = sum(1 for difference in differences if difference < -_TIE_EPSILON)
    ties = len(differences) - wins - losses
    return {
        "system_a": system_a,
        "system_b": system_b,
        "shared_scenarios": len(shared),
        "mean_difference": round(statistics.fmean(differences), 2),
        "difference_ci95": list(bootstrap_confidence_interval(differences)),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate": round(wins / len(differences), 4),
    }


def pareto_frontier(summaries: list[dict[str, Any]]) -> list[str]:
    """System IDs not dominated on (mean cost, mean quality); needs cost data."""
    candidates = [
        (
            summary["system_id"],
            summary["efficiency"]["cost_usd"]["mean"],
            summary["quality"]["mean"],
        )
        for summary in summaries
        if summary.get("quality") and summary.get("efficiency", {}).get("cost_usd")
    ]
    frontier: list[str] = []
    for system_id, cost, quality in candidates:
        dominated = any(
            other_cost <= cost
            and other_quality >= quality
            and (other_cost < cost or other_quality > quality)
            for _other_id, other_cost, other_quality in candidates
        )
        if not dominated:
            frontier.append(system_id)
    return sorted(frontier)


def build_comparison(run_set_directory: Path) -> dict[str, Any]:
    """The full comparison payload used by reports and exports."""
    records = load_trials(run_set_directory)
    if not records:
        raise ValueError(f"no trials found in {run_set_directory}")
    by_system: dict[str, list[TrialRecord]] = {}
    for record in records:
        by_system.setdefault(record.system_id, []).append(record)

    system_ids = sorted(by_system)
    summaries = [summarize_system(system_id, by_system[system_id]) for system_id in system_ids]

    by_strategy: dict[str, list[TrialRecord]] = {}
    strategy_systems: dict[str, set[str]] = {}
    for record in records:
        label = record.strategy or "unspecified"
        by_strategy.setdefault(label, []).append(record)
        strategy_systems.setdefault(label, set()).add(record.system_id)
    strategies = [
        summarize_strategy(label, by_strategy[label], sorted(strategy_systems[label]))
        for label in sorted(by_strategy)
    ]

    pairwise: list[dict[str, Any]] = []
    for index, system_a in enumerate(system_ids):
        for system_b in system_ids[index + 1 :]:
            pair = paired_comparison(
                system_a, by_system[system_a], system_b, by_system[system_b]
            )
            if pair is not None:
                pairwise.append(pair)

    unevaluated = sum(1 for record in records if record.quality is None)
    warnings: list[str] = []
    if unevaluated:
        warnings.append(
            f"{unevaluated} trials have no evaluation.json; run 'owrb evaluate' first"
        )
    return {
        "run_set": run_set_directory.name,
        "systems": summaries,
        "strategies": strategies,
        "pairwise": pairwise,
        "pareto_frontier_cost_quality": pareto_frontier(summaries),
        "scenario_count": len({record.scenario_id for record in records}),
        "trial_count": len(records),
        "warnings": warnings,
    }
