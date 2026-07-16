import json
from pathlib import Path

from owrb.schema_export import SCHEMA_EXPORTS, check_schemas

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIRECTORY = REPOSITORY_ROOT / "schemas"


def test_checked_in_schemas_match_pydantic_models() -> None:
    stale = check_schemas(SCHEMAS_DIRECTORY)
    assert not stale, f"stale schemas (run 'owrb schemas generate'): {stale}"


def test_all_schema_files_are_exported() -> None:
    checked_in = {path.name for path in SCHEMAS_DIRECTORY.glob("*.schema.json")}
    assert checked_in == set(SCHEMA_EXPORTS)


def test_schemas_forbid_additional_properties() -> None:
    for file_name in SCHEMA_EXPORTS:
        schema = json.loads((SCHEMAS_DIRECTORY / file_name).read_text(encoding="utf-8"))
        assert schema.get("additionalProperties") is False, file_name
