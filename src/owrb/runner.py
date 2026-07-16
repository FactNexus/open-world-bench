from __future__ import annotations

from typing import Protocol

from owrb.models import RunResult, ScenarioInstance, SystemDefinition


class SystemAdapter(Protocol):
    async def run(
        self,
        scenario: ScenarioInstance,
        system: SystemDefinition,
        trial_id: str,
    ) -> RunResult:
        ...
