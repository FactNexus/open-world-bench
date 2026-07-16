"""Adapter registry: map a system definition to a SystemAdapter instance."""

from __future__ import annotations

from owrb.adapters.base import AdapterError, SystemAdapter, build_input_text
from owrb.models import SystemDefinition

__all__ = ["AdapterError", "SystemAdapter", "build_input_text", "create_adapter"]


def create_adapter(system: SystemDefinition) -> SystemAdapter:
    adapter_name = system.adapter
    if adapter_name == "provider_specific":
        # Resolve the concrete provider adapter from the provider field.
        adapter_name = system.provider or ""

    if adapter_name == "generic_http":
        from owrb.adapters.generic_http import GenericHttpAdapter

        return GenericHttpAdapter()
    if adapter_name == "command":
        from owrb.adapters.command import CommandAdapter

        return CommandAdapter()
    if adapter_name == "anthropic":
        from owrb.adapters.anthropic_api import AnthropicAdapter

        return AnthropicAdapter()
    if adapter_name == "openai":
        from owrb.adapters.openai_api import OpenAiAdapter

        return OpenAiAdapter()
    if adapter_name == "manual_import":
        raise AdapterError(
            f"system {system.id!r} uses manual_import; results are supplied with "
            "'owrb import', not executed by the runner"
        )
    raise AdapterError(f"system {system.id!r} uses unknown adapter {system.adapter!r}")
