# Open-World Research Benchmark

> Working title: **OWRB**

A domain-parameterised benchmark for comparing research agents that must discover, assess, and synthesise information from the live web or another open knowledge environment.

The first included domain pack is **Australian tourism**. It is designed to compare systems such as:

- a curated tourism index with ontology-aware retrieval;
- the same index without the ontology;
- Gemini or Claude with provider-native web search;
- general web-research agents;
- MCP-based or custom retrieval agents.

Unlike closed-corpus benchmarks, OWRB does not give every system the same document collection. It freezes the **scenario**, not the **knowledge source**. Each system finds its own evidence, and the evaluator scores the quality of the answer and its supporting evidence.

## Status

This repository is an implementation specification and starter scaffold. It is intended to be handed to a coding agent or development team.

The normative requirements are in [SPEC.md](SPEC.md). Supporting design notes are in [`docs/`](docs/).

## Core workflow

```text
Domain pack + random seed
          |
          v
Generated scenario instance
          |
          v
Run the same instance against each system
          |
          v
Answer + citations + trace + metrics
          |
          v
Evidence retrieval and mixed evaluation
          |
          v
Quality report + efficiency report + paired comparison
```

## Proposed command-line interface

```bash
uv sync

owrb domain validate domains/australian-tourism
owrb scenarios generate --domain australian-tourism --count 30 --seed 20260716
owrb run --suite suites/australian-tourism-dev.yaml
owrb evaluate --run-set runs/2026-07-16-australian-tourism
owrb compare --run-set runs/2026-07-16-australian-tourism
owrb report --run-set runs/2026-07-16-australian-tourism
```

Only domain validation is expected to work in the initial scaffold. The remaining commands are implementation targets.

## Minimal domain pack

A domain pack needs only:

1. `domain.yaml`
2. one or more parameter files, such as locations or interests;
3. one or more scenario templates.

It does **not** require an ontology, a gold answer, a custom crawler, or custom Python code.

## Repository map

```text
src/owrb/                   benchmark framework
schemas/                    generated/public JSON schemas
domains/                    domain packs
systems/                    example system adapter configurations
suites/                     benchmark run configurations
docs/                       architecture and implementation guidance
examples/                   generated scenario and run examples
tests/                      contract and schema tests
```

## Design lineage

The filesystem-first layout and task/evaluation/report separation are influenced by Harvey LAB. The open-world and dynamic-task aspects also draw on work such as WebArena, BrowseComp, GAIA, DeepResearch Bench, and AndroidWorld. See [docs/prior-art.md](docs/prior-art.md).

## Licence

MIT for the framework and example content. Any included or generated third-party data remains subject to its source licence. In particular, an OSM-derived location dataset must include OpenStreetMap attribution and comply with the ODbL.
