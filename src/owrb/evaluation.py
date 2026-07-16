from __future__ import annotations

from typing import Protocol

from owrb.models import CriterionResult, RunResult, ScenarioInstance


class CriterionEvaluator(Protocol):
    async def evaluate(
        self,
        scenario: ScenarioInstance,
        run_result: RunResult,
    ) -> list[CriterionResult]:
        ...
