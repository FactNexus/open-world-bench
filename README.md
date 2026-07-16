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

The normative requirements are in [SPEC.md](SPEC.md). Supporting design notes are in [`docs/`](docs/).

Implemented so far (Milestones 0 and 1 of SPEC.md §26):

- deep domain-pack validation (manifest, templates, providers, prompts, rules);
- system-definition validation;
- JSON Schemas generated from the Pydantic contracts (`owrb schemas generate --check` in CI);
- seeded, reproducible scenario generation with the no-code providers
  (`csv`, `yaml_list`, `values`, `range`, `date_window`, `derived`),
  safe compatibility rules, rejection sampling, and duplicate detection;
- the async run harness: suite execution with per-instance randomised system order,
  concurrency limits, per-trial timeouts, and preserved failures/timeouts;
- adapters: `generic_http`, `command`, `manual_import` (via `owrb import`), and
  provider adapters for Anthropic (Messages API) and OpenAI (Responses API) with
  native web search and normalised citations/metrics/trace;
- evidence and evaluation: SSRF-guarded, cached, polite evidence retrieval; a shared
  per-scenario evidence bundle; deterministic checks; claim decomposition and
  citation-support verdicts; blind rubric judging; dimension weighting with
  hard-constraint capping; per-trial `evaluation.json` artefacts.

Comparison and reporting are the next implementation targets (Milestone 4).

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

## Command-line interface

Working today:

```bash
uv sync --all-extras

owrb domain validate domains/australian-tourism   # deep validation, --json supported
owrb domain list                                  # list packs under domains/
owrb scenarios generate --domain australian-tourism --count 30 --seed 20260716
owrb scenarios inspect <instance-id>              # show a generated instance
owrb systems validate systems/generic-http.example.yaml
owrb schemas generate [--check]                   # regenerate/verify public JSON Schemas
owrb run --suite suites/australian-tourism-dev.example.yaml
owrb import --run-set runs/<id> --scenario <instance-id> --system <id> --answer answer.md
owrb evaluate --run-set runs/<id> [--judge-adapter anthropic --judge-model <model>]
owrb evidence refresh --run-set runs/<id>
```

Generated instances are written to `runs/scenarios/<domain>-seed<seed>/` (override with
`--output`), together with a `generation-report.json` covering rule and duplicate
rejections. Re-running with the same seed and pack version reproduces byte-identical
instances apart from the generation timestamp.

`owrb run` writes per-trial artefacts (`config.json`, `scenario.json`, `answer.md`,
`citations.json`, `metrics.json`, `trace.jsonl`, `result.json`) under
`runs/<run-set-id>/<instance-id>/<system-id>/<trial-id>/`. Secrets are referenced by
environment-variable name and never written to artefacts.

`owrb evaluate` builds a shared evidence bundle per scenario (the union of every
candidate's cited URLs, fetched once with SSRF protection, caching, and rate
limits), runs deterministic checks, and — when a judge is configured via the suite's
`evaluation.judge` or the `--judge-*` flags — decomposes claims, judges citation
support against the retrieved evidence, and scores template criteria blind to the
candidate's identity. Without a judge it still produces deterministic scores and
marks results as requiring review.

Implementation targets (exit with code 2 for now):

```bash
owrb compare --run-set runs/2026-07-16-australian-tourism
owrb report --run-set runs/2026-07-16-australian-tourism
```

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
