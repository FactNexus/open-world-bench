import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml
from conftest import write_minimal_pack

from owrb.models import SuiteConfig
from owrb.runner import (
    SuiteError,
    execute_run_set,
    import_manual_result,
    load_suite,
)

OK_COMMAND = [
    sys.executable,
    "-c",
    (
        "import json, sys; request = json.load(sys.stdin); "
        "print(json.dumps({'answer': 'ok: ' + request['scenario_instance_id'], "
        "'citations': ['https://example.com/ok'], "
        "'metrics': {'input_tokens': 4, 'output_tokens': 2}}))"
    ),
]
FAILING_COMMAND = [sys.executable, "-c", "import sys; sys.exit(2)"]
SLEEPING_COMMAND = [sys.executable, "-c", "import time; time.sleep(30)"]


def write_system(path: Path, system_id: str, command: list[str]) -> str:
    payload = {
        "schema_version": 1,
        "id": system_id,
        "name": system_id,
        "adapter": "command",
        "settings": {"command": command},
        "environment": {"api_key": "OWRB_TEST_SECRET"},
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return str(path)


def build_suite(
    tmp_path: Path, timeout: int = 20, repetitions: int = 1
) -> tuple[SuiteConfig, Path]:
    pack = write_minimal_pack(tmp_path)
    systems_directory = tmp_path / "systems"
    systems_directory.mkdir()
    system_paths = [
        write_system(systems_directory / "ok.yaml", "ok-system", OK_COMMAND),
        write_system(systems_directory / "failing.yaml", "failing-system", FAILING_COMMAND),
        write_system(systems_directory / "sleeping.yaml", "sleeping-system", SLEEPING_COMMAND),
    ]
    manual_path = systems_directory / "manual.yaml"
    manual_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "manual-system",
                "name": "Manual",
                "adapter": "manual_import",
            }
        ),
        encoding="utf-8",
    )
    suite = SuiteConfig(
        id="runner-test",
        name="Runner test suite",
        domain={"id": "minimal", "path": str(pack)},
        scenario_generation={"seed": 7, "count": 2},
        systems=[*system_paths, str(manual_path)],
        repetitions=repetitions,
        concurrency=8,
        run_timeout_seconds=timeout,
    )
    return suite, tmp_path


def test_run_set_preserves_failures_and_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWRB_TEST_SECRET", "topsecret-value-123")
    suite, suite_directory = build_suite(tmp_path, timeout=2)
    run_set = tmp_path / "runs" / "test-run"
    summary = asyncio.run(execute_run_set(suite, suite_directory, run_set))

    assert summary.scenario_count == 2
    # 3 runnable systems x 2 scenarios: ok completes, failing fails, sleeping times out.
    assert summary.completed == 2
    assert summary.failed == 2
    assert summary.timed_out == 2
    assert summary.manual_systems == ["manual-system"]

    scenario_files = sorted((run_set / "scenarios").glob("*.json"))
    assert len(scenario_files) == 2
    scenario_id = scenario_files[0].stem

    ok_result = json.loads(
        (run_set / scenario_id / "ok-system" / "t01" / "result.json").read_text("utf-8")
    )
    assert ok_result["status"] == "completed"
    assert ok_result["answer"].startswith("ok: ")
    failed_result = json.loads(
        (run_set / scenario_id / "failing-system" / "t01" / "result.json").read_text("utf-8")
    )
    assert failed_result["status"] == "failed"
    assert failed_result["warnings"]
    timeout_result = json.loads(
        (run_set / scenario_id / "sleeping-system" / "t01" / "result.json").read_text("utf-8")
    )
    assert timeout_result["status"] == "timeout"

    manifest = json.loads((run_set / "run-set.json").read_text("utf-8"))
    assert manifest["summary"]["completed"] == 2
    assert manifest["manual_systems"] == ["manual-system"]

    expected_files = {
        "config.json",
        "scenario.json",
        "answer.md",
        "citations.json",
        "metrics.json",
        "trace.jsonl",
        "result.json",
    }
    trial_directory = run_set / scenario_id / "ok-system" / "t01"
    assert {path.name for path in trial_directory.iterdir()} == expected_files


def test_no_secret_values_appear_in_artefacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "topsecret-value-123"
    monkeypatch.setenv("OWRB_TEST_SECRET", secret)
    suite, suite_directory = build_suite(tmp_path, timeout=2)
    run_set = tmp_path / "runs" / "secret-run"
    asyncio.run(execute_run_set(suite, suite_directory, run_set))
    for artefact in run_set.rglob("*"):
        if artefact.is_file():
            assert secret.encode() not in artefact.read_bytes(), artefact


