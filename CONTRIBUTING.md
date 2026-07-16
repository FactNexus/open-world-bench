# Contributing

## Development setup

```bash
uv sync --all-extras
uv run ruff check .
uv run mypy src
uv run pytest
```

## Contributions

Contributions may add:

- domain packs;
- scenario templates;
- adapters;
- parameter providers;
- deterministic validators;
- evidence extractors;
- evaluation methods;
- reports and analysis tools.

## Contract changes

Any breaking change to a persisted YAML or JSON format requires:

- a schema-version increment;
- a migration note;
- updated examples and tests;
- backwards-compatibility handling where practical.

## Domain-pack review

A domain pack should be rejected if it requires its own ontology or proprietary index to determine benchmark truth. Such resources may be candidate systems, but not mandatory evaluation dependencies.
