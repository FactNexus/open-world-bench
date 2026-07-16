# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**OWRB (Open-World Research Benchmark)** — a domain-parameterised benchmark for comparing research agents (web-search LLMs, RAG systems, curated indexes, MCP agents) that must find their own evidence on the live web. It freezes the **scenario**, not the knowledge source: each system answers the same generated prompt from its own sources, and the evaluator scores answer quality and evidence support.

**Current state: this is a specification plus a starter scaffold, not a working implementation.** [SPEC.md](SPEC.md) is the normative requirements document; [docs/implementation-plan.md](docs/implementation-plan.md) prescribes the PR-by-PR build order (contracts → generation → harness → evaluation → reporting). Only `owrb domain validate` works today; `scenarios generate`, `run`, `evaluate`, and `compare` are stubs that exit with code 2. Most modules in `src/owrb/` are Protocol definitions or `NotImplementedError` placeholders marking milestone targets. When implementing, follow SPEC.md — it resolves most design questions (safe expression rules, evidence handling, scoring weights, hard-constraint caps, etc.).

## Commands

Python 3.12+, managed with `uv`:

```bash
make install        # uv sync --all-extras
make lint           # uv run ruff check .
make typecheck      # uv run mypy src        (strict mode)
make test           # uv run pytest
make check          # lint + typecheck + test

# Single test
uv run pytest tests/test_models.py::test_example_scenario_instance_is_valid

# The one working CLI command
uv run owrb domain validate domains/australian-tourism [--json]
```

All CLI commands must support `--json` output and non-zero exit codes on failure.

## Architecture

Pipeline (see docs/architecture.md and SPEC.md §7):

```
domain pack + seed → frozen scenario instance → system adapters → normalised RunResult
  → shared evidence bundle → mixed evaluation (deterministic / claim-evidence / LLM rubric)
  → EvaluationResult → static HTML reports + paired comparison
```

- **`src/owrb/models.py`** — the canonical runtime contracts (Pydantic 2, all `extra="forbid"`): `DomainPack`, `ScenarioTemplate`, `ScenarioInstance`, `SystemDefinition`, `RunResult`, `EvaluationResult`. JSON Schemas in `schemas/` are generated from these models. Any breaking change to a persisted format requires a `schema_version` increment, a migration note, and updated examples/tests.
- **`src/owrb/generation.py`** — seeded scenario generation (Milestone 1). Instances must be byte-reproducible from the same seed + versions, persisted before any system runs, and replayable without the original parameter provider.
- **`src/owrb/runner.py`** — `SystemAdapter` protocol (async). Adapters normalise heterogeneous systems (HTTP, command, manual import, provider APIs) into the common `RunResult`.
- **`src/owrb/evidence.py` / `evaluation.py`** — evaluator retrieves cited URLs into a shared per-scenario evidence bundle, then scores via deterministic validators, claim/citation judging, and a blind LLM rubric judge.
- **`domains/australian-tourism/`** — the first domain pack: `domain.yaml` manifest, `values/` parameter files (CSV/YAML), `scenarios/` Jinja-templated task definitions with evaluation criteria. Domain packs are **no-code by default** — YAML/CSV only.
- **`suites/`, `systems/`** — YAML configs describing a benchmark run (seed, quotas, systems, repetitions) and how to invoke each candidate system. `runs/` output is gitignored.

## Non-negotiable design rules

These come from SPEC.md and CONTRIBUTING.md and constrain all implementation work:

- A candidate system's ontology/index is **never** benchmark truth. Reject any design that makes evaluation depend on a proprietary index; the Australian tourism ontology is an experimental variable being tested, not a reference.
- Scenario templates must not contain gold answers; gold answers are optional everywhere.
- Quality and efficiency are scored and reported **separately** — never fold cost/latency into the quality score.
- Compatibility rules use a safe expression evaluator, never Python `eval`.
- Secrets are env-var references only; they must never appear in YAML configs or run artefacts.
- The evaluator judge must be blind to candidate system identity.
- Evidence retrieval needs SSRF protection (block loopback/private/link-local/cloud-metadata), size/redirect/timeout limits, caching, and polite rate limits. Unreachable citations are marked (blocked/paywalled/missing), not auto-failed.
- Confirmed hard-constraint violations cap the overall score (default 49) but don't erase dimension scores.
- Filesystem-first: JSON artefacts under `runs/`, static offline HTML reports. No database in v1.
- The OSM-derived location snapshot is a scenario-generation input only, never evidence; it requires OpenStreetMap/ODbL attribution.
