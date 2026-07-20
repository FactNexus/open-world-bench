"""The committed 100-instance public development suite (SPEC.md Milestone 5)."""

import json
from collections import Counter
from pathlib import Path

from owrb.generation import canonical_prompt_hash, generate_batch, instance_to_canonical_json
from owrb.models import ScenarioInstance
from owrb.providers.builtin import BuiltinProviderFactory
from owrb.runner import load_suite
from owrb.validation import validate_domain_pack

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = REPOSITORY_ROOT / "suites" / "australian-tourism-dev.yaml"
DEV_SUITE_DIRECTORY = REPOSITORY_ROOT / "examples" / "dev-suite"


def load_committed_instances() -> list[ScenarioInstance]:
    paths = sorted(DEV_SUITE_DIRECTORY.glob("australian-tourism.*.json"))
    return [
        ScenarioInstance.model_validate(json.loads(path.read_text("utf-8"))) for path in paths
    ]


def test_suite_config_quotas_sum_to_count() -> None:
    suite = load_suite(SUITE_PATH)
    assert suite.scenario_generation.count == 100
    assert sum(suite.scenario_generation.template_quotas.values()) == 100


def test_committed_instances_are_valid_unique_and_quota_matched() -> None:
    suite = load_suite(SUITE_PATH)
    instances = load_committed_instances()
    assert len(instances) == 100
    prompt_hashes = {canonical_prompt_hash(instance.prompt) for instance in instances}
    assert len(prompt_hashes) == 100, "committed instances must have unique prompts"
    per_template = Counter(instance.template_id for instance in instances)
    assert per_template == Counter(suite.scenario_generation.template_quotas)
    for instance in instances:
        assert "{{" not in instance.prompt
        assert instance.domain_version == "0.3.0"


def test_committed_instances_respect_template_rules() -> None:
    for instance in load_committed_instances():
        parameters = instance.parameters
        if instance.template_id == "remote-area-plan":
            assert parameters["location"]["remoteness"] == "remote", instance.id
        if instance.template_id == "season-adaptation":
            assert parameters["season"] in parameters["condition"]["seasons"], instance.id
        if instance.template_id == "compare-destinations":
            assert parameters["location_a"]["id"] != parameters["location_b"]["id"], instance.id
        if instance.template_id == "multi-stop-itinerary":
            start, end = parameters["location_a"], parameters["location_b"]
            assert start["id"] != end["id"], instance.id
            assert (start["state_or_territory"] == "TAS") == (
                end["state_or_territory"] == "TAS"
            ), instance.id
            squared_km = ((start["latitude"] - end["latitude"]) * 111) ** 2 + (
                (start["longitude"] - end["longitude"]) * 88
            ) ** 2
            assert 10_000 <= squared_km <= (parameters["trip_days"] * 200) ** 2, instance.id
            if parameters["interest"]["id"] == "coast":
                assert start["coastal"] == 1 or end["coastal"] == 1, instance.id
        if instance.template_id == "everyday-essentials" and (
            parameters["need"].get("requires_children") == 1
        ):
            assert parameters["traveller"]["children"] > 0, instance.id
        interest = parameters.get("interest")
        coast_elsewhere = (
            interest
            and interest["id"] == "coast"
            and instance.template_id != "multi-stop-itinerary"
        )
        if coast_elsewhere:
            for key in ("location", "location_a", "location_b"):
                if key in parameters:
                    assert parameters[key]["coastal"] == 1, instance.id


def test_committed_instances_regenerate_byte_identically() -> None:
    """The public suite must reproduce from its seed (SPEC.md 10.4).

    If this fails after an intentional domain-pack or generator change,
    regenerate with:
    owrb scenarios generate --suite suites/australian-tourism-dev.yaml \
        --output examples/dev-suite
    and review/commit the result together with a domain version bump.
    """
    suite = load_suite(SUITE_PATH)
    domain_directory = REPOSITORY_ROOT / suite.domain.path
    validation = validate_domain_pack(domain_directory)
    assert validation.valid and validation.domain_pack is not None
    factory = BuiltinProviderFactory(domain_directory)
    instances, _report = generate_batch(
        domain_pack=validation.domain_pack,
        templates=validation.templates,
        provider_factory=factory,
        suite_seed=suite.scenario_generation.seed,
        count=suite.scenario_generation.count,
        template_quotas=suite.scenario_generation.template_quotas,
    )

    def stripped(serialised: bytes) -> bytes:
        return b"\n".join(
            line for line in serialised.splitlines() if b"generated_at" not in line
        )

    committed = {
        path.stem: stripped(path.read_bytes())
        for path in DEV_SUITE_DIRECTORY.glob("australian-tourism.*.json")
    }
    assert len(committed) == len(instances)
    for instance in instances:
        assert instance.id in committed, f"missing committed instance {instance.id}"
        assert stripped(instance_to_canonical_json(instance)) == committed[instance.id], (
            f"{instance.id} does not match the committed dev suite"
        )
