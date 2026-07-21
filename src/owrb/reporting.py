"""Static offline reports and exports (SPEC.md section 18).

``owrb report`` writes:

- ``report/index.html`` — the suite comparison dashboard (quality by system,
  quality by discovery strategy, dimension table, per-template heatmap, paired
  win matrix, efficiency table, quality-cost frontier), fully self-contained:
  inline CSS, no scripts, no external assets, so it works offline;
- ``report/comparison.json`` plus ``summary.csv``, ``by-strategy.csv``,
  ``pairwise.csv``, and ``per-template.csv`` — every number shown in the
  dashboard is exportable;
- ``report.html`` inside each trial directory — the per-run audit view
  (scenario, answer, citations, criterion and claim tables, warnings).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import orjson
from jinja2 import Environment

_ENVIRONMENT = Environment(autoescape=True)

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 72rem;
       padding: 0 1rem; color: #1a1a1a; }
h1, h2 { border-bottom: 1px solid #ddd; padding-bottom: .3rem; }
table { border-collapse: collapse; margin: 1rem 0; width: 100%; }
th, td { border: 1px solid #ccc; padding: .35rem .6rem; text-align: left;
         vertical-align: top; font-size: .92rem; }
th { background: #f2f2f2; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar { background: #4a7db5; height: .9rem; display: inline-block;
       vertical-align: middle; min-width: 1px; }
.barbox { background: #eee; width: 10rem; display: inline-block; }
.warn { color: #8a4b00; }
.fail { color: #a11; }
.ok { color: #171; }
.muted { color: #666; }
pre.answer { white-space: pre-wrap; background: #f8f8f8; border: 1px solid #ddd;
             padding: 1rem; }
.pill { border-radius: .6rem; padding: 0 .5rem; font-size: .8rem; }
.pill.hard { background: #fdd; color: #a11; }
.heat { text-align: right; font-variant-numeric: tabular-nums; }
"""

