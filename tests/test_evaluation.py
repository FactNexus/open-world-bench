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
    assert (
        create_judge(JudgeConfig(adapter="openrouter", model="anthropic/claude-opus-4.8"))
        is not None
    )


def test_openrouter_judge_completes_via_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from owrb.judge import JudgeError, OpenAiCompatibleJudge

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '[{"verdict": "supported"}]'}}]},
        )

    judge = OpenAiCompatibleJudge(
        JudgeConfig(adapter="openrouter", model="anthropic/claude-opus-4.8")
    )
    judge.transport = httpx.MockTransport(handler)
    text = asyncio.run(judge.complete("system prompt", "user prompt"))
    assert text == '[{"verdict": "supported"}]'
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["auth"] == "Bearer openrouter-secret"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "system prompt"}

    # The generic flavour must be pointed at a gateway explicitly.
    with pytest.raises(JudgeError, match="base_url"):
        OpenAiCompatibleJudge(
            JudgeConfig(adapter="openai_compatible", model="m"), flavour="openai_compatible"
        )


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


def test_as_list_unwraps_wrapped_judge_arrays() -> None:
    from owrb.evaluation import _as_list

    assert _as_list([1, 2]) == [1, 2]
    assert _as_list({"claims": [{"id": "c1"}]}) == [{"id": "c1"}]
    assert _as_list({"note": "x", "verdicts": [{"id": "v1"}]}) == [{"id": "v1"}]
    assert _as_list({"anything": [{"id": "a"}]}) == [{"id": "a"}]
    assert _as_list({"a": [1], "b": [2]}) is None
    assert _as_list({"text": "no arrays here"}) is None
    assert _as_list("string") is None


def test_extract_json_salvages_truncated_array() -> None:
    from owrb.judge import extract_json

    truncated = (
        '[\n  {"id": "c1", "text": "A \\"quoted\\" claim, with a } brace", "citation_ids": []},\n'
        '  {"id": "c2", "text": "second", "citation_ids": ["c1"]},\n  {"id": "c3", "text": "cut of'
    )
    value = extract_json(truncated)
    assert [item["id"] for item in value] == ["c1", "c2"]
    assert value[0]["text"] == 'A "quoted" claim, with a } brace'


def test_extract_json_skips_a_malformed_object_inside_an_array() -> None:
    from owrb.judge import extract_json

    text = '[{"id": "a", "score": 1.0}, {"id": "b", "explanation": "bad \\x escape"}, {"id": "c", "score": 0.5}'
    value = extract_json(text)
    assert [item["id"] for item in value] == ["a", "c"]


# --- decision-model judge -----------------------------------------------------------


class FakeDecisionClient:
    """Answers verdict/rubric questions from the state; records what it was asked."""

    identity = {"adapter": "fake_decisions", "model": "scripted"}

    def __init__(self) -> None:
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self.states: list[object] = []

    async def decide(self, state: object, questions: dict[str, object]) -> dict[str, object]:
        self.states.append(state)
        self.usage["calls"] += 1
        if "verdict" in questions:
            text = str(state)
            choice = "supported" if "wheelchair accessible" in text else "not_addressed"
            probabilities = {"supported": 0.0, "contradicted": 0.0, "not_addressed": 0.0}
            probabilities[choice] = 0.9
            return {
                "verdict": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probabilities,
                    "confidence": 0.88,
                },
                "source_suitable": {"type": "noul", "noul": 0.7},
            }
        answers: dict[str, object] = {}
        for key in questions:
            if key.endswith("__score"):
                answers[key] = {
                    "type": "score",
                    "score": 1.0,
                    "confidence": 0.6,
                    "probabilities": {},
                }
            elif key.endswith("__passed"):
                answers[key] = {"type": "noul", "noul": 0.2}
        return answers


def test_decision_judge_takes_over_verdicts_and_rubric() -> None:
    from owrb.evaluation import EVIDENCE_HANDLING, EvaluationConfig, evaluate_run

    scenario = scenario_with_criteria()
    judge = FakeJudge()
    decision = FakeDecisionClient()
    evaluation = asyncio.run(
        evaluate_run(
            scenario, completed_result(), reachable_evidence(), judge, EvaluationConfig(), decision
        )
    )
    verdicts = {claim.id: claim.verdict for claim in evaluation.claims}
    assert verdicts["c1"] == "supported"
    assert verdicts["c2"] == "not_addressed"
    assert verdicts["c3"] == "no_citation"
    assert all(claim.confidence == 0.88 for claim in evaluation.claims if claim.citation_ids)
    assert all(
        claim.explanation.startswith("decision model:")
        for claim in evaluation.claims
        if claim.citation_ids
    )
    template_findings = [
        finding
        for finding in evaluation.criteria
        if not finding.criterion_id.startswith("framework.")
    ]
    assert template_findings
    assert all(f.explanation.startswith("decision model:") for f in template_findings)
    assert all(f.score == round(1.0 / 3, 4) and f.passed is False for f in template_findings)
    assert evaluation.judge_configuration == {
        "adapter": "fake",
        "model": "scripted",
        "decisions": {"adapter": "fake_decisions", "model": "scripted"},
        "evidence_handling": EVIDENCE_HANDLING,
    }
    # decomposition still went through the text judge; verdicts and rubric did not
    assert len(judge.prompts) == 1
    assert decision.usage["calls"] == 3  # two cited claims + one rubric call


def test_judge_configuration_change_invalidates_resume(tmp_path: Path) -> None:
    from owrb.evaluation import _already_evaluated, judge_configuration_for

    trial = tmp_path / "t01"
    trial.mkdir()
    (trial / "evaluation.json").write_text(
        json.dumps({"judge_configuration": {"adapter": "fake", "model": "scripted"}}),
        encoding="utf-8",
    )
    assert not _already_evaluated(trial, judge_configuration_for(FakeJudge(), None))
    (trial / "evaluation.json").write_text(
        json.dumps({"judge_configuration": judge_configuration_for(FakeJudge(), None)}),
        encoding="utf-8",
    )
    assert _already_evaluated(trial, judge_configuration_for(FakeJudge(), None))
    assert not _already_evaluated(trial, judge_configuration_for(FakeJudge(), FakeDecisionClient()))


def test_support_prompt_shows_claim_relevant_passages_not_front_matter() -> None:
    from owrb.evaluation import build_support_prompt

    url = "https://parks.example/clifftop"
    record, _ = reachable_evidence()[url]
    filler = "\n\n".join(
        f"Paragraph {n} about the visitor centre and the car park." for n in range(60)
    )
    page = (
        "---\nversion: 1.0.0\nfetched_at: 2026-09-20\n---\n\n# Clifftop\n\n" + filler
        + "\n\n## Fees\n\n| Item | Price | Reported |\n|---|---|---|\n"
        + "| Adult | AUD 12.00 | 2025-07 |\n"
    )
    prompt = build_support_prompt(
        [
            {
                "id": "c1",
                "text": "Adult entry costs AUD 12.00, reported July 2025",
                "citation_ids": ["c1"],
            }
        ],
        {"c1": url},
        {url: (record, page)},
    )
    assert "version: 1.0.0" not in prompt
    assert "| Adult | AUD 12.00 | 2025-07 |" in prompt
