#!/usr/bin/env python3
"""Re-run only the failed/timeout trials of an existing OWRB run-set.

owrb's runner has no resume support (SPEC 14.2 preserves failures rather than
retrying), so this drives the runner's own internals — _execute_trial +
write_run_artefacts — over just the (scenario, system) pairs whose
result.json is not 'completed', preserving each trial's original
execution_order, then rewrites the run-set.json summary.

Usage: uv run python owrb_resume_failed.py <suite.yaml> <run-set-dir>
"""

import asyncio
import json
import sys
from pathlib import Path

from owrb.models import ScenarioInstance
from owrb.runner import (
    _execute_trial,
    _load_systems,
    load_suite,
    write_run_artefacts,
)


async def main(suite_path: Path, run_set_directory: Path) -> int:
    suite = load_suite(suite_path)
    systems = {
        s.id: s
        for s in _load_systems(suite, suite_path.resolve().parent)
        if s.adapter != "manual_import"
    }

    scenarios = {}
    for p in sorted((run_set_directory / "scenarios").glob("*.json")):
        inst = ScenarioInstance.model_validate_json(p.read_text())
        scenarios[inst.id] = inst

    todo = []  # (instance, system, trial_id, execution_order)
    for scenario_id, inst in scenarios.items():
        for system_id, system in systems.items():
            for result_path in sorted(
                (run_set_directory / scenario_id / system_id).glob("*/result.json")
            ):
                result = json.loads(result_path.read_text())
                if result.get("status") == "completed":
                    continue
                config = json.loads((result_path.parent / "config.json").read_text())
                todo.append(
                    (
                        inst,
                        system,
                        result_path.parent.name,
                        config["run"].get("execution_order", 0),
                    )
                )

    print(f"{len(todo)} failed trials to re-run "
          f"(concurrency {suite.concurrency}, timeout {suite.run_timeout_seconds}s)")

    semaphore = asyncio.Semaphore(suite.concurrency)
    counts = {"completed": 0, "failed": 0, "timeout": 0}

    async def run_one(inst, system, trial_id, order):
        async with semaphore:
            result = await _execute_trial(inst, system, trial_id, suite.run_timeout_seconds)
        write_run_artefacts(
            run_set_directory,
            inst,
            system,
            result,
            run_configuration={
                "suite_id": suite.id,
                "timeout_seconds": suite.run_timeout_seconds,
                "execution_order": order,
                "repetitions": suite.repetitions,
            },
        )
        counts[result.status] = counts.get(result.status, 0) + 1
        done = sum(counts.values())
        print(f"[{done}/{len(todo)}] {result.status:9s} {system.id} :: {inst.id}", flush=True)

    async with asyncio.TaskGroup() as tg:
        for inst, system, trial_id, order in todo:
            tg.create_task(run_one(inst, system, trial_id, order))

    # Recount the whole run-set and refresh the manifest summary.
    totals = {"completed": 0, "failed": 0, "timeout": 0}
    for scenario_id in scenarios:
        for system_id in systems:
            for result_path in (run_set_directory / scenario_id / system_id).glob(
                "*/result.json"
            ):
                status = json.loads(result_path.read_text()).get("status", "failed")
                totals[status] = totals.get(status, 0) + 1

    manifest_path = run_set_directory / "run-set.json"
    manifest = json.loads(manifest_path.read_text())
    from owrb.adapters.base import utc_now

    manifest["completed_at"] = utc_now().isoformat()
    manifest["summary"]["completed"] = totals["completed"]
    manifest["summary"]["failed"] = totals["failed"]
    manifest["summary"]["timed_out"] = totals["timeout"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"resume pass: {counts}")
    print(f"run-set totals: {totals}")
    return 0 if totals["failed"] == 0 and totals["timeout"] == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]))))
