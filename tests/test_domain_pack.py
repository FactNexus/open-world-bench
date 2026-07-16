from pathlib import Path

from owrb.domain_loader import load_domain_pack, load_scenario_template

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIRECTORY = REPOSITORY_ROOT / "domains" / "australian-tourism"


def test_australian_tourism_domain_manifest_is_valid() -> None:
    domain_pack = load_domain_pack(DOMAIN_DIRECTORY)
    assert domain_pack.id == "australian-tourism"
    assert domain_pack.templates


def test_australian_tourism_templates_are_valid() -> None:
    template_paths = sorted((DOMAIN_DIRECTORY / "scenarios").glob("*.yaml"))
    templates = [load_scenario_template(template_path) for template_path in template_paths]
    assert {template.id for template in templates} == {
        "nearby-discovery",
        "constrained-day-plan",
        "compare-destinations",
    }
