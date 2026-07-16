"""Mixed evaluation: deterministic checks, claim/evidence judging, rubric scoring.

Implements SPEC.md section 16. The pipeline per run:

1. deterministic framework checks (no judge, always run);
2. claim decomposition and citation-support verdicts against the shared
   evidence bundle (judge required);
3. rubric scoring of template criteria (judge required);
4. dimension weighting, hard-constraint capping, and citation metrics.

Judge prompts never contain the candidate system's identity (SPEC.md 16.6).
Quality and efficiency stay separate: nothing here reads run metrics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, Field

from owrb.evidence import EvidenceStore, build_evidence_bundle
from owrb.judge import JudgeClient, JudgeConfig, JudgeError, create_judge, extract_json
from owrb.models import (
    ClaimResult,
    CriterionResult,
    EvaluationResult,
    EvidenceRecord,
    RunResult,
    ScenarioInstance,
)
from owrb.validators import run_deterministic_checks

EVALUATOR_VERSION = "0.1.0"

# SPEC.md 16.1 default dimension weights.
DEFAULT_QUALITY_WEIGHTS: dict[str, float] = {
    "constraint_satisfaction": 25,
    "citation_support": 25,
    "factual_correctness": 20,
    "coverage": 15,
    "source_quality_freshness": 10,
    "clarity": 5,
}

_MAX_ANSWER_CHARS = 8000
_MAX_EVIDENCE_CHARS = 1200
_MAX_CLAIMS = 30

_SYSTEM_PROMPT = (
    "You are a blind evaluator for an open-world research benchmark. You do not "
    "know which system produced the answer, and you must not guess or reward "
    "style over substance. Base every judgement only on the material provided. "
    "Respond with strict JSON only: no prose, no markdown fences."
)


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_weights: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_QUALITY_WEIGHTS)
    )
    hard_constraint_score_cap: float = Field(default=49, ge=0, le=100)
    evidence_cache: bool = True
    judge: JudgeConfig = Field(default_factory=JudgeConfig)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def _citation_lines(result: RunResult) -> str:
    return "\n".join(
        f"- {citation.id}: {citation.url}" for citation in result.citations
    ) or "(none)"


def build_decompose_prompt(scenario: ScenarioInstance, result: RunResult) -> str:
    return (
        "Task prompt given to the system:\n"
        f"{scenario.prompt}\n\n"
        "Answer to analyse:\n"
        f"{_truncate(result.answer, _MAX_ANSWER_CHARS)}\n\n"
        "Citations supplied with the answer:\n"
        f"{_citation_lines(result)}\n\n"
        "Extract the material factual claims and recommendations from the answer. "
        "Material claims are ones a traveller would rely on: operating status, "
        "opening hours, prices, bookings, distances, accessibility, safety, and "
        "each concrete recommendation. Ignore filler and hedging. "
        f"Return at most {_MAX_CLAIMS} claims as a JSON array of objects with keys: "
        "id (c1, c2, ...), text (the claim, self-contained), "
        "type (factual | operational | recommendation | other), "
        "time_sensitive (boolean), "
        "citation_ids (array of citation ids placed near this claim, may be empty)."
    )


def build_support_prompt(
    claims: list[dict[str, Any]],
    citation_urls: dict[str, str],
    evidence: dict[str, tuple[EvidenceRecord, str]],
) -> str:
    evidence_sections: list[str] = []
    for citation_id, url in sorted(citation_urls.items()):
        entry = evidence.get(url)
        if entry is None:
            evidence_sections.append(f"[{citation_id}] {url}\nstatus: not retrieved")
            continue
        record, text = entry
        if record.status == "reachable" and text:
            evidence_sections.append(
                f"[{citation_id}] {url}\nstatus: reachable"
                + (f"\ntitle: {record.title}" if record.title else "")
                + f"\nextract:\n{_truncate(text, _MAX_EVIDENCE_CHARS)}"
            )
        else:
            evidence_sections.append(
                f"[{citation_id}] {url}\nstatus: {record.status}"
                + (f" ({record.warning})" if record.warning else "")
            )
    claim_lines = json.dumps(claims, indent=1)
    return (
        "Claims extracted from an answer, with the citation ids placed near them:\n"
        f"{claim_lines}\n\n"
        "Retrieved evidence for each citation id:\n\n"
        + "\n\n".join(evidence_sections)
        + "\n\n"
        "For each claim, judge whether its cited evidence supports it. Use verdicts: "
        "supported (evidence clearly backs the claim), "
        "contradicted (evidence clearly conflicts with the claim), "
        "not_addressed (evidence is reachable but does not cover the claim), "
        "unverifiable (cited evidence could not be retrieved or extracted). "
        "A claim with no citations must get verdict no_citation. "
        "Also judge source suitability: an operator or authority page suits "
        "operational claims; user-generated content alone does not suit safety, "
        "legal, access, or price claims. "
        "Return a JSON array of objects with keys: id, verdict, explanation "
        "(one sentence naming the evidence), source_suitable (boolean or null)."
    )


def build_rubric_prompt(
    scenario: ScenarioInstance,
    result: RunResult,
    claims: list[ClaimResult],
) -> str:
    criteria_payload = [
        {
            "id": criterion.id,
            "title": criterion.title,
            "description": criterion.description,
        }
        for criterion in scenario.criteria
    ]
    verdict_summary = [
        {"text": _truncate(claim.text, 200), "verdict": claim.verdict}
        for claim in claims
    ]
    return (
        "Task prompt given to the system:\n"
        f"{scenario.prompt}\n\n"
        "Answer to score:\n"
        f"{_truncate(result.answer, _MAX_ANSWER_CHARS)}\n\n"
        "Citations supplied with the answer:\n"
        f"{_citation_lines(result)}\n\n"
        "Evidence verdicts already established for the answer's claims:\n"
        f"{json.dumps(verdict_summary, indent=1)}\n\n"
        "Score the answer against each criterion below on a 0.0-1.0 scale, "
        "where 1.0 fully satisfies the criterion:\n"
        f"{json.dumps(criteria_payload, indent=1)}\n\n"
        "Return a JSON array of objects with keys: id (criterion id), "
        "score (0.0-1.0), passed (boolean), explanation (one or two sentences "
        "grounded in the answer and evidence), confidence (0.0-1.0)."
    )


async def _decompose_claims(
    judge: JudgeClient, scenario: ScenarioInstance, result: RunResult
) -> list[dict[str, Any]]:
    response = await judge.complete(_SYSTEM_PROMPT, build_decompose_prompt(scenario, result))
    raw = extract_json(response)
    if not isinstance(raw, list):
        raise JudgeError("claim decomposition did not return a JSON array")
    claims: list[dict[str, Any]] = []
    valid_citation_ids = {citation.id for citation in result.citations}
    for position, item in enumerate(raw[:_MAX_CLAIMS], start=1):
        if not isinstance(item, dict) or not item.get("text"):
            continue
        claims.append(
            {
                "id": str(item.get("id") or f"c{position}"),
                "text": str(item["text"]),
                "type": item.get("type", "factual"),
                "time_sensitive": bool(item.get("time_sensitive", False)),
                "citation_ids": [
                    str(citation_id)
                    for citation_id in item.get("citation_ids", [])
                    if str(citation_id) in valid_citation_ids
                ],
            }
        )
    return claims


async def _judge_claim_support(
    judge: JudgeClient,
    claims: list[dict[str, Any]],
    result: RunResult,
    evidence: dict[str, tuple[EvidenceRecord, str]],
) -> list[ClaimResult]:
    citation_urls = {citation.id: citation.url for citation in result.citations}
    cited_claims = [claim for claim in claims if claim["citation_ids"]]
    verdicts: dict[str, dict[str, Any]] = {}
    if cited_claims:
        response = await judge.complete(
            _SYSTEM_PROMPT, build_support_prompt(cited_claims, citation_urls, evidence)
        )
        raw = extract_json(response)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    verdicts[str(item["id"])] = item

    allowed = {"supported", "contradicted", "not_addressed", "unverifiable", "no_citation"}
    claim_results: list[ClaimResult] = []
    for claim in claims:
        claim_type = claim["type"] if claim["type"] in (
            "factual",
            "operational",
            "recommendation",
            "other",
        ) else "other"
        if not claim["citation_ids"]:
            verdict, explanation = "no_citation", "no citation was placed near this claim"
        else:
            judged = verdicts.get(claim["id"], {})
            verdict = str(judged.get("verdict", "unverifiable"))
            if verdict not in allowed:
                verdict = "unverifiable"
            explanation = str(judged.get("explanation", ""))
        claim_results.append(
            ClaimResult(
                id=claim["id"],
                text=claim["text"],
                claim_type=claim_type,  # type: ignore[arg-type]
                time_sensitive=claim["time_sensitive"],
                citation_ids=claim["citation_ids"],
                verdict=verdict,  # type: ignore[arg-type]
                explanation=explanation,
            )
        )
    return claim_results


async def _judge_rubric(
    judge: JudgeClient,
    scenario: ScenarioInstance,
    result: RunResult,
    claims: list[ClaimResult],
) -> list[CriterionResult]:
    if not scenario.criteria:
        return []
    response = await judge.complete(
        _SYSTEM_PROMPT, build_rubric_prompt(scenario, result, claims)
    )
    raw = extract_json(response)
    scored: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                scored[str(item["id"])] = item

    findings: list[CriterionResult] = []
    for criterion in scenario.criteria:
        item = scored.get(criterion.id)
        if item is None:
            findings.append(
                CriterionResult(
                    criterion_id=criterion.id,
                    dimension=criterion.dimension,
                    score=0.5,
                    passed=None,
                    explanation="judge did not return a score for this criterion",
                    confidence=0.0,
                )
            )
            continue
        score = min(1.0, max(0.0, float(item.get("score", 0.5))))
        confidence_raw = item.get("confidence")
        confidence = (
            min(1.0, max(0.0, float(confidence_raw))) if confidence_raw is not None else None
        )
        passed = item.get("passed")
        findings.append(
            CriterionResult(
                criterion_id=criterion.id,
                dimension=criterion.dimension,
                score=score,
                passed=bool(passed) if passed is not None else None,
                explanation=str(item.get("explanation", "")),
                confidence=confidence,
                hard_failure=criterion.hard and passed is False,
            )
        )
    return findings


def compute_citation_metrics(claims: list[ClaimResult]) -> dict[str, float]:
    if not claims:
        return {}
    total = len(claims)
    with_citations = sum(1 for claim in claims if claim.citation_ids)
    judged = [
        claim
        for claim in claims
        if claim.verdict in ("supported", "contradicted", "not_addressed")
    ]
    supported = sum(1 for claim in judged if claim.verdict == "supported")
    unsupported = sum(
        1
        for claim in claims
        if claim.verdict in ("contradicted", "not_addressed", "no_citation")
    )
    metrics = {
        "claims": float(total),
        "citation_coverage": round(with_citations / total, 4),
        "unsupported_claim_rate": round(unsupported / total, 4),
    }
    if judged:
        metrics["citation_precision"] = round(supported / len(judged), 4)
    return metrics


def claims_to_criteria(
    claims: list[ClaimResult], evidence: dict[str, tuple[EvidenceRecord, str]]
) -> list[CriterionResult]:
    """Synthesise framework criteria from claim verdicts and source status."""
    findings: list[CriterionResult] = []
    metrics = compute_citation_metrics(claims)
    if "citation_precision" in metrics:
        precision = metrics["citation_precision"]
        findings.append(
            CriterionResult(
                criterion_id="framework.claim-support",
                dimension="citation_support",
                score=precision,
                passed=precision >= 0.8,
                explanation=(
                    f"{precision:.0%} of evidence-judged claims are supported by "
                    "their citations"
                ),
                confidence=0.8,
            )
        )
        contradicted = sum(1 for claim in claims if claim.verdict == "contradicted")
        judged = sum(
            1
            for claim in claims
            if claim.verdict in ("supported", "contradicted", "not_addressed")
        )
        consistency = 1.0 - contradicted / judged if judged else 1.0
        findings.append(
            CriterionResult(
                criterion_id="framework.factual-consistency",
                dimension="factual_correctness",
                score=consistency,
                passed=contradicted == 0,
                explanation=f"{contradicted} claims are contradicted by cited evidence",
                confidence=0.8,
            )
        )
    if evidence:
        reachable = sum(
            1 for record, _text in evidence.values() if record.status == "reachable"
        )
        share = reachable / len(evidence)
        findings.append(
            CriterionResult(
                criterion_id="framework.sources-reachable",
                dimension="source_quality_freshness",
                score=share,
                passed=share > 0,
                explanation=(
                    f"{reachable}/{len(evidence)} cited sources were independently "
                    "retrievable"
                ),
                confidence=1.0,
            )
        )
    return findings


def compute_scores(
    criteria_results: list[CriterionResult],
    scenario: ScenarioInstance,
    config: EvaluationConfig,
) -> tuple[dict[str, float], float, bool]:
    """Weighted dimension scores, overall quality, and the hard-constraint cap."""
    template_weights = {criterion.id: criterion.weight for criterion in scenario.criteria}
    by_dimension: dict[str, list[tuple[float, float]]] = {}
    for finding in criteria_results:
        weight = template_weights.get(finding.criterion_id, 1.0)
        by_dimension.setdefault(finding.dimension, []).append((finding.score, weight))

    dimension_scores: dict[str, float] = {}
    for dimension, entries in by_dimension.items():
        total_weight = sum(weight for _score, weight in entries)
        dimension_scores[dimension] = round(
            sum(score * weight for score, weight in entries) / total_weight, 4
        )

    weights = {**DEFAULT_QUALITY_WEIGHTS, **config.quality_weights}
    present = {
        dimension: weight
        for dimension, weight in weights.items()
        if dimension in dimension_scores
    }
    for dimension in dimension_scores:
        # Dimensions unknown to the weighting table still count, at weight 5.
        present.setdefault(dimension, 5.0)
    if not present:
        return {}, 0.0, False
    quality = 100 * sum(
        dimension_scores[dimension] * weight for dimension, weight in present.items()
    ) / sum(present.values())

    hard_failure = any(finding.hard_failure for finding in criteria_results)
    if hard_failure:
        quality = min(quality, config.hard_constraint_score_cap)
    return dimension_scores, round(quality, 2), hard_failure


async def evaluate_run(
    scenario: ScenarioInstance,
    result: RunResult,
    evidence: dict[str, tuple[EvidenceRecord, str]],
    judge: JudgeClient | None,
    config: EvaluationConfig,
) -> EvaluationResult:
    run_id = f"{scenario.id}/{result.system_id}/{result.trial_id}"
    warnings: list[str] = []
    judge_configuration = dict(judge.identity) if judge is not None else {"adapter": "none"}

    if result.status not in ("completed", "manual"):
        return EvaluationResult(
            run_id=run_id,
            evaluated_at=datetime.now(tz=UTC),
            evaluator_version=EVALUATOR_VERSION,
            judge_configuration=judge_configuration,
            criteria=[],
            dimension_scores={},
            quality_score=0,
            warnings=[f"run status is {result.status!r}; quality scored 0"],
            review_status="not_required",
        )

    criteria_results = run_deterministic_checks(scenario, result)

    claims: list[ClaimResult] = []
    if judge is not None:
        try:
            raw_claims = await _decompose_claims(judge, scenario, result)
            claims = await _judge_claim_support(judge, raw_claims, result, evidence)
            criteria_results.extend(claims_to_criteria(claims, evidence))
            criteria_results.extend(await _judge_rubric(judge, scenario, result, claims))
        except JudgeError as error:
            warnings.append(f"judge evaluation incomplete: {error}")
    else:
        if scenario.criteria:
            warnings.append(
                "no judge configured; template criteria and claim support were not scored"
            )
        criteria_results.extend(claims_to_criteria(claims, evidence))

    dimension_scores, quality, cap_applied = compute_scores(
        criteria_results, scenario, config
    )
    low_confidence = any(
        finding.confidence is not None and finding.confidence < 0.5
        for finding in criteria_results
    )
    review_required = judge is None or cap_applied or low_confidence or bool(warnings)

    return EvaluationResult(
        run_id=run_id,
        evaluated_at=datetime.now(tz=UTC),
        evaluator_version=EVALUATOR_VERSION,
        judge_configuration=judge_configuration,
        criteria=criteria_results,
        claims=claims,
        citation_metrics=compute_citation_metrics(claims),
        dimension_scores=dimension_scores,
        quality_score=quality,
        hard_constraint_cap_applied=cap_applied,
        review_status="required" if review_required else "not_required",
        warnings=warnings,
    )


def _load_run_results(run_set_directory: Path, scenario_id: str) -> list[tuple[Path, RunResult]]:
    results: list[tuple[Path, RunResult]] = []
    scenario_directory = run_set_directory / scenario_id
    if not scenario_directory.is_dir():
        return results
    for result_path in sorted(scenario_directory.glob("*/*/result.json")):
        results.append(
            (result_path.parent, RunResult.model_validate_json(result_path.read_text("utf-8")))
        )
    return results


async def evaluate_run_set(
    run_set_directory: Path,
    config: EvaluationConfig | None = None,
    judge: JudgeClient | None = None,
    store: EvidenceStore | None = None,
    force_evidence_refresh: bool = False,
) -> dict[str, Any]:
    """Evaluate every trial in a run set; returns a summary dict."""
    scenario_directory = run_set_directory / "scenarios"
    if not scenario_directory.is_dir():
        raise ValueError(f"not a run set (missing scenarios/): {run_set_directory}")

    if config is None:
        config = EvaluationConfig()
        manifest_path = run_set_directory / "run-set.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text("utf-8"))
            raw_evaluation = dict(manifest.get("suite", {}).get("evaluation") or {})
            known_judge_adapters = {"anthropic", "openai", "none"}
            raw_judge = dict(raw_evaluation.get("judge") or {})
            if raw_judge.get("adapter") not in known_judge_adapters:
                raw_judge = {}
            raw_evaluation["judge"] = raw_judge
            config = EvaluationConfig.model_validate(raw_evaluation)
    if judge is None:
        judge = create_judge(config.judge)
    if store is None:
        store = EvidenceStore(run_set_directory / "evidence")

    summary: dict[str, Any] = {
        "evaluated": 0,
        "scenarios": 0,
        "judge": dict(judge.identity) if judge else {"adapter": "none"},
        "warnings": [],
    }
    if judge is None:
        summary["warnings"].append(
            "no judge configured; only deterministic and evidence checks were scored"
        )

    for scenario_path in sorted(scenario_directory.glob("*.json")):
        scenario = ScenarioInstance.model_validate_json(scenario_path.read_text("utf-8"))
        trials = _load_run_results(run_set_directory, scenario.id)
        if not trials:
            continue
        summary["scenarios"] += 1

        cited_urls = sorted(
            {
                citation.url
                for _directory, result in trials
                for citation in result.citations
            }
        )
        evidence: dict[str, tuple[EvidenceRecord, str]] = {}
        if cited_urls:
            await build_evidence_bundle(
                store,
                scenario.id,
                cited_urls,
                run_set_directory / "evidence" / "bundles",
                force=force_evidence_refresh,
            )
            for url in cited_urls:
                evidence[url] = await store.get(url)

        for trial_directory, result in trials:
            trial_evidence = {
                citation.url: evidence[citation.url]
                for citation in result.citations
                if citation.url in evidence
            }
            evaluation = await evaluate_run(scenario, result, trial_evidence, judge, config)
            (trial_directory / "evaluation.json").write_bytes(
                orjson.dumps(
                    evaluation.model_dump(mode="json"),
                    option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
                )
                + b"\n"
            )
            summary["evaluated"] += 1
    return summary
