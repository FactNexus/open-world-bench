from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderSpec(StrictModel):
    type: str
    path: str | None = None
    values: list[Any] | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class DomainParameterSpec(StrictModel):
    provider: ProviderSpec
    description: str | None = None


class DomainPack(StrictModel):
    schema_version: int = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    description: str
    version: str
    default_locale: str = "en-AU"
    default_timezone: str = "Australia/Sydney"
    templates: list[str]
    parameters: dict[str, DomainParameterSpec]
    source_policy: str | None = None
    licence: str | None = None
    attribution: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TemplateParameterSpec(StrictModel):
    source: str | None = None
    provider: ProviderSpec | None = None
    required: bool = True
    description: str | None = None


class CriterionSpec(StrictModel):
    id: str
    dimension: str
    title: str
    description: str
    hard: bool = False
    weight: float = Field(default=1.0, gt=0)
    validator: str | None = None


class AnswerContract(StrictModel):
    format: Literal["markdown", "text", "json"] = "markdown"
    citations_required: bool = True
    instructions: list[str] = Field(default_factory=list)


class ScenarioTemplate(StrictModel):
    schema_version: int = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    description: str | None = None
    prompt: str
    parameters: dict[str, TemplateParameterSpec]
    criteria: list[CriterionSpec] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    answer_contract: AnswerContract = Field(default_factory=AnswerContract)
    tags: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "mixed"


class ScenarioInstance(StrictModel):
    schema_version: int = 1
    id: str
    domain_id: str
    domain_version: str
    template_id: str
    template_version: str
    seed: int
    generated_at: datetime
    parameters: dict[str, Any]
    prompt: str
    answer_contract: AnswerContract
    criteria: list[CriterionSpec]
    source_hashes: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class SystemCapabilities(StrictModel):
    web_search: bool = False
    browser: bool = False
    domain_index: bool = False
    ontology: bool = False
    mcp: bool = False
    citations: bool = True
    trace: bool = False
    token_metrics: bool = False
    cost_metrics: bool = False


class SystemDefinition(StrictModel):
    schema_version: int = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    adapter: str
    provider: str | None = None
    model: str | None = None
    capabilities: SystemCapabilities = Field(default_factory=SystemCapabilities)
    settings: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)


class Citation(StrictModel):
    id: str
    url: str
    title: str | None = None
    source_name: str | None = None
    answer_spans: list[str] = Field(default_factory=list)


class RunMetrics(StrictModel):
    latency_ms: int | None = None
    time_to_first_token_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    retrieved_context_tokens: int | None = None
    tool_calls: int | None = None
    searches: int | None = None
    unique_sources: int | None = None
    cost_usd: float | None = None


class RunResult(StrictModel):
    schema_version: int = 1
    scenario_instance_id: str
    system_id: str
    trial_id: str
    status: Literal["completed", "failed", "timeout", "manual"]
    started_at: datetime
    completed_at: datetime
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    warnings: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class CriterionResult(StrictModel):
    criterion_id: str
    dimension: str
    score: float = Field(ge=0, le=1)
    passed: bool | None = None
    explanation: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    hard_failure: bool = False


class EvaluationResult(StrictModel):
    schema_version: int = 1
    run_id: str
    evaluated_at: datetime
    evaluator_version: str
    judge_configuration: dict[str, Any] = Field(default_factory=dict)
    criteria: list[CriterionResult]
    dimension_scores: dict[str, float]
    quality_score: float = Field(ge=0, le=100)
    hard_constraint_cap_applied: bool = False
    review_status: Literal["not_required", "required", "reviewed"] = "not_required"
    warnings: list[str] = Field(default_factory=list)
