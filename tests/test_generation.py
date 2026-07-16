from pathlib import Path

import pytest

from owrb.domain_loader import load_domain_pack, load_scenario_template
from owrb.generation import (
    GenerationError,
    canonical_prompt_hash,
    generate_batch,
    instance_to_canonical_json,
)
from owrb.models import DomainPack, ScenarioTemplate
from owrb.providers.builtin import BuiltinProviderFactory

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIRECTORY = REPOSITORY_ROOT / "domains" / "australian-tourism"


def load_pack() -> tuple[DomainPack, list[ScenarioTemplate]]:
    domain_pack = load_domain_pack(DOMAIN_DIRECTORY)
    templates = [
        load_scenario_template(path)
        for path in sorted((DOMAIN_DIRECTORY / "scenarios").glob("*.yaml"))
    ]
    return domain_pack, templates


def generate(seed: int, count: int) -> list[bytes]:
    domain_pack, templates = load_pack()
    factory = BuiltinProviderFactory(DOMAIN_DIRECTORY)
    instances, _ = generate_batch(domain_pack, templates, factory, seed, count)
    return [instance_to_canonical_json(instance) for instance in instances]


def strip_timestamp(serialised: bytes) -> bytes:
    return b"\n".join(
        line for line in serialised.splitlines() if b"generated_at" not in line
    )


def test_same_seed_reproduces_byte_equivalent_instances() -> None:
    first = generate(20260716, 9)
    second = generate(20260716, 9)
    assert [strip_timestamp(a) for a in first] == [strip_timestamp(b) for b in second]


def test_different_seeds_differ() -> None:
    first = generate(1, 6)
    second = generate(2, 6)
    assert [strip_timestamp(a) for a in first] != [strip_timestamp(b) for b in second]


def test_instances_have_no_unresolved_placeholders_or_duplicates() -> None:
    domain_pack, templates = load_pack()
    factory = BuiltinProviderFactory(DOMAIN_DIRECTORY)
    instances, report = generate_batch(domain_pack, templates, factory, 42, 100)
    assert report.generated == 100
    prompt_hashes = {canonical_prompt_hash(instance.prompt) for instance in instances}
    assert len(prompt_hashes) == 100
    for instance in instances:
        assert "{{" not in instance.prompt and "}}" not in instance.prompt
        assert instance.parameters
        assert instance.source_hashes


def test_compatibility_rules_are_enforced() -> None:
    domain_pack, templates = load_pack()
    ruled_templates = [template for template in templates if template.rules]
    assert ruled_templates, "domain pack should exercise at least one compatibility rule"
    factory = BuiltinProviderFactory(DOMAIN_DIRECTORY)
    instances, report = generate_batch(domain_pack, ruled_templates, factory, 7, 60)
    assert report.rule_rejections > 0, "expected rejection sampling to trigger at least once"
    # No surviving instance may violate its template's rules; re-checking them
    # against persisted parameters must pass by construction.
    from owrb.expressions import evaluate_expression

    templates_by_id = {template.id: template for template in ruled_templates}
    for instance in instances:
        for rule in templates_by_id[instance.template_id].rules:
            assert evaluate_expression(rule, instance.parameters), (
                f"{instance.id} violates rule {rule!r}"
            )


def test_duplicate_prompts_are_rejected() -> None:
    domain_pack, _ = load_pack()
    constant_template = ScenarioTemplate(
        id="constant",
        name="Constant prompt",
        version="0.0.1",
        prompt="Always the same prompt.",
        parameters={},
    )
    factory = BuiltinProviderFactory(DOMAIN_DIRECTORY)
    with pytest.raises(GenerationError, match="exhausted"):
        generate_batch(domain_pack, [constant_template], factory, 1, 2)


def test_generation_report_accounts_for_all_instances() -> None:
    domain_pack, templates = load_pack()
    factory = BuiltinProviderFactory(DOMAIN_DIRECTORY)
    _, report = generate_batch(domain_pack, templates, factory, 99, 12)
    assert report.requested == 12
    assert report.generated == 12
    assert sum(report.per_template.values()) == 12
    assert set(report.per_template) == {template.id for template in templates}
