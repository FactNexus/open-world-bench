import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import make_scenario

from owrb.evaluation import (
    EvaluationConfig,
    build_decompose_prompt,
    build_rubric_prompt,
    build_support_prompt,
    compute_scores,
    evaluate_run,
    evaluate_run_set,
)
from owrb.evidence import EvidenceStore
from owrb.judge import JudgeConfig, create_judge, extract_json
from owrb.models import (
    Citation,
    CriterionResult,
    CriterionSpec,
    EvidenceRecord,
    RunResult,
    ScenarioInstance,
)

ANSWER = (
    "## Recommendations\n"
    "1. Grand Clifftop Walk — open daily and wheelchair accessible [c1].\n"
    "2. Echo Point Lookout — free entry, closes at 5pm [c2].\n"
)


class FakeJudge:
    """Scripted judge: routes each pipeline call by prompt content."""

    identity = {"adapter": "fake", "model": "scripted"}

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(system_prompt + "\n" + user_prompt)
        if "Extract the material factual claims" in user_prompt:
            return json.dumps(
                [
                    {
                        "id": "c1",
                        "text": "Grand Clifftop Walk is open daily and wheelchair accessible",
                        "type": "operational",
                        "time_sensitive": True,
                        "citation_ids": ["c1"],
                    },
                    {
                        "id": "c2",
                        "text": "Echo Point Lookout closes at 5pm",
                        "type": "operational",
                        "time_sensitive": True,
                        "citation_ids": ["c2"],
                    },
                    {
                        "id": "c3",
                        "text": "Both walks suit a wheelchair user",
                        "type": "recommendation",
                        "time_sensitive": False,
                        "citation_ids": [],
                    },
                ]
            )
        if "judge whether its cited evidence supports it" in user_prompt:
            return json.dumps(
                [
                    {
                        "id": "c1",
                        "verdict": "supported",
                        "explanation": "operator page confirms daily opening and access",
                        "source_suitable": True,
                    },
                    {
                        "id": "c2",
                        "verdict": "contradicted",
                        "explanation": "the cited page says the lookout is open 24 hours",
                        "source_suitable": True,
                    },
                ]
            )
        if "Score the answer against each criterion" in user_prompt:
            return json.dumps(
                [
                    {
                        "id": "radius",
                        "score": 0.2,
                        "passed": False,
                        "explanation": "one recommendation is outside the stated radius",
                        "confidence": 0.9,
                    },
                    {
                        "id": "traveller-fit",
                        "score": 0.9,
                        "passed": True,
                        "explanation": "both options address the accessibility requirement",
                        "confidence": 0.8,
                    },
                ]
            )
        raise AssertionError(f"unexpected judge prompt: {user_prompt[:80]}")


def scenario_with_criteria() -> ScenarioInstance:
    scenario = make_scenario()
    return scenario.model_copy(
        update={
            "criteria": [
                CriterionSpec(
                    id="radius",
                    dimension="constraint_satisfaction",
                    title="Within radius",
                    description="Recommendations respect the maximum travel radius.",
                    hard=True,
                ),
                CriterionSpec(
                    id="traveller-fit",
                    dimension="coverage",
                    title="Fits traveller",
                    description="Recommendations fit the traveller profile.",
                ),
            ]
        }
    )


def completed_result(**overrides: object) -> RunResult:
    payload: dict[str, object] = {
        "scenario_instance_id": "minimal.pick-colour.000001",
        "system_id": "candidate-a",
        "trial_id": "t01",
        "status": "completed",
        "started_at": datetime(2026, 7, 17, tzinfo=UTC),
        "completed_at": datetime(2026, 7, 17, tzinfo=UTC),
        "answer": ANSWER,
        "citations": [
            Citation(id="c1", url="https://parks.example/clifftop"),
            Citation(id="c2", url="https://lookout.example/echo-point"),
        ],
    }
    payload.update(overrides)
    return RunResult.model_validate(payload)


def reachable_evidence() -> dict[str, tuple[EvidenceRecord, str]]:
    def record(url: str) -> EvidenceRecord:
        return EvidenceRecord(
            url=url,
            status="reachable",
            http_status=200,
            retrieved_at=datetime(2026, 7, 17, tzinfo=UTC),
            text_length=100,
        )

    return {
        "https://parks.example/clifftop": (
            record("https://parks.example/clifftop"),
            "The Grand Clifftop Walk is open daily and is wheelchair accessible.",
        ),
        "https://lookout.example/echo-point": (
            record("https://lookout.example/echo-point"),
            "Echo Point Lookout is open 24 hours a day.",
        ),
    }


def test_full_evaluation_surfaces_unsupported_claims_and_caps_hard_failures() -> None:
    judge = FakeJudge()
    evaluation = asyncio.run(
        evaluate_run(
            scenario_with_criteria(),
            completed_result(),
            reachable_evidence(),
            judge,
            EvaluationConfig(),
        )
    )
    contradicted = [claim for claim in evaluation.claims if claim.verdict == "contradicted"]
    assert len(contradicted) == 1
    assert "open 24 hours" in contradicted[0].explanation
    no_citation = [claim for claim in evaluation.claims if claim.verdict == "no_citation"]
    assert len(no_citation) == 1

    assert evaluation.citation_metrics["citation_precision"] == 0.5
    assert evaluation.citation_metrics["citation_coverage"] == pytest.approx(2 / 3, abs=1e-4)

    assert evaluation.hard_constraint_cap_applied is True
    assert evaluation.quality_score <= 49
    assert evaluation.review_status == "required"

    radius = next(c for c in evaluation.criteria if c.criterion_id == "radius")
    assert radius.hard_failure is True
    dimensions = evaluation.dimension_scores
    assert set(dimensions) >= {
        "constraint_satisfaction",
        "citation_support",
        "factual_correctness",
        "coverage",
        "source_quality_freshness",
    }