_COMPARISON_TEMPLATE = _ENVIRONMENT.from_string(
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>OWRB comparison — {{ data.run_set }}</title>
<style>{{ style }}</style></head><body>
<h1>OWRB comparison — {{ data.run_set }}</h1>
<p class="muted">{{ data.scenario_count }} scenarios, {{ data.trial_count }} trials.
Quality and efficiency are reported separately; manual imports are excluded from
efficiency aggregates.</p>
{% for warning in data.warnings %}<p class="warn">⚠ {{ warning }}</p>{% endfor %}

<h2>Quality by system</h2>
<table><tr><th>System</th><th>Strategy</th><th class="num">Mean</th><th class="num">95% CI</th>
<th class="num">Median</th><th class="num">Stdev</th><th class="num">Completion</th>
<th class="num">Hard failures</th><th></th></tr>
{% for system in data.systems %}
<tr><td>{{ system.system_id }}{% if system.manual %} <span class="muted">(manual)</span>{% endif %}</td>
<td>{{ system.strategy or '—' }}</td>
{% if system.quality %}
<td class="num">{{ '%.1f' % system.quality.mean }}</td>
<td class="num">[{{ '%.1f' % system.quality.ci95[0] }}, {{ '%.1f' % system.quality.ci95[1] }}]</td>
<td class="num">{{ '%.1f' % system.quality.median }}</td>
<td class="num">{{ '%.1f' % system.quality.stdev }}</td>
{% else %}<td class="num muted" colspan="4">not evaluated</td>{% endif %}
<td class="num">{{ '%.0f%%' % (100 * system.completion_rate) }}</td>
<td class="num">{{ '%.0f%%' % (100 * system.hard_failure_rate) }}</td>
<td><span class="barbox"><span class="bar" style="width: {{ system.quality.mean if system.quality else 0 }}%"></span></span></td>
</tr>{% endfor %}
</table>

<h2>Quality by strategy</h2>
<p class="muted">Trials pooled across every system sharing a discovery strategy.</p>
<table><tr><th>Strategy</th><th class="num">Systems</th><th class="num">Scored</th>
<th class="num">Mean</th><th class="num">95% CI</th><th class="num">Median</th>
<th class="num">Cost USD</th><th class="num">Latency ms</th></tr>
{% for strat in data.strategies %}
<tr><td>{{ strat.strategy }}</td>
<td class="num">{{ strat.system_count }}</td>
<td class="num">{{ strat.scored_trials }}</td>
{% if strat.quality %}
<td class="num">{{ '%.1f' % strat.quality.mean }}</td>
<td class="num">[{{ '%.1f' % strat.quality.ci95[0] }}, {{ '%.1f' % strat.quality.ci95[1] }}]</td>
<td class="num">{{ '%.1f' % strat.quality.median }}</td>
{% else %}<td class="num muted" colspan="3">not evaluated</td>{% endif %}
<td class="num">{{ '%.4f' % strat.efficiency.cost_usd_mean if 'cost_usd_mean' in strat.efficiency else '—' }}</td>
<td class="num">{{ '%.0f' % strat.efficiency.latency_ms_mean if 'latency_ms_mean' in strat.efficiency else '—' }}</td>
</tr>{% endfor %}
</table>

<h2>Dimension scores (0–1)</h2>
<table><tr><th>System</th>{% for dimension in dimensions %}<th class="num">{{ dimension }}</th>{% endfor %}</tr>
{% for system in data.systems %}{% if system.dimensions %}
<tr><td>{{ system.system_id }}</td>
{% for dimension in dimensions %}<td class="heat">{{ '%.2f' % system.dimensions.get(dimension, 0) if dimension in system.dimensions else '—' }}</td>{% endfor %}
</tr>{% endif %}{% endfor %}
</table>

<h2>Quality per template</h2>
<table><tr><th>System</th>{% for template in templates %}<th class="num">{{ template }}</th>{% endfor %}</tr>
{% for system in data.systems %}{% if system.per_template %}
<tr><td>{{ system.system_id }}</td>
{% for template in templates %}<td class="heat">{{ '%.1f' % system.per_template[template] if template in system.per_template else '—' }}</td>{% endfor %}
</tr>{% endif %}{% endfor %}
</table>

<h2>Paired comparison</h2>
<table><tr><th>A</th><th>B</th><th class="num">Shared</th><th class="num">A wins</th>
<th class="num">Ties</th><th class="num">B wins</th><th class="num">Mean Δ (A−B)</th>
<th class="num">Δ 95% CI</th></tr>
{% for pair in data.pairwise %}
<tr><td>{{ pair.system_a }}</td><td>{{ pair.system_b }}</td>
<td class="num">{{ pair.shared_scenarios }}</td>
<td class="num">{{ pair.wins }}</td><td class="num">{{ pair.ties }}</td>
<td class="num">{{ pair.losses }}</td>
<td class="num">{{ '%.1f' % pair.mean_difference }}</td>
<td class="num">[{{ '%.1f' % pair.difference_ci95[0] }}, {{ '%.1f' % pair.difference_ci95[1] }}]</td>
</tr>{% endfor %}
</table>

<h2>Efficiency (automated trials only)</h2>
<table><tr><th>System</th><th class="num">Latency ms</th><th class="num">Input tok</th>
<th class="num">Output tok</th><th class="num">Searches</th><th class="num">Cost USD</th>
<th class="num">Q/$</th><th class="num">Q/10k tok</th><th class="num">Q/min</th></tr>
{% for system in data.systems %}
{% set eff = system.efficiency %}
<tr><td>{{ system.system_id }}</td>
<td class="num">{{ '%.0f' % eff.latency_ms.mean if eff.latency_ms else '—' }}</td>
<td class="num">{{ '%.0f' % eff.input_tokens.mean if eff.input_tokens else '—' }}</td>
<td class="num">{{ '%.0f' % eff.output_tokens.mean if eff.output_tokens else '—' }}</td>
<td class="num">{{ '%.1f' % eff.searches.mean if eff.searches else '—' }}</td>
<td class="num">{{ '%.4f' % eff.cost_usd.mean if eff.cost_usd else '—' }}</td>
<td class="num">{{ '%.0f' % eff.quality_per_dollar if eff.quality_per_dollar else '—' }}</td>
<td class="num">{{ '%.1f' % eff.quality_per_10k_tokens if eff.quality_per_10k_tokens else '—' }}</td>
<td class="num">{{ '%.1f' % eff.quality_per_minute if eff.quality_per_minute else '—' }}</td>
</tr>{% endfor %}
</table>
{% if data.pareto_frontier_cost_quality %}
<p>Quality-cost Pareto frontier:
{% for sid in data.pareto_frontier_cost_quality %}<strong>{{ sid }}</strong>{% if strategy_by_system.get(sid) %} <span class="muted">({{ strategy_by_system[sid] }})</span>{% endif %}{{ ', ' if not loop.last }}{% endfor %}</p>
{% endif %}

<h2>Per-run reports</h2>
<ul>{% for link in trial_links %}
<li><a href="{{ link.href }}">{{ link.label }}</a>
{% if link.quality is not none %}<span class="muted">— quality {{ '%.1f' % link.quality }}</span>{% endif %}</li>
{% endfor %}</ul>

<p class="muted">Exports: <a href="comparison.json">comparison.json</a>,
<a href="summary.csv">summary.csv</a>, <a href="by-strategy.csv">by-strategy.csv</a>,
<a href="pairwise.csv">pairwise.csv</a>, <a href="per-template.csv">per-template.csv</a></p>
</body></html>
"""
)

_TRIAL_TEMPLATE = _ENVIRONMENT.from_string(
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{ scenario.id }} — {{ result.system_id }}/{{ result.trial_id }}</title>
<style>{{ style }}</style></head><body>
<h1>{{ scenario.id }} — {{ result.system_id }}/{{ result.trial_id }}</h1>
<p>Status: <strong class="{{ 'ok' if result.status in ('completed', 'manual') else 'fail' }}">{{ result.status }}</strong>
{% if evaluation %} · Quality: <strong>{{ '%.1f' % evaluation.quality_score }}</strong>
{% if evaluation.hard_constraint_cap_applied %}<span class="pill hard">hard-constraint cap applied</span>{% endif %}
· Review: {{ evaluation.review_status }}{% endif %}</p>

{% for warning in (result.warnings or []) %}<p class="warn">⚠ run: {{ warning }}</p>{% endfor %}
{% if evaluation %}{% for warning in (evaluation.warnings or []) %}<p class="warn">⚠ evaluation: {{ warning }}</p>{% endfor %}{% endif %}

<h2>Scenario</h2>
<pre class="answer">{{ scenario.prompt }}</pre>

<h2>Answer</h2>
<pre class="answer">{{ result.answer or '(empty)' }}</pre>

<h2>Citations</h2>
{% if result.citations %}
<table><tr><th>ID</th><th>URL</th><th>Title</th></tr>
{% for citation in result.citations %}
<tr><td>{{ citation.id }}</td>
<td><a href="{{ citation.url }}" rel="nofollow noreferrer">{{ citation.url }}</a></td>
<td>{{ citation.title or '' }}</td></tr>
{% endfor %}</table>
{% else %}<p class="muted">No citations supplied.</p>{% endif %}

{% if evaluation %}
<h2>Criterion results</h2>
<table><tr><th>Criterion</th><th>Dimension</th><th class="num">Score</th>
<th>Passed</th><th class="num">Confidence</th><th>Explanation</th></tr>
{% for criterion in evaluation.criteria %}
<tr{% if criterion.hard_failure %} class="fail"{% endif %}>
<td>{{ criterion.criterion_id }}{% if criterion.hard_failure %} <span class="pill hard">hard</span>{% endif %}</td>
<td>{{ criterion.dimension }}</td>
<td class="num">{{ '%.2f' % criterion.score }}</td>
<td>{{ criterion.passed if criterion.passed is not none else '—' }}</td>
<td class="num">{{ '%.2f' % criterion.confidence if criterion.confidence is not none else '—' }}</td>
<td>{{ criterion.explanation }}</td></tr>
{% endfor %}</table>

{% if evaluation.claims %}
<h2>Claims and evidence verdicts</h2>
<table><tr><th>Claim</th><th>Type</th><th>Citations</th><th>Verdict</th><th>Explanation</th></tr>
{% for claim in evaluation.claims %}
<tr{% if claim.verdict == 'contradicted' %} class="fail"{% endif %}>
<td>{{ claim.text }}</td><td>{{ claim.claim_type }}{% if claim.time_sensitive %} ⏱{% endif %}</td>
<td>{{ claim.citation_ids | join(', ') or '—' }}</td>
<td class="{{ 'ok' if claim.verdict == 'supported' else ('fail' if claim.verdict == 'contradicted' else 'warn') }}">{{ claim.verdict }}</td>
<td>{{ claim.explanation }}</td></tr>
{% endfor %}</table>
{% if evaluation.citation_metrics %}
<p class="muted">Citation metrics:
{% for key, value in evaluation.citation_metrics.items() %}{{ key }}={{ value }}{{ '; ' if not loop.last }}{% endfor %}</p>
{% endif %}{% endif %}

<h2>Dimension scores</h2>
<table><tr><th>Dimension</th><th class="num">Score (0–1)</th></tr>
{% for dimension, value in evaluation.dimension_scores.items() %}
<tr><td>{{ dimension }}</td><td class="num">{{ '%.2f' % value }}</td></tr>
{% endfor %}</table>
{% endif %}

<h2>Run metrics</h2>
<table><tr><th>Metric</th><th class="num">Value</th></tr>
{% for key, value in result.metrics.items() if value is not none %}
<tr><td>{{ key }}</td><td class="num">{{ value }}</td></tr>
{% endfor %}</table>
</body></html>
"""
)