def test_repetitions_create_separate_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWRB_TEST_SECRET", "unused")
    pack = write_minimal_pack(tmp_path)
    system_path = write_system(tmp_path / "ok.yaml", "ok-system", OK_COMMAND)
    suite = SuiteConfig(
        id="repeat-test",
        name="Repeat",
        domain={"id": "minimal", "path": str(pack)},
        scenario_generation={"seed": 3, "count": 1},
        systems=[system_path],
        repetitions=2,
        run_timeout_seconds=20,
    )
    run_set = tmp_path / "runs" / "repeat"
    summary = asyncio.run(execute_run_set(suite, tmp_path, run_set))
    assert summary.completed == 2
    scenario_id = next((run_set / "scenarios").glob("*.json")).stem
    trials = {path.name for path in (run_set / scenario_id / "ok-system").iterdir()}
    assert trials == {"t01", "t02"}


def test_template_quotas_control_distribution(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    system_path = write_system(tmp_path / "ok.yaml", "ok-system", OK_COMMAND)
    suite = SuiteConfig(
        id="quota-test",
        name="Quota",
        domain={"id": "minimal", "path": str(pack)},
        scenario_generation={"seed": 3, "count": 3, "template_quotas": {"pick-colour": 3}},
        systems=[system_path],
        run_timeout_seconds=20,
    )
    run_set = tmp_path / "runs" / "quota"
    summary = asyncio.run(execute_run_set(suite, tmp_path, run_set))
    assert summary.scenario_count == 3


def test_import_manual_result(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    system_path = write_system(tmp_path / "ok.yaml", "ok-system", OK_COMMAND)
    suite = SuiteConfig(
        id="manual-test",
        name="Manual",
        domain={"id": "minimal", "path": str(pack)},
        scenario_generation={"seed": 5, "count": 1},
        systems=[system_path],
        run_timeout_seconds=20,
    )
    run_set = tmp_path / "runs" / "manual"
    asyncio.run(execute_run_set(suite, tmp_path, run_set))
    scenario_id = next((run_set / "scenarios").glob("*.json")).stem

    answer_path = tmp_path / "answer.md"
    answer_path.write_text("A manually captured answer.", encoding="utf-8")
    citations_path = tmp_path / "citations.json"
    citations_path.write_text(json.dumps(["https://example.com/manual"]), encoding="utf-8")

    trial_directory = import_manual_result(
        run_set_directory=run_set,
        scenario_id=scenario_id,
        system_id="hosted-product",
        answer_path=answer_path,
        citations_path=citations_path,
        latency_ms=12345,
    )
    result = json.loads((trial_directory / "result.json").read_text("utf-8"))
    assert result["status"] == "manual"
    assert result["answer"] == "A manually captured answer."
    assert result["citations"][0]["url"] == "https://example.com/manual"
    assert result["metrics"]["latency_ms"] == 12345

    second = import_manual_result(
        run_set_directory=run_set,
        scenario_id=scenario_id,
        system_id="hosted-product",
        answer_path=answer_path,
    )
    assert second.name == "manual-02"

    with pytest.raises(SuiteError, match="not found"):
        import_manual_result(run_set, "missing-scenario", "x", answer_path)


def test_load_suite_follows_pointer_and_validates(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "pointer-test",
                "name": "Pointer",
                "domain": {"id": "minimal", "path": "unused"},
                "scenario_generation": {"seed": 1, "count": 1},
                "systems": ["unused.yaml"],
            }
        ),
        encoding="utf-8",
    )
    pointer_path = tmp_path / "benchmark.yaml"
    pointer_path.write_text("suite: suite.yaml\n", encoding="utf-8")
    assert load_suite(pointer_path).id == "pointer-test"

    with pytest.raises(SuiteError, match="not found"):
        load_suite(tmp_path / "missing.yaml")

    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text("id: 1\n", encoding="utf-8")
    with pytest.raises(SuiteError, match="invalid suite"):
        load_suite(bad_path)


def test_example_suite_file_is_valid() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    suite = load_suite(repository_root / "suites" / "australian-tourism-dev.example.yaml")
    assert suite.id == "australian-tourism-dev"
    assert suite.scenario_generation.count == 30
    pointer = load_suite(repository_root / "benchmark.example.yaml")
    assert pointer.id == suite.id
