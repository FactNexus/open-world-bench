import asyncio
import subprocess
import sys
from pathlib import Path

from conftest import make_scenario, make_system

from owrb.adapters.base import build_input_text
from owrb.adapters.command import CommandAdapter
from owrb.models import RunRequest

RUNNER = (
    Path(__file__).resolve().parents[1]
    / "systems"
    / "templates"
    / "runners"
    / "reference_runner.py"
)


def _request() -> RunRequest:
    scenario = make_scenario()
    return RunRequest(
        scenario=scenario,
        system=make_system(
            adapter="command", settings={"command": [sys.executable, str(RUNNER)]}
        ),
        trial_id="t01",
        input_text=build_input_text(scenario),
        timeout_seconds=30,
    )


def test_reference_runner_satisfies_the_command_contract() -> None:
    result = asyncio.run(CommandAdapter().run(_request()))
    assert result.status == "completed"
    assert "PLACEHOLDER" in result.answer  # the stub, until discover() is replaced
    assert result.metrics.latency_ms is not None
    assert result.metrics.cost_usd == 0.0
    assert isinstance(result.citations, list)


def test_reference_runner_rejects_bad_stdin() -> None:
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "invalid request" in proc.stderr
