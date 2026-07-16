import json
from pathlib import Path

from typer.testing import CliRunner

from owrb.cli import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_domain_validate_json_output() -> None:
    result = runner.invoke(
        app,
        ["domain", "validate", str(REPOSITORY_ROOT / "domains" / "australian-tourism"), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["id"] == "australian-tourism"
    assert len(payload["templates"]) == 3


def test_domain_validate_fails_with_nonzero_exit(tmp_path: Path) -> None:
    result = runner.invoke(app, ["domain", "validate", str(tmp_path)])
    assert result.exit_code == 1


def test_generate_and_inspect_round_trip(tmp_path: Path) -> None:
    output_directory = tmp_path / "instances"
    result = runner.invoke(
        app,
        [
            "scenarios",
            "generate",
            "--domain",
            str(REPOSITORY_ROOT / "domains" / "australian-tourism"),
            "--count",
            "3",
            "--seed",
            "20260716",
            "--output",
            str(output_directory),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report"]["generated"] == 3
    assert (output_directory / "generation-report.json").is_file()

    instance_id = payload["instances"][0]
    inspect_result = runner.invoke(
        app,
        ["scenarios", "inspect", instance_id, "--directory", str(output_directory), "--json"],
    )
    assert inspect_result.exit_code == 0, inspect_result.output
    instance = json.loads(inspect_result.output)
    assert instance["id"] == instance_id
    assert instance["prompt"]


def test_systems_validate() -> None:
    result = runner.invoke(
        app,
        ["systems", "validate", str(REPOSITORY_ROOT / "systems" / "generic-http.example.yaml")],
    )
    assert result.exit_code == 0, result.output


def test_unimplemented_commands_exit_2() -> None:
    for command in ("run", "evaluate", "compare"):
        result = runner.invoke(app, [command])
        assert result.exit_code == 2
