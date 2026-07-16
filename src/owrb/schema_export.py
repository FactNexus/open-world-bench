"""Generate the public JSON Schemas in ``schemas/`` from the Pydantic contracts.

Pydantic models are the canonical runtime contract (SPEC.md section 20); the
checked-in JSON Schemas are derived artefacts. ``owrb schemas generate --check``
fails when they drift, and CI runs that check.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from owrb.models import (
    DomainPack,
    EvaluationResult,
    RunResult,
    ScenarioInstance,
    ScenarioTemplate,
    SystemDefinition,
)

SCHEMA_EXPORTS: dict[str, type[BaseModel]] = {
    "domain-pack.schema.json": DomainPack,
    "scenario-template.schema.json": ScenarioTemplate,
    "scenario-instance.schema.json": ScenarioInstance,
    "system.schema.json": SystemDefinition,
    "run-result.schema.json": RunResult,
    "evaluation-result.schema.json": EvaluationResult,
}


def render_schema(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write_schemas(output_directory: Path) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name, model in SCHEMA_EXPORTS.items():
        path = output_directory / file_name
        path.write_text(render_schema(model), encoding="utf-8")
        written.append(path)
    return written


def check_schemas(output_directory: Path) -> list[str]:
    """Return the schema files that are missing or stale."""
    stale: list[str] = []
    for file_name, model in SCHEMA_EXPORTS.items():
        path = output_directory / file_name
        if not path.is_file() or path.read_text(encoding="utf-8") != render_schema(model):
            stale.append(file_name)
    return stale
