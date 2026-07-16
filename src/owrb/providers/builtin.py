"""Built-in no-code parameter providers (SPEC.md section 10.2).

Every provider selects one value using only the deterministic random generator
it is handed, so regenerating with the same seed and source files reproduces
the same scenario instance. File-backed providers record a SHA-256 hash of
each file they load; those hashes are persisted on generated instances so a
change in a parameter dataset is detectable after the fact.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from random import Random
from typing import Any

import yaml

from owrb.expressions import evaluate_expression
from owrb.generation import ParameterProvider
from owrb.models import ProviderSpec


class ProviderConfigurationError(ValueError):
    """Raised when a provider specification is invalid or its data cannot load."""


def _coerce_csv_value(raw: str) -> Any:
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _weight(item: Any, weight_field: str | None) -> float:
    if weight_field is None or not isinstance(item, dict):
        return 1.0
    raw = item.get(weight_field, 1.0)
    try:
        weight = float(raw)
    except (TypeError, ValueError) as error:
        raise ProviderConfigurationError(
            f"weight field {weight_field!r} has non-numeric value {raw!r}"
        ) from error
    if weight < 0:
        raise ProviderConfigurationError(f"weight field {weight_field!r} is negative: {weight}")
    return weight


@dataclass(frozen=True)
class WeightedChoiceProvider:
    """Select one item from a fixed list, optionally weighted by a field."""

    items: tuple[Any, ...]
    weights: tuple[float, ...]

    def select(self, random_generator: Random, context: dict[str, Any]) -> Any:
        del context
        return random_generator.choices(self.items, weights=self.weights, k=1)[0]


@dataclass(frozen=True)
class RangeProvider:
    """Sample an integer or decimal from an inclusive range."""

    minimum: float
    maximum: float
    step: float | None
    integer: bool
    precision: int

    def select(self, random_generator: Random, context: dict[str, Any]) -> Any:
        del context
        if self.integer:
            step = int(self.step) if self.step else 1
            offsets = int((int(self.maximum) - int(self.minimum)) // step)
            return int(self.minimum) + step * random_generator.randint(0, offsets)
        value = random_generator.uniform(self.minimum, self.maximum)
        return round(value, self.precision)


@dataclass(frozen=True)
class DateWindowProvider:
    """Sample an ISO date from a window, or a named season from a list."""

    start: date | None
    end: date | None
    seasons: tuple[str, ...]

    def select(self, random_generator: Random, context: dict[str, Any]) -> Any:
        del context
        if self.seasons:
            return random_generator.choice(list(self.seasons))
        assert self.start is not None and self.end is not None
        day_span = (self.end - self.start).days
        selected = self.start + timedelta(days=random_generator.randint(0, day_span))
        return selected.isoformat()


@dataclass(frozen=True)
class DerivedProvider:
    """Calculate a value from previously selected parameters via a safe expression."""

    expression: str

    def select(self, random_generator: Random, context: dict[str, Any]) -> Any:
        del random_generator
        return evaluate_expression(self.expression, context)


class BuiltinProviderFactory:
    """Create providers from :class:`ProviderSpec` entries in a domain pack.

    File hashes for every loaded parameter dataset accumulate on
    :attr:`file_hashes`, keyed by path relative to the pack directory.
    """

    def __init__(self, base_directory: Path) -> None:
        self._base_directory = base_directory
        self.file_hashes: dict[str, str] = {}
        self._provider_cache: dict[str, ParameterProvider] = {}

    def create(self, provider_spec: ProviderSpec) -> ParameterProvider:
        cache_key = repr(provider_spec)
        cached = self._provider_cache.get(cache_key)
        if cached is not None:
            return cached
        provider = self._build(provider_spec)
        self._provider_cache[cache_key] = provider
        return provider

    def _build(self, spec: ProviderSpec) -> ParameterProvider:
        if spec.type == "values":
            if not spec.values:
                raise ProviderConfigurationError("values provider requires a non-empty list")
            return WeightedChoiceProvider(tuple(spec.values), tuple([1.0] * len(spec.values)))
        if spec.type == "csv":
            return self._build_csv(spec)
        if spec.type == "yaml_list":
            return self._build_yaml_list(spec)
        if spec.type == "range":
            return self._build_range(spec)
        if spec.type == "date_window":
            return self._build_date_window(spec)
        if spec.type == "derived":
            expression = spec.options.get("expression")
            if not isinstance(expression, str):
                raise ProviderConfigurationError(
                    "derived provider requires an 'expression' option"
                )
            return DerivedProvider(expression)
        raise ProviderConfigurationError(f"unknown provider type {spec.type!r}")

    def _read_source_file(self, spec: ProviderSpec) -> tuple[Path, str]:
        if not spec.path:
            raise ProviderConfigurationError(f"{spec.type} provider requires a 'path'")
        relative = Path(spec.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ProviderConfigurationError(
                f"provider path {spec.path!r} must stay inside the domain pack"
            )
        path = self._base_directory / relative
        if not path.is_file():
            raise ProviderConfigurationError(f"provider file not found: {path}")
        content = path.read_bytes()
        self.file_hashes[spec.path] = hashlib.sha256(content).hexdigest()
        return path, content.decode("utf-8")

    def _build_csv(self, spec: ProviderSpec) -> ParameterProvider:
        path, content = self._read_source_file(spec)
        weight_field = spec.options.get("weight_field")
        rows: list[dict[str, Any]] = []
        for raw_row in csv.DictReader(content.splitlines()):
            rows.append({key: _coerce_csv_value(value) for key, value in raw_row.items()})
        if not rows:
            raise ProviderConfigurationError(f"CSV file has no data rows: {path}")
        weights = tuple(_weight(row, weight_field) for row in rows)
        return WeightedChoiceProvider(tuple(rows), weights)

    def _build_yaml_list(self, spec: ProviderSpec) -> ParameterProvider:
        path, content = self._read_source_file(spec)
        items = yaml.safe_load(content)
        if not isinstance(items, list) or not items:
            raise ProviderConfigurationError(f"YAML file must contain a non-empty list: {path}")
        weight_field = spec.options.get("weight_field")
        weights = tuple(_weight(item, weight_field) for item in items)
        return WeightedChoiceProvider(tuple(items), weights)

    def _build_range(self, spec: ProviderSpec) -> ParameterProvider:
        options = spec.options
        try:
            minimum = float(options["min"])
            maximum = float(options["max"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderConfigurationError(
                "range provider requires numeric 'min' and 'max' options"
            ) from error
        if maximum < minimum:
            raise ProviderConfigurationError("range provider 'max' must be >= 'min'")
        step = options.get("step")
        integer = options.get("type", "int") == "int"
        precision = int(options.get("precision", 2))
        return RangeProvider(
            minimum=minimum,
            maximum=maximum,
            step=float(step) if step is not None else None,
            integer=integer,
            precision=precision,
        )

    def _build_date_window(self, spec: ProviderSpec) -> ParameterProvider:
        options = spec.options
        seasons = options.get("seasons")
        if seasons is not None:
            if not isinstance(seasons, list) or not all(
                isinstance(season, str) for season in seasons
            ):
                raise ProviderConfigurationError("'seasons' must be a list of strings")
            return DateWindowProvider(start=None, end=None, seasons=tuple(seasons))
        try:
            start = date.fromisoformat(str(options["start"]))
            end = date.fromisoformat(str(options["end"]))
        except (KeyError, ValueError) as error:
            raise ProviderConfigurationError(
                "date_window provider requires ISO 'start' and 'end' options or 'seasons'"
            ) from error
        if end < start:
            raise ProviderConfigurationError("date_window 'end' must be >= 'start'")
        return DateWindowProvider(start=start, end=end, seasons=())
