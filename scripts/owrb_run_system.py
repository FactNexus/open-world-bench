#!/usr/bin/env python3
"""Run ONE system over an existing run-set's frozen scenarios.

Adds a late arm to a finished comparison without re-running the others:
executes every scenario instance in the run-set against the given system
YAML (skipping trials that already completed, so it is re-runnable after
interruptions) and writes artefacts into the same run-set. Follow with
`owrb evaluate --resume` to judge just the new trials.

Usage: uv run python owrb_run_system.py <system.yaml> <run-set-dir> [concurrency]
"""

import asyncio
import json
import sys
from pathlib import Path

from owrb.domain_loader import load_yaml
from owrb.models import ScenarioInstance, SystemDefinition
from owrb.runner import _execute_trial, write_run_artefacts


async def main(system_path: Path, run_set_directory: Path, concurrency: int) -> int:
    system = SystemDefinition.model_validate(load_yaml(system_path))
    manifest = json.loads((run_set_directory / "run-set.json").read_text())
    suite = manifest["suite"]
    timeout_seconds = suite.get("run_timeout_seconds", 600)

    scenarios = [
        ScenarioInstance.model_validate_json(p.read_text())
        for p in sorted((run_set_directory / "scenarios").glob("*.json"))
    ]
    todo = []
    for inst in scenarios:
        result_path = run_set_directory / inst.id / system.id / "t01" / "result.json"
        if (
            result_path.is_file()
            and json.loads(result_path.read_text()).get("status") == "completed"
        ):
            continue
        todo.append(inst)

    print(f"{system.id}: {len(todo)}/{len(scenarios)} scenarios to run "
          f"(concurrency {concurrency}, timeout {timeout_seconds}s)")

    semaphore = asyncio.Semaphore(concurrency)
    counts = {"completed": 0, "failed": 0, "timeout": 0}

    async def run_one(inst):
        async with semaphore:
            result = await _execute_trial(inst, system, "t01", timeout_seconds)
        write_run_artefacts(
            run_set_directory,
            inst,
            system,
            result,
            run_configuration={
                "suite_id": suite.get("id"),
                "timeout_seconds": timeout_seconds,
                # Appended arm: ran after the original comparison, not
                # interleaved — noted rather than faked.
                "execution_order": len(manifest.get("systems", [])),
                "repetitions": 1,
            },
        )
        counts[result.status] = counts.get(result.status, 0) + 1
        done = sum(counts.values())
        print(f"[{done}/{len(todo)}] {result.status:9s} {inst.id}", flush=True)

    async with asyncio.TaskGroup() as tg:
        for inst in todo:
            tg.create_task(run_one(inst))

    if system.id not in manifest.get("systems", []):
        manifest.setdefault("systems", []).append(system.id)
        (run_set_directory / "run-set.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )

    print(f"pass: {counts}")
    return 0 if counts["failed"] == 0 and counts["timeout"] == 0 else 2


if __name__ == "__main__":
    conc = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    sys.exit(asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]), conc)))