def test_judge_prompts_are_blind_to_candidate_identity() -> None:
    scenario = scenario_with_criteria()
    result = completed_result(system_id="secret-system-name")
    judge = FakeJudge()
    asyncio.run(
        evaluate_run(scenario, result, reachable_evidence(), judge, EvaluationConfig())
    )
    assert judge.prompts, "judge must have been called"
    for prompt in judge.prompts:
        assert "secret-system-name" not in prompt

    for builder_output in (
        build_decompose_prompt(scenario, result),
        build_support_prompt([], {}, {}),
        build_rubric_prompt(scenario, result, []),
    ):
        assert "secret-system-name" not in builder_output


def test_failed_runs_score_zero_without_judge_calls() -> None:
    judge = FakeJudge()
    evaluation = asyncio.run(
        evaluate_run(
            scenario_with_criteria(),
            completed_result(status="failed", answer=""),
            {},
            judge,
            EvaluationConfig(),
        )
    )
    assert evaluation.quality_score == 0
    assert judge.prompts == []
    assert "run status is 'failed'" in evaluation.warnings[0]


def test_no_judge_mode_scores_deterministically_and_requires_review() -> None:
    evaluation = asyncio.run(
        evaluate_run(
            scenario_with_criteria(),
            completed_result(),
            reachable_evidence(),
            None,
            EvaluationConfig(),
        )
    )
    assert evaluation.review_status == "required"
    assert any("no judge configured" in warning for warning in evaluation.warnings)
    assert evaluation.claims == []
    assert evaluation.quality_score > 0, "deterministic checks still produce a score"


def test_compute_scores_weighting_and_cap() -> None:
    scenario = scenario_with_criteria()
    config = EvaluationConfig()
    findings = [
        CriterionResult(
            criterion_id="a",
            dimension="citation_support",
            score=1.0,
            explanation="",
        ),
        CriterionResult(
            criterion_id="b",
            dimension="clarity",
            score=0.0,
            explanation="",
        ),
    ]
    dimensions, quality, capped = compute_scores(findings, scenario, config)
    assert dimensions == {"citation_support": 1.0, "clarity": 0.0}
    # citation_support weight 25, clarity 5 -> 25/30.
    assert quality == pytest.approx(100 * 25 / 30, abs=0.01)
    assert capped is False

    findings.append(
        CriterionResult(
            criterion_id="radius",
            dimension="constraint_satisfaction",
            score=0.0,
            passed=False,
            explanation="",
            hard_failure=True,
        )
    )
    _dimensions, quality, capped = compute_scores(findings, scenario, config)
    assert capped is True
    assert quality <= config.hard_constraint_score_cap


def test_extract_json_tolerates_fences_and_prose() -> None:
    assert extract_json('[{"a": 1}]') == [{"a": 1}]
    assert extract_json('Here you go:\n```json\n[{"a": 1}]\n```\nDone.') == [{"a": 1}]
    assert extract_json('The result is {"a": 1} as requested.') == {"a": 1}
    from owrb.judge import JudgeError

    with pytest.raises(JudgeError):
        extract_json("no json here")


def test_create_judge_handles_unconfigured_placeholders() -> None:
    assert create_judge(JudgeConfig()) is None
    assert create_judge(JudgeConfig(adapter="configurable", model="replace-me")) is None
    assert create_judge(JudgeConfig(adapter="anthropic", model="claude-fable-5")) is not None
    assert create_judge(JudgeConfig(adapter="openai", model="gpt-test")) is not None


def test_evaluate_run_set_end_to_end(tmp_path: Path) -> None:
    """Build a tiny run set on disk, evaluate it, and check the artefacts."""
    scenario = scenario_with_criteria()
    run_set = tmp_path / "run-set"
    scenarios_directory = run_set / "scenarios"
    scenarios_directory.mkdir(parents=True)
    (scenarios_directory / f"{scenario.id}.json").write_text(
        scenario.model_dump_json(), encoding="utf-8"
    )
    trial_directory = run_set / scenario.id / "candidate-a" / "t01"
    trial_directory.mkdir(parents=True)
    (trial_directory / "result.json").write_text(
        completed_result().model_dump_json(), encoding="utf-8"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "clifftop" in request.url.path:
            return httpx.Response(
                200,
                html="<html><title>Clifftop</title><body><p>Open daily, wheelchair "
                "accessible.</p></body></html>",
            )
        return httpx.Response(404)

    store = EvidenceStore(
        run_set / "evidence",
        transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"],
        min_host_interval=0,
    )
    summary = asyncio.run(
        evaluate_run_set(run_set, judge=FakeJudge(), store=store)
    )
    assert summary["evaluated"] == 1
    assert summary["scenarios"] == 1

    evaluation = json.loads((trial_directory / "evaluation.json").read_text("utf-8"))
    assert evaluation["run_id"] == f"{scenario.id}/candidate-a/t01"
    assert evaluation["claims"], "claims must be persisted for audit"
    assert evaluation["hard_constraint_cap_applied"] is True

    bundle = run_set / "evidence" / "bundles" / f"{scenario.id}.bundle.json"
    assert bundle.is_file()
    bundle_payload = json.loads(bundle.read_text("utf-8"))
    statuses = {
        url: source["status"] for url, source in bundle_payload["sources"].items()
    }
    assert statuses["https://parks.example/clifftop"] == "reachable"
    assert statuses["https://lookout.example/echo-point"] == "missing"
