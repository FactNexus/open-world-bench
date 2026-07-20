"""Deep validation for domain packs and system definitions (Milestone 0).

Validation goes beyond schema checks: template globs are resolved, parameter
references are cross-checked against the manifest, file-backed providers are
loaded to prove their data parses, prompt placeholders are compared with
declared parameters, and compatibility rules are parsed with the safe
expression engine. Every finding carries a location so errors point at the
offending file and field (implementation-plan PR 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, meta
from jinja2 import TemplateSyntaxError as JinjaTemplateSyntaxError
from pydantic import ValidationError

from owrb.domain_loader import load_domain_pack, load_scenario_template, load_yaml
from owrb.expressions import ExpressionError, parse_expression
from owrb.models import DomainPack, ProviderSpec, ScenarioTemplate, SystemDefinition
from owrb.providers.builtin import BuiltinProviderFactory, ProviderConfigurationError

KNOWN_ADAPTERS = frozenset(
    {
        "generic_http",
        "command",
        "manual_import",
        "provider_specific",
        "openai",
        "anthropic",
        "google",
        "openrouter",
        "openai_compatible",
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    severity: Literal["error", "warning"]
    location: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "location": self.location, "message": self.message}


@dataclass
class DomainValidationResult:
    domain_pack: DomainPack | None
    templates: list[ScenarioTemplate]
    issues: list[ValidationIssue]

    @property
    def valid(self) -> bool:
        return self.domain_pack is not None and not any(
            issue.severity == "error" for issue in self.issues
        )


def _format_pydantic_error(error: ValidationError) -> str:
    parts = []
    for detail in error.errors():
        location = ".".join(str(item) for item in detail["loc"]) or "<root>"
        parts.append(f"{location}: {detail['msg']}")
    return "; ".join(parts)


def validate_domain_pack(domain_directory: Path) -> DomainValidationResult:
    issues: list[ValidationIssue] = []
    manifest_location = str(domain_directory / "domain.yaml")

    try:
        domain_pack = load_domain_pack(domain_directory)
    except FileNotFoundError:
        issues.append(ValidationIssue("error", manifest_location, "domain.yaml not found"))
        return DomainValidationResult(None, [], issues)
    except ValidationError as error:
        issues.append(ValidationIssue("error", manifest_location, _format_pydantic_error(error)))
        return DomainValidationResult(None, [], issues)

    factory = BuiltinProviderFactory(domain_directory)
    for name, parameter in domain_pack.parameters.items():
        _check_provider(
            factory, parameter.provider, f"{manifest_location}#parameters.{name}", issues
        )

    if domain_pack.source_policy is not None:
        policy_path = domain_directory / domain_pack.source_policy
        if not policy_path.is_file():
            issues.append(
                ValidationIssue(
                    "error",
                    f"{manifest_location}#source_policy",
                    f"source policy file not found: {domain_pack.source_policy}",
                )
            )

    templates = _validate_templates(domain_directory, domain_pack, factory, issues)
    return DomainValidationResult(domain_pack, templates, issues)


def _check_provider(
    factory: BuiltinProviderFactory,
    provider_spec: ProviderSpec,
    location: str,
    issues: list[ValidationIssue],
) -> None:
    try:
        factory.create(provider_spec)
    except ProviderConfigurationError as error:
        issues.append(ValidationIssue("error", location, str(error)))


def _validate_templates(
    domain_directory: Path,
    domain_pack: DomainPack,
    factory: BuiltinProviderFactory,
    issues: list[ValidationIssue],
) -> list[ScenarioTemplate]:
    manifest_location = str(domain_directory / "domain.yaml")
    template_paths: list[Path] = []
    for pattern in domain_pack.templates:
        matches = sorted(domain_directory.glob(pattern))
        if not matches:
            issues.append(
                ValidationIssue(
                    "error",
                    f"{manifest_location}#templates",
                    f"template pattern {pattern!r} matches no files",
                )
            )
        template_paths.extend(matches)

    templates: list[ScenarioTemplate] = []
    seen_ids: dict[str, Path] = {}
    for template_path in template_paths:
        location = str(template_path)
        try:
            template = load_scenario_template(template_path)
        except ValidationError as error:
            issues.append(ValidationIssue("error", location, _format_pydantic_error(error)))
            continue

        if template.id in seen_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    location,
                    f"duplicate template id {template.id!r} "
                    f"(also defined in {seen_ids[template.id]})",
                )
            )
            continue
        seen_ids[template.id] = template_path
        templates.append(template)
        _validate_template_content(template, domain_pack, factory, location, issues)
    return templates


def _validate_template_content(
    template: ScenarioTemplate,
    domain_pack: DomainPack,
    factory: BuiltinProviderFactory,
    location: str,
    issues: list[ValidationIssue],
) -> None:
    for name, parameter in template.parameters.items():
        parameter_location = f"{location}#parameters.{name}"
        if parameter.provider is None and parameter.source is None:
            issues.append(
                ValidationIssue(
                    "error", parameter_location, "parameter needs either a source or a provider"
                )
            )
        if parameter.provider is not None and parameter.source is not None:
            issues.append(
                ValidationIssue(
                    "error", parameter_location, "parameter must not set both source and provider"
                )
            )
        if parameter.source is not None and parameter.source not in domain_pack.parameters:
            issues.append(
                ValidationIssue(
                    "error",
                    parameter_location,
                    f"unknown domain parameter {parameter.source!r}",
                )
            )
        if parameter.provider is not None:
            _check_provider(factory, parameter.provider, parameter_location, issues)

    environment = Environment(autoescape=False)
    try:
        parsed_prompt = environment.parse(template.prompt)
    except JinjaTemplateSyntaxError as error:
        issues.append(
            ValidationIssue("error", f"{location}#prompt", f"prompt template error: {error}")
        )
    else:
        undeclared = meta.find_undeclared_variables(parsed_prompt)
        missing = sorted(undeclared - set(template.parameters))
        if missing:
            issues.append(
                ValidationIssue(
                    "error",
                    f"{location}#prompt",
                    f"prompt references undeclared parameters: {', '.join(missing)}",
                )
            )

    for rule in template.rules:
        try:
            parse_expression(rule)
        except ExpressionError as error:
            issues.append(
                ValidationIssue("error", f"{location}#rules", f"rule {rule!r}: {error}")
            )

    seen_criteria: set[str] = set()
    for criterion in template.criteria:
        if criterion.id in seen_criteria:
            issues.append(
                ValidationIssue(
                    "error", f"{location}#criteria", f"duplicate criterion id {criterion.id!r}"
                )
            )
        seen_criteria.add(criterion.id)


@dataclass
class SystemValidationResult:
    system: SystemDefinition | None
    issues: list[ValidationIssue]

    @property
    def valid(self) -> bool:
        return self.system is not None and not any(
            issue.severity == "error" for issue in self.issues
        )


def validate_system_definition(system_path: Path) -> SystemValidationResult:
    issues: list[ValidationIssue] = []
    location = str(system_path)
    if not system_path.is_file():
        issues.append(ValidationIssue("error", location, "system definition file not found"))
        return SystemValidationResult(None, issues)

    try:
        raw: Any = load_yaml(system_path)
        system = SystemDefinition.model_validate(raw)
    except ValidationError as error:
        issues.append(ValidationIssue("error", location, _format_pydantic_error(error)))
        return SystemValidationResult(None, issues)

    if system.adapter not in KNOWN_ADAPTERS:
        issues.append(
            ValidationIssue(
                "warning",
                f"{location}#adapter",
                f"adapter {system.adapter!r} is not a built-in adapter "
                f"({', '.join(sorted(KNOWN_ADAPTERS))})",
            )
        )
    for key, value in system.environment.items():
        if not value.replace("_", "").isalnum() or value != value.upper():
            issues.append(
                ValidationIssue(
                    "warning",
                    f"{location}#environment.{key}",
                    f"{value!r} does not look like an environment variable name; "
                    "secrets must be referenced by name, never embedded",
                )
            )
    return SystemValidationResult(system, issues)
