import json
from pathlib import Path

from owrb.models import ScenarioInstance

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_example_scenario_instance_is_valid() -> None:
    example_path = REPOSITORY_ROOT / "examples" / "generated" / "scenario-instance.example.json"
    scenario_data = json.loads(example_path.read_text(encoding="utf-8"))
    scenario_instance = ScenarioInstance.model_validate(scenario_data)
    assert scenario_instance.domain_id == "australian-tourism"
