from __future__ import annotations

from pathlib import Path

import yaml

from owrb.models import DomainPack, ScenarioTemplate


def load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as input_file:
        return yaml.safe_load(input_file)


def load_domain_pack(domain_directory: Path) -> DomainPack:
    manifest_path = domain_directory / "domain.yaml"
    return DomainPack.model_validate(load_yaml(manifest_path))


def load_scenario_template(template_path: Path) -> ScenarioTemplate:
    return ScenarioTemplate.model_validate(load_yaml(template_path))
