from collections import Counter
from pathlib import Path
from random import Random

import pytest

from owrb.models import ProviderSpec
from owrb.providers.builtin import BuiltinProviderFactory, ProviderConfigurationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIRECTORY = REPOSITORY_ROOT / "domains" / "australian-tourism"


def make_factory(base: Path | None = None) -> BuiltinProviderFactory:
    return BuiltinProviderFactory(base or DOMAIN_DIRECTORY)


def test_values_provider_is_seed_deterministic() -> None:
    provider = make_factory().create(ProviderSpec(type="values", values=[3, 4, 5]))
    first = [provider.select(Random(seed), {}) for seed in range(20)]
    second = [provider.select(Random(seed), {}) for seed in range(20)]
    assert first == second
    assert set(first) <= {3, 4, 5}


def test_csv_provider_coerces_types_and_records_hash() -> None:
    factory = make_factory()
    provider = factory.create(
        ProviderSpec(
            type="csv",
            path="values/locations.csv",
            options={"id_field": "id", "weight_field": "sampling_weight"},
        )
    )
    row = provider.select(Random(1), {})
    assert isinstance(row["name"], str)
    assert isinstance(row["latitude"], float)
    assert isinstance(row["sampling_weight"], int)
    assert "values/locations.csv" in factory.file_hashes
    assert len(factory.file_hashes["values/locations.csv"]) == 64


def test_yaml_list_provider_returns_items() -> None:
    provider = make_factory().create(
        ProviderSpec(type="yaml_list", path="values/travellers.yaml")
    )
    item = provider.select(Random(7), {})
    assert "id" in item and "description" in item


def test_weighted_selection_prefers_heavier_items(tmp_path: Path) -> None:
    csv_path = tmp_path / "weighted.csv"
    csv_path.write_text("id,weight\nrare,1\ncommon,99\n", encoding="utf-8")
    provider = make_factory(tmp_path).create(
        ProviderSpec(type="csv", path="weighted.csv", options={"weight_field": "weight"})
    )
    counts = Counter(provider.select(Random(seed), {})["id"] for seed in range(300))
    assert counts["common"] > counts["rare"] * 5


def test_range_provider_integer_and_float() -> None:
    factory = make_factory()
    integer_provider = factory.create(
        ProviderSpec(type="range", options={"min": 2, "max": 10, "step": 2})
    )
    values = {integer_provider.select(Random(seed), {}) for seed in range(50)}
    assert values <= {2, 4, 6, 8, 10}

    float_provider = factory.create(
        ProviderSpec(type="range", options={"min": 0.5, "max": 1.5, "type": "float"})
    )
    value = float_provider.select(Random(3), {})
    assert 0.5 <= value <= 1.5


def test_date_window_provider_dates_and_seasons() -> None:
    factory = make_factory()
    date_provider = factory.create(
        ProviderSpec(type="date_window", options={"start": "2026-01-01", "end": "2026-03-31"})
    )
    selected = date_provider.select(Random(5), {})
    assert selected.startswith("2026-0")

    season_provider = factory.create(
        ProviderSpec(type="date_window", options={"seasons": ["summer", "winter"]})
    )
    assert season_provider.select(Random(5), {}) in {"summer", "winter"}


def test_derived_provider_uses_context() -> None:
    provider = make_factory().create(
        ProviderSpec(type="derived", options={"expression": "max_distance_km // 2"})
    )
    assert provider.select(Random(0), {"max_distance_km": 50}) == 25


@pytest.mark.parametrize(
    "spec",
    [
        ProviderSpec(type="unknown"),
        ProviderSpec(type="values", values=[]),
        ProviderSpec(type="csv"),
        ProviderSpec(type="csv", path="../outside.csv"),
        ProviderSpec(type="csv", path="/etc/passwd"),
        ProviderSpec(type="range", options={"min": 5, "max": 1}),
        ProviderSpec(type="date_window", options={"start": "2026-01-01"}),
        ProviderSpec(type="derived", options={}),
    ],
)
def test_invalid_specifications_are_rejected(spec: ProviderSpec) -> None:
    with pytest.raises(ProviderConfigurationError):
        make_factory().create(spec)
