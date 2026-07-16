# Domain packs

## Minimum authoring burden

A domain pack should be possible to create without Python.

```text
domains/example/
├── domain.yaml
├── values/items.csv
└── scenarios/example.yaml
```

The author provides a description, parameter sources, and prompt templates. Generic evaluation supplies citation, factuality, completeness, and clarity criteria.

## `domain.yaml`

```yaml
schema_version: 1
id: example
name: Example domain
description: Demonstration domain
version: 0.1.0
default_locale: en-AU
default_timezone: Australia/Sydney
templates:
  - scenarios/*.yaml
parameters:
  location:
    provider:
      type: csv
      path: values/locations.csv
```

## Scenario template

```yaml
schema_version: 1
id: nearby-discovery
name: Nearby discovery
version: 0.1.0
prompt: |
  Recommend {{ recommendation_count }} experiences near {{ location.name }}
  for {{ traveller.description }} who is interested in {{ interest.label }}.
parameters:
  location:
    source: location
  traveller:
    source: traveller
  interest:
    source: interest
  recommendation_count:
    provider:
      type: values
      values: [3, 4, 5]
criteria:
  - id: meets-interest
    dimension: coverage
    title: Recommendations match the stated interest
    description: Each recommendation should have a clear connection to the interest.
```

## Provider snapshots

Dynamic public data should be imported into a local, versioned snapshot before generation. The scenario instance stores the selected row, so replay never depends on the original source.

For OSM-derived data, include attribution and source/licence metadata. Keep OSM data used to create prompts distinct from web evidence used to grade answers.

## Optional custom code

Custom providers and validators live inside the domain pack and are loaded only when explicitly enabled. A pack must declare plugin names in its manifest; arbitrary import-by-path is not permitted by default.
