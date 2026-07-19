from pathlib import Path

from owrb.validation import validate_domain_pack, validate_system_definition

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIRECTORY = REPOSITORY_ROOT / "domains" / "australian-tourism"
SYSTEMS_DIRECTORY = REPOSITORY_ROOT / "systems"

MINIMAL_MANIFEST = """
schema_version: 1
id: minimal
name: Minimal
description: Minimal test pack
version: 0.0.1
templates:
  - scenarios/*.yaml
parameters:
  colour:
    provider:
      type: values
      values: [red, blue]
"""

MINIMAL_TEMPLATE = """
schema_version: 1
id: pick-colour
name: Pick a colour
version: 0.0.1
prompt: "Describe the colour {{ colour }}."
parameters:
  colour:
    source: colour
"""


def write_minimal_pack(root: Path) -> Path:
    pack = root / "minimal"
    (pack / "scenarios").mkdir(parents=True)
    (pack / "domain.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (pack / "scenarios" / "pick-colour.yaml").write_text(MINIMAL_TEMPLATE, encoding="utf-8")
    return pack


def errors(result_issues: list) -> list[str]:  # type: ignore[type-arg]
    return [issue.message for issue in result_issues if issue.severity == "error"]


def test_australian_tourism_pack_is_fully_valid() -> None:
    result = validate_domain_pack(DOMAIN_DIRECTORY)
    assert result.valid, [issue.as_dict() for issue in result.issues]
    assert len(result.templates) == 6


def test_minimal_pack_is_valid(tmp_path: Path) -> None:
    result = validate_domain_pack(write_minimal_pack(tmp_path))
    assert result.valid, [issue.as_dict() for issue in result.issues]


def test_missing_manifest_is_an_error(tmp_path: Path) -> None:
    result = validate_domain_pack(tmp_path)
    assert not result.valid
    assert any("domain.yaml not found" in message for message in errors(result.issues))


def test_unmatched_template_glob_is_an_error(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    (pack / "scenarios" / "pick-colour.yaml").unlink()
    result = validate_domain_pack(pack)
    assert any("matches no files" in message for message in errors(result.issues))


def test_unknown_parameter_source_is_an_error(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    template_path = pack / "scenarios" / "pick-colour.yaml"
    template_path.write_text(
        MINIMAL_TEMPLATE.replace("source: colour", "source: no-such-parameter"),
        encoding="utf-8",
    )
    result = validate_domain_pack(pack)
    assert any("unknown domain parameter" in message for message in errors(result.issues))


def test_undeclared_prompt_variable_is_an_error(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    template_path = pack / "scenarios" / "pick-colour.yaml"
    template_path.write_text(
        MINIMAL_TEMPLATE.replace("{{ colour }}", "{{ colour }} in {{ location }}"),
        encoding="utf-8",
    )
    result = validate_domain_pack(pack)
    assert any("undeclared parameters: location" in message for message in errors(result.issues))


def test_unsafe_rule_is_an_error(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    template_path = pack / "scenarios" / "pick-colour.yaml"
    template_path.write_text(
        MINIMAL_TEMPLATE + "rules:\n  - \"__import__('os').system('true')\"\n",
        encoding="utf-8",
    )
    result = validate_domain_pack(pack)
    assert any("rule" in message for message in errors(result.issues))


def test_missing_provider_file_is_an_error(tmp_path: Path) -> None:
    pack = write_minimal_pack(tmp_path)
    manifest = MINIMAL_MANIFEST.replace(
        "provider:\n      type: values\n      values: [red, blue]",
        "provider:\n      type: csv\n      path: values/missing.csv",
    )
    (pack / "domain.yaml").write_text(manifest, encoding="utf-8")
    result = validate_domain_pack(pack)
    assert any("provider file not found" in message for message in errors(result.issues))


def test_example_system_definitions_are_valid() -> None:
    for system_path in sorted(SYSTEMS_DIRECTORY.glob("*.yaml")):
        result = validate_system_definition(system_path)
        assert result.valid, (system_path, [issue.as_dict() for issue in result.issues])


def test_unknown_adapter_is_a_warning_not_an_error(tmp_path: Path) -> None:
    system_path = tmp_path / "system.yaml"
    system_path.write_text(
        "schema_version: 1\nid: exotic\nname: Exotic\nadapter: exotic_adapter\n",
        encoding="utf-8",
    )
    result = validate_system_definition(system_path)
    assert result.valid
    assert any(issue.severity == "warning" for issue in result.issues)


def test_embedded_secret_looking_environment_value_is_flagged(tmp_path: Path) -> None:
    system_path = tmp_path / "system.yaml"
    system_path.write_text(
        "schema_version: 1\nid: leaky\nname: Leaky\nadapter: generic_http\n"
        "environment:\n  api_key: sk-live-abc123\n",
        encoding="utf-8",
    )
    result = validate_system_definition(system_path)
    assert any(issue.severity == "warning" for issue in result.issues)
