from __future__ import annotations

from random import Random
from typing import Any, Protocol

from owrb.models import ProviderSpec, ScenarioInstance, ScenarioTemplate


class ParameterProvider(Protocol):
    def select(self, random_generator: Random, context: dict[str, Any]) -> Any:
        """Select one parameter value using only the supplied deterministic generator."""
        ...


class ProviderFactory(Protocol):
    def create(self, provider_spec: ProviderSpec, base_directory: str) -> ParameterProvider:
        ...


def generate_scenario_instance(
    template: ScenarioTemplate,
    seed: int,
    provider_factory: ProviderFactory,
) -> ScenarioInstance:
    """Generate one immutable instance. Implementation target for Milestone 1."""
    del template, seed, provider_factory
    raise NotImplementedError
