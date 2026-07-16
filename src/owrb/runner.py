"""Run-set orchestration (SPEC.md section 14).

The runner freezes scenario instances first, then executes every runnable
system against every instance with per-instance randomised system order, a
global concurrency cap, and per-trial timeouts. Failures and timeouts are
preserved as run results rather than silently retried (SPEC.md 14.2), and
artefacts are written per trial under::

    runs/<run-set-id>/<scenario-instance-id>/<system-id>/<trial-id>/

Secrets are resolved from the environment inside adapters at call time and
never written to artefacts.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any

import orjson
from pydantic import ValidationError

from owrb.adapters import AdapterError, create_adapter
from owrb.adapters.base import build_input_text, utc_now
from owrb.domain_loader import load_yaml
from owrb.generation import generate_batch, instance_to_canonical_json
from owrb.models import (
    RunRequest,
    RunResult,
    ScenarioInstance,
    SuiteConfig,
    SystemDefinition,
)
from owrb.providers.builtin import BuiltinProviderFactory
from owrb.validation import validate_domain_pack


class SuiteError(ValueError):
    """Raised when a suite configuration cannot be loaded or executed."""


@dataclass
class RunSetSummary:
    run_set_directory: Path
    scenario_count: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    manual_systems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_set_directory": str(self.run_set_directory),
            "scenario_count": self.scenario_count,
            "completed": self.completed,
            "failed": self.failed,
            "timed_out": self.timed_out,
            "manual_systems": self.manual_systems,
            "warnings": self.warnings,
        }


def load_suite(suite_path: Path) -> SuiteConfig:
    """Load a suite file; a ``{suite: path}`` pointer file is followed once."""
    if not suite_path.is_file():
        raise SuiteError(f"suite file not found: {suite_path}")
    raw = load_yaml(suite_path)
    if isinstance(raw, dict) and set(raw) == {"suite"}:
        pointer = Path(str(raw["suite"]))
        if not pointer.is_absolute():
            candidate = suite_path.parent / pointer
            pointer = candidate if candidate.is_file() else pointer
        return load_suite(pointer)
    try:
        return SuiteConfig.model_validate(raw)
    except ValidationError as error:
        raise SuiteError(f"invalid suite {suite_path}: {error}") from error


def _resolve_relative(base: Path, reference: str) -> Path:
    candidate = Path(reference)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return base / reference


def _load_systems(suite: SuiteConfig, suite_directory: Path) -> list[SystemDefinition]:
    systems: list[SystemDefinition] = []
    seen_ids: set[str] = set()
    for reference in suite.systems:
        system_path = _resolve_relative(suite_directory, reference)
        if not system_path.is_file():
            raise SuiteError(f"system definition not found: {reference}")
        try:
            system = SystemDefinition.model_validate(load_yaml(system_path))
        except ValidationError as error:
            raise SuiteError(f"invalid system definition {system_path}: {error}") from error
        if system.id in seen_ids:
            raise SuiteError(f"duplicate system id {system.id!r} in suite")
        seen_ids.add(system.id)
        systems.append(system)
    if not systems:
        raise SuiteError("suite lists no systems")
    return systems


def _generate_scenarios(suite: SuiteConfig, suite_directory: Path) -> list[ScenarioInstance]:
    domain_directory = _resolve_relative(suite_directory, suite.domain.path)
    validation = validate_domain_pack(domain_directory)
    if not validation.valid or validation.domain_pack is None:
        details = "; ".join(
            f"{issue.location}: {issue.message}"
            for issue in validation.issues
            if issue.severity == "error"
        )
        raise SuiteError(f"domain pack {suite.domain.path} is invalid: {details}")
    if validation.domain_pack.id != suite.domain.id:
        raise SuiteError(
            f"suite domain id {suite.domain.id!r} does not match pack id "
            f"{validation.domain_pack.id!r}"
        )
    generation = suite.scenario_generation
    factory = BuiltinProviderFactory(domain_directory)
    instances, _ = generate_batch(
        domain_pack=validation.domain_pack,
        templates=validation.templates,
        provider_factory=factory,
        suite_seed=generation.seed,
        count=generation.count,
        template_quotas=generation.template_quotas or None,
    )
    return instances


def _canonical_json(payload: Any) -> bytes:
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"


def write_run_artefacts(
    run_set_directory: Path,
    scenario: ScenarioInstance,
    system: SystemDefinition,
    result: RunResult,
    run_configuration: dict[str, Any],
) -> Path:
    """Write the per-trial artefact tree (SPEC.md 14.3). Never receives secrets."""
    trial_directory = run_set_directory / scenario.id / system.id / result.trial_id
    trial_directory.mkdir(parents=True, exist_ok=True)
    # system.environment maps logical names to environment *variable names*;
    # values are resolved inside adapters and never reach a RunResult.
    (trial_directory / "config.json").write_bytes(
        _canonical_json({"system": system.model_dump(mode="json"), "run": run_configuration})
    )
    (trial_directory / "scenario.json").write_bytes(instance_to_canonical_json(scenario) + b"\n")
    (trial_directory / "answer.md").write_text(result.answer, encoding="utf-8")
    (trial_directory / "citations.json").write_bytes(
        _canonical_json([citation.model_dump(mode="json") for citation in result.citations])
    )
    (trial_directory / "metrics.json").write_bytes(
        _canonical_json(result.metrics.model_dump(mode="json"))
    )
    with (trial_directory / "trace.jsonl").open("wb") as trace_file:
        for event in result.trace:
            trace_file.write(orjson.dumps(event, option=orjson.OPT_SORT_KEYS) + b"\n")
    (trial_directory / "result.json").write_bytes(_canonical_json(result.model_dump(mode="json")))
    return trial_directory


def _failure_result(
    scenario: ScenarioInstance,
    system: SystemDefinition,
    trial_id: str,
    status: str,
    message: str,
) -> RunResult:
    now = utc_now()
    return RunResult(
        scenario_instance_id=scenario.id,
        system_id=system.id,
        trial_id=trial_id,
        status=status,  # type: ignore[arg-type]
        started_at=now,
        completed_at=now,
        answer="",
        warnings=[message],
    )


async def _execute_trial(
    scenario: ScenarioInstance,
    system: SystemDefinition,
    trial_id: str,
    timeout_seconds: int,
) -> RunResult:
    request = RunRequest(
        scenario=scenario,
        system=system,
        trial_id=trial_id,
        input_text=build_input_text(scenario),
        timeout_seconds=timeout_seconds,
    )
    try:
        adapter = create_adapter(system)
        return await asyncio.wait_for(adapter.run(request), timeout=timeout_seconds)
    except TimeoutError:
        return _failure_result(
            scenario, system, trial_id, "timeout", f"timed out after {timeout_seconds}s"
        )
    except AdapterError as error:
        return _failure_result(scenario, system, trial_id, "failed", str(error))
    except Exception as error:  # noqa: BLE001 - failures must be preserved, not raised
        return _failure_result(
            scenario, system, trial_id, "failed", f"{type(error).__name__}: {error}"
        )


async def execute_run_set(
    suite: SuiteConfig,
    suite_directory: Path,
    run_set_directory: Path,
) -> RunSetSummary:
    summary = RunSetSummary(run_set_directory=run_set_directory)
    systems = _load_systems(suite, suite_directory)
    runnable = [system for system in systems if system.adapter != "manual_import"]
    summary.manual_systems = [
        system.id for system in systems if system.adapter == "manual_import"
    ]
    for system_id in summary.manual_systems:
        summary.warnings.append(
            f"system {system_id!r} is manual_import; supply results with 'owrb import'"
        )

    instances = _generate_scenarios(suite, suite_directory)
    summary.scenario_count = len(instances)
    scenario_directory = run_set_directory / "scenarios"
    scenario_directory.mkdir(parents=True, exist_ok=True)
    for instance in instances:
        (scenario_directory / f"{instance.id}.json").write_bytes(
            instance_to_canonical_json(instance) + b"\n"
        )

    started_at = utc_now()
    semaphore = asyncio.Semaphore(suite.concurrency)

    async def run_one(
        instance: ScenarioInstance, system: SystemDefinition, trial_id: str, order: int
    ) -> RunResult:
        async with semaphore:
            result = await _execute_trial(instance, system, trial_id, suite.run_timeout_seconds)
        write_run_artefacts(
            run_set_directory,
            instance,
            system,
            result,
            run_configuration={
                "suite_id": suite.id,
                "timeout_seconds": suite.run_timeout_seconds,
                "execution_order": order,
                "repetitions": suite.repetitions,
            },
        )
        return result

    tasks: list[asyncio.Task[RunResult]] = []
    async with asyncio.TaskGroup() as task_group:
        for instance in instances:
            ordered_systems = list(runnable)
            if suite.randomise_system_order:
                Random(f"{suite.scenario_generation.seed}:{instance.id}:order").shuffle(
                    ordered_systems
                )
            for repetition in range(1, suite.repetitions + 1):
                for order, system in enumerate(ordered_systems):
                    tasks.append(
                        task_group.create_task(
                            run_one(instance, system, f"t{repetition:02d}", order)
                        )
                    )

    for task in tasks:
        result = task.result()
        if result.status == "completed":
            summary.completed += 1
        elif result.status == "timeout":
            summary.timed_out += 1
        else:
            summary.failed += 1

    manifest = {
        "suite": suite.model_dump(mode="json"),
        "systems": [system.id for system in systems],
        "manual_systems": summary.manual_systems,
        "started_at": started_at.isoformat(),
        "completed_at": utc_now().isoformat(),
        "summary": {
            "scenario_count": summary.scenario_count,
            "completed": summary.completed,
            "failed": summary.failed,
            "timed_out": summary.timed_out,
        },
    }
    (run_set_directory / "run-set.json").write_bytes(_canonical_json(manifest))
    return summary


def import_manual_result(
    run_set_directory: Path,
    scenario_id: str,
    system_id: str,
    answer_path: Path,
    citations_path: Path | None = None,
    latency_ms: int | None = None,
    product_metadata: dict[str, Any] | None = None,
) -> Path:
    """Import a manually captured answer as a ``manual`` trial (SPEC.md 13.5)."""
    scenario_file = run_set_directory / "scenarios" / f"{scenario_id}.json"
    if not scenario_file.is_file():
        raise SuiteError(f"scenario {scenario_id!r} not found in {run_set_directory}")
    scenario = ScenarioInstance.model_validate(json.loads(scenario_file.read_text("utf-8")))
    if not answer_path.is_file():
        raise SuiteError(f"answer file not found: {answer_path}")
    answer = answer_path.read_text(encoding="utf-8")

    citations = []
    if citations_path is not None:
        from owrb.adapters.generic_http import _parse_citations

        citations = _parse_citations(json.loads(citations_path.read_text("utf-8")))

    system_directory = run_set_directory / scenario_id / system_id
    existing = len(list(system_directory.glob("manual-*"))) if system_directory.is_dir() else 0
    trial_id = f"manual-{existing + 1:02d}"

    now = utc_now()
    result = RunResult(
        scenario_instance_id=scenario_id,
        system_id=system_id,
        trial_id=trial_id,
        status="manual",
        started_at=now,
        completed_at=now,
        answer=answer,
        citations=citations,
        warnings=["result imported manually; efficiency metrics are not comparable"],
        provider_metadata=product_metadata or {},
    )
    if latency_ms is not None:
        result.metrics.latency_ms = latency_ms
    system = SystemDefinition(id=system_id, name=system_id, adapter="manual_import")
    return write_run_artefacts(
        run_set_directory,
        scenario,
        system,
        result,
        run_configuration={"imported": True},
    )
