"""Adapter contract and shared helpers (SPEC.md section 13).

Adapters normalise heterogeneous candidate systems into the common
:class:`~owrb.models.RunResult`. They must never place secret values in the
result; secrets are resolved from the environment at call time and used only
in outbound requests.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from owrb.models import RunMetrics, RunRequest, RunResult, ScenarioInstance

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class AdapterError(RuntimeError):
    """Raised when an adapter cannot be constructed or configured."""


class SystemAdapter(Protocol):
    async def run(self, request: RunRequest) -> RunResult:
        ...


def build_input_text(scenario: ScenarioInstance) -> str:
    """Render the prompt plus the short common answer contract (SPEC.md 13.4).

    Every system receives exactly this text: no domain-pack internals, no
    scoring rubric, and no other system's output.
    """
    contract = scenario.answer_contract
    requirements = [
        f"Provide a direct answer in {contract.format} format.",
        "Address every stated constraint explicitly.",
    ]
    if contract.citations_required:
        requirements.append(
            "Support factual and operational claims with citations (URLs) placed "
            "close to the claims they support."
        )
    requirements.append(
        "State clearly where information could not be verified. Do not present a "
        "claim as current unless a current source supports it."
    )
    requirements.extend(contract.instructions)
    requirement_lines = "\n".join(f"- {requirement}" for requirement in requirements)
    return f"{scenario.prompt}\n\nResponse requirements:\n{requirement_lines}\n"


def resolve_environment_value(variable_name: str) -> str:
    value = os.environ.get(variable_name)
    if not value:
        raise AdapterError(f"environment variable {variable_name!r} is not set")
    return value


def substitute_environment(text: str) -> str:
    """Replace ``${VAR}`` placeholders in configuration strings."""

    def _replace(match: re.Match[str]) -> str:
        return resolve_environment_value(match.group(1))

    return _ENV_PLACEHOLDER.sub(_replace, text)


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def compute_cost_usd(settings: dict[str, Any], metrics: RunMetrics) -> float | None:
    """Compute cost from system-declared per-million-token rates (SPEC.md 9.4)."""
    cost_config = settings.get("cost")
    if not isinstance(cost_config, dict):
        return None
    if metrics.input_tokens is None or metrics.output_tokens is None:
        return None
    try:
        input_rate = float(cost_config["input_per_mtok"])
        output_rate = float(cost_config["output_per_mtok"])
    except (KeyError, TypeError, ValueError):
        return None
    cost = (
        metrics.input_tokens * input_rate + metrics.output_tokens * output_rate
    ) / 1_000_000
    return round(cost, 6)