def _csv_bytes(rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


def _summary_csv(comparison: dict[str, Any]) -> str:
    rows: list[list[Any]] = [
        [
            "system_id",
            "strategy",
            "trials",
            "scored_trials",
            "completion_rate",
            "hard_failure_rate",
            "quality_mean",
            "quality_median",
            "quality_stdev",
            "quality_ci95_low",
            "quality_ci95_high",
            "latency_ms_mean",
            "input_tokens_mean",
            "output_tokens_mean",
            "cost_usd_mean",
        ]
    ]
    for system in comparison["systems"]:
        quality = system.get("quality") or {}
        ci = quality.get("ci95") or ["", ""]
        efficiency = system.get("efficiency") or {}
        rows.append(
            [
                system["system_id"],
                system.get("strategy") or "",
                system["trials"],
                system["scored_trials"],
                system["completion_rate"],
                system["hard_failure_rate"],
                quality.get("mean", ""),
                quality.get("median", ""),
                quality.get("stdev", ""),
                ci[0],
                ci[1],
                (efficiency.get("latency_ms") or {}).get("mean", ""),
                (efficiency.get("input_tokens") or {}).get("mean", ""),
                (efficiency.get("output_tokens") or {}).get("mean", ""),
                (efficiency.get("cost_usd") or {}).get("mean", ""),
            ]
        )
    return _csv_bytes(rows)


def _pairwise_csv(comparison: dict[str, Any]) -> str:
    rows: list[list[Any]] = [
        [
            "system_a",
            "system_b",
            "shared_scenarios",
            "wins",
            "ties",
            "losses",
            "win_rate",
            "mean_difference",
            "difference_ci95_low",
            "difference_ci95_high",
        ]
    ]
    for pair in comparison["pairwise"]:
        rows.append(
            [
                pair["system_a"],
                pair["system_b"],
                pair["shared_scenarios"],
                pair["wins"],
                pair["ties"],
                pair["losses"],
                pair["win_rate"],
                pair["mean_difference"],
                pair["difference_ci95"][0],
                pair["difference_ci95"][1],
            ]
        )
    return _csv_bytes(rows)


def _per_template_csv(comparison: dict[str, Any]) -> str:
    templates = sorted(
        {
            template
            for system in comparison["systems"]
            for template in (system.get("per_template") or {})
        }
    )
    rows: list[list[Any]] = [["system_id", *templates]]
    for system in comparison["systems"]:
        per_template = system.get("per_template") or {}
        rows.append(
            [system["system_id"], *[per_template.get(template, "") for template in templates]]
        )
    return _csv_bytes(rows)


def _by_strategy_csv(comparison: dict[str, Any]) -> str:
    rows: list[list[Any]] = [
        [
            "strategy",
            "systems",
            "system_count",
            "scored_trials",
            "quality_mean",
            "quality_median",
            "quality_stdev",
            "quality_ci95_low",
            "quality_ci95_high",
            "cost_usd_mean",
            "latency_ms_mean",
        ]
    ]
    for strategy in comparison.get("strategies", []):
        quality = strategy.get("quality") or {}
        ci = quality.get("ci95") or ["", ""]
        efficiency = strategy.get("efficiency") or {}
        rows.append(
            [
                strategy["strategy"],
                " ".join(strategy.get("systems", [])),
                strategy["system_count"],
                strategy["scored_trials"],
                quality.get("mean", ""),
                quality.get("median", ""),
                quality.get("stdev", ""),
                ci[0],
                ci[1],
                efficiency.get("cost_usd_mean", ""),
                efficiency.get("latency_ms_mean", ""),
            ]
        )
    return _csv_bytes(rows)


def render_comparison_html(
    comparison: dict[str, Any], trial_links: list[dict[str, Any]]
) -> str:
    dimensions = sorted(
        {
            dimension
            for system in comparison["systems"]
            for dimension in (system.get("dimensions") or {})
        }
    )
    templates = sorted(
        {
            template
            for system in comparison["systems"]
            for template in (system.get("per_template") or {})
        }
    )
    strategy_by_system = {
        system["system_id"]: system.get("strategy") for system in comparison["systems"]
    }
    return _COMPARISON_TEMPLATE.render(
        data=comparison,
        style=_STYLE,
        dimensions=dimensions,
        templates=templates,
        strategy_by_system=strategy_by_system,
        trial_links=trial_links,
    )


def render_trial_html(
    scenario: dict[str, Any], result: dict[str, Any], evaluation: dict[str, Any] | None
) -> str:
    return _TRIAL_TEMPLATE.render(
        scenario=scenario, result=result, evaluation=evaluation, style=_STYLE
    )


def write_reports(run_set_directory: Path) -> dict[str, Any]:
    """Write per-trial reports, the comparison dashboard, and all exports."""
    from owrb.stats import build_comparison

    comparison = build_comparison(run_set_directory)

    scenarios: dict[str, dict[str, Any]] = {}
    for scenario_path in sorted((run_set_directory / "scenarios").glob("*.json")):
        scenario = json.loads(scenario_path.read_text("utf-8"))
        scenarios[scenario["id"]] = scenario

    trial_links: list[dict[str, Any]] = []
    trial_reports = 0
    for result_path in sorted(run_set_directory.glob("*/*/*/result.json")):
        result = json.loads(result_path.read_text("utf-8"))
        scenario = scenarios.get(result["scenario_instance_id"])
        if scenario is None:
            continue
        evaluation_path = result_path.parent / "evaluation.json"
        evaluation = (
            json.loads(evaluation_path.read_text("utf-8"))
            if evaluation_path.is_file()
            else None
        )
        report_path = result_path.parent / "report.html"
        report_path.write_text(
            render_trial_html(scenario, result, evaluation), encoding="utf-8"
        )
        trial_reports += 1
        trial_links.append(
            {
                "href": "../" + report_path.relative_to(run_set_directory).as_posix(),
                "label": (
                    f"{result['scenario_instance_id']} · {result['system_id']} · "
                    f"{result['trial_id']} ({result['status']})"
                ),
                "quality": evaluation["quality_score"] if evaluation else None,
            }
        )

    report_directory = run_set_directory / "report"
    report_directory.mkdir(parents=True, exist_ok=True)
    (report_directory / "index.html").write_text(
        render_comparison_html(comparison, trial_links), encoding="utf-8"
    )
    (report_directory / "comparison.json").write_bytes(
        orjson.dumps(comparison, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    )
    (report_directory / "summary.csv").write_text(_summary_csv(comparison), encoding="utf-8")
    (report_directory / "by-strategy.csv").write_text(
        _by_strategy_csv(comparison), encoding="utf-8"
    )
    (report_directory / "pairwise.csv").write_text(_pairwise_csv(comparison), encoding="utf-8")
    (report_directory / "per-template.csv").write_text(
        _per_template_csv(comparison), encoding="utf-8"
    )
    return {
        "report_directory": str(report_directory),
        "index": str(report_directory / "index.html"),
        "trial_reports": trial_reports,
        "warnings": comparison["warnings"],
    }
