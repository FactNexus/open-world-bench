"""Shared test helpers."""

from datetime import UTC, datetime
from pathlib import Path

from owrb.models import AnswerContract, ScenarioInstance, SystemDefinition

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
      values: [red, blue, green, amber, violet, teal]
"""

MINIMAL_TEMPLATE = """
schema_version: 1
id: pick-colour
name: Pick a colour
version: 0.0.1
prompt: "Describe the colour {{ colour }} (variant {{ variant }})."
parameters:
  colour:
    source: colour
  variant:
    provider:
      type: range
      options: {min: 1, max: 1000}
"""


def write_minimal_pack(root: Path) -> Path:
    pack = root / "minimal"
    (pack / "scenarios").mkdir(parents=True)
    (pack / "domain.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (pack / "scenarios" / "pick-colour.yaml").write_text(MINIMAL_TEMPLATE, encoding="utf-8")
    return pack


def make_scenario(instance_id: str = "minimal.pick-colour.000001") -> ScenarioInstance:
    return ScenarioInstance(
        id=instance_id,
        domain_id="minimal",
        domain_version="0.0.1",
        template_id="pick-colour",
        template_version="0.0.1",
        seed=1,
        generated_at=datetime(2026, 7, 16, tzinfo=UTC),
        parameters={"colour": "red"},
        prompt="Describe the colour red.",
        answer_contract=AnswerContract(),
        criteria=[],
    )


def make_system(**overrides: object) -> SystemDefinition:
    payload: dict[str, object] = {
        "id": "test-system",
        "name": "Test system",
        "adapter": "command",
        "settings": {},
    }
    payload.update(overrides)
    return SystemDefinition.model_validate(payload)
