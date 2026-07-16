"""Seeded, reproducible scenario generation (SPEC.md section 10).

Each instance seed is derived from the suite seed, the template ID, the
instance index, and the rejection attempt via SHA-256, so:

- the same suite seed and pack versions reproduce identical instances;
- adding templates or instances never perturbs other instances;
- rejection sampling stays deterministic.

Instances persist every selected parameter value, so replay never needs the
original provider (SPEC.md section 10.4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Any, Protocol

import orjson
from jinja2 import Environment, StrictUndefined
from jinja2 import TemplateError as JinjaTemplateError

from owrb.expressions import ExpressionError, evaluate_expression
from owrb.models import DomainPack, ProviderSpec, ScenarioInstance, ScenarioTemplate

DEFAULT_MAX_ATTEMPTS = 25


class GenerationError(ValueError):
    """Raised when an instance cannot be generated within the attempt budget."""


class ParameterProvider(Protocol):
    def select(self, random_generator: Random, context: dict[str, Any]) -> Any:
        """Select one parameter value using only the supplied deterministic generator."""
        ...


class ProviderFactory(Protocol):
    file_hashes: dict[str, str]

    def create(self, provider_spec: ProviderSpec) -> ParameterProvider:
        ...


@dataclass
class GenerationReport:
    """Dry-run and audit summary for one generation batch (SPEC.md section 10.5)."""

    requested: int = 0
    generated: int = 0
    rule_rejections: int = 0
    duplicate_rejections: int = 0
    per_template: dict[str, int] = field(default_factory=dict)
    rejected_rules: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "generated": self.generated,
            "rule_rejections": self.rule_rejections,
            "duplicate_rejections": self.duplicate_rejections,
            "per_template": dict(sorted(self.per_template.items())),
            "rejected_rules": dict(sorted(self.rejected_rules.items())),
        }


def derive_attempt_seed(suite_seed: int, template_id: str, index: int, attempt: int) -> int:
    """Deterministically derive the RNG seed for one generation attempt."""
    material = f"{suite_seed}:{template_id}:{index}:{attempt}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def canonical_prompt_hash(prompt: str) -> str:
    """Hash used for duplicate detection; whitespace runs are collapsed."""
    canonical = " ".join(prompt.split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _render_prompt(template: ScenarioTemplate, context: dict[str, Any]) -> str:
    environment = Environment(undefined=StrictUndefined, autoescape=False)
    try:
        rendered = environment.from_string(template.prompt).render(**context)
    except JinjaTemplateError as error:
        raise GenerationError(f"template {template.id!r} failed to render: {error}") from error
    lines = [line.rstrip() for line in rendered.strip().splitlines()]
    return "\n".join(lines)


def _resolve_parameters(
    template: ScenarioTemplate,
    domain_pack: DomainPack,
    provider_factory: ProviderFactory,
    random_generator: Random,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for name, parameter in template.parameters.items():
        if parameter.provider is not None:
            spec = parameter.provider
        elif parameter.source is not None:
            domain_parameter = domain_pack.parameters.get(parameter.source)
            if domain_parameter is None:
                raise GenerationError(
                    f"template {template.id!r} parameter {name!r} references "
                    f"unknown domain parameter {parameter.source!r}"
                )
            spec = domain_parameter.provider
        else:
            raise GenerationError(
                f"template {template.id!r} parameter {name!r} declares "
                "neither a source nor a provider"
            )
        provider = provider_factory.create(spec)
        context[name] = provider.select(random_generator, context)
    return context


def _rules_satisfied(
    template: ScenarioTemplate, context: dict[str, Any], report: GenerationReport
) -> bool:
    for rule in template.rules:
        try:
            satisfied = bool(evaluate_expression(rule, context))
        except ExpressionError as error:
            raise GenerationError(
                f"template {template.id!r} rule {rule!r} failed to evaluate: {error}"
            ) from error
        if not satisfied:
            report.rule_rejections += 1
            report.rejected_rules[rule] = report.rejected_rules.get(rule, 0) + 1
            return False
    return True


def generate_scenario_instance(
    domain_pack: DomainPack,
    template: ScenarioTemplate,
    provider_factory: ProviderFactory,
    suite_seed: int,
    index: int,
    seen_prompt_hashes: set[str],
    report: GenerationReport,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ScenarioInstance:
    """Generate one immutable instance, retrying on rule failures and duplicates."""
    for attempt in range(max_attempts):
        attempt_seed = derive_attempt_seed(suite_seed, template.id, index, attempt)
        random_generator = Random(attempt_seed)
        context = _resolve_parameters(template, domain_pack, provider_factory, random_generator)
        if not _rules_satisfied(template, context, report):
            continue
        prompt = _render_prompt(template, context)
        prompt_hash = canonical_prompt_hash(prompt)
        if prompt_hash in seen_prompt_hashes:
            report.duplicate_rejections += 1
            continue
        seen_prompt_hashes.add(prompt_hash)
        return ScenarioInstance(
            id=f"{domain_pack.id}.{template.id}.{index + 1:06d}",
            domain_id=domain_pack.id,
            domain_version=domain_pack.version,
            template_id=template.id,
            template_version=template.version,
            seed=attempt_seed,
            generated_at=datetime.now(tz=UTC),
            parameters=context,
            prompt=prompt,
            answer_contract=template.answer_contract,
            criteria=template.criteria,
            source_hashes=dict(sorted(provider_factory.file_hashes.items())),
            tags=list(template.tags),
        )
    raise GenerationError(
        f"template {template.id!r} instance {index} exhausted {max_attempts} attempts "
        "(check compatibility rules and parameter space size)"
    )


def generate_batch(
    domain_pack: DomainPack,
    templates: list[ScenarioTemplate],
    provider_factory: ProviderFactory,
    suite_seed: int,
    count: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> tuple[list[ScenarioInstance], GenerationReport]:
    """Generate ``count`` instances, cycling templates in sorted-ID order."""
    if not templates:
        raise GenerationError("domain pack has no scenario templates")
    ordered_templates = sorted(templates, key=lambda item: item.id)
    report = GenerationReport(requested=count)
    seen_prompt_hashes: set[str] = set()
    instances: list[ScenarioInstance] = []
    for index in range(count):
        template = ordered_templates[index % len(ordered_templates)]
        instance = generate_scenario_instance(
            domain_pack=domain_pack,
            template=template,
            provider_factory=provider_factory,
            suite_seed=suite_seed,
            index=index,
            seen_prompt_hashes=seen_prompt_hashes,
            report=report,
            max_attempts=max_attempts,
        )
        instances.append(instance)
        report.generated += 1
        report.per_template[template.id] = report.per_template.get(template.id, 0) + 1
    return instances, report


def instance_to_canonical_json(instance: ScenarioInstance) -> bytes:
    """Serialise an instance to sorted, indented JSON for byte-stable artefacts."""
    payload = instance.model_dump(mode="json")
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)


def write_instances(instances: list[ScenarioInstance], output_directory: Path) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for instance in instances:
        path = output_directory / f"{instance.id}.json"
        path.write_bytes(instance_to_canonical_json(instance) + b"\n")
        written.append(path)
    return written
