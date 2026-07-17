# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**OWRB (Open-World Research Benchmark)** — a domain-parameterised benchmark for comparing research agents (web-search LLMs, RAG systems, curated indexes, MCP agents) that must find their own evidence on the live web. It freezes the **scenario**, not the knowledge source: each system answers the same generated prompt from its own sources, and the evaluator scores answer quality and evidence support.

**Current state: Milestones 0–4 of SPEC.md §26 are implemented — the full §27 MVP command surface works.** [SPEC.md](SPEC.md) is the normative requirements document. All CLI commands are live: `owrb domain validate|list`, `owrb scenarios generate|inspect`, `owrb systems validate`, `owrb schemas generate [--check]`, `owrb run --suite`, `owrb import`, `owrb evaluate`, `owrb evidence refresh`, `owrb compare`, `owrb report`. Remaining work is Milestone 5 (baseline release: expanded location snapshot, six-plus templates, 100-instance dev suite, baseline runs, methodology doc) plus SPEC extras like pairwise blind judging, contrast sets, and replay mode. Follow SPEC.md when extending — it resolves most design questions.

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
- **`src/owrb/generation.py`** — seeded scenario generation. Attempt seeds derive from `sha256(suite_seed:template_id:index:attempt)`, so instances are byte-reproducible from the same seed + versions (except `generated_at`), and adding templates/instances never perturbs others. Instances persist all selected parameters, so replay never needs the original provider. `providers/builtin.py` holds the no-code providers (csv, yaml_list, values, range, date_window, derived) and records SHA-256 hashes of loaded parameter files onto instances. `expressions.py` is the AST-whitelist safe evaluator used by compatibility rules and `derived` parameters.
- **`src/owrb/validation.py`** — deep domain-pack/system validation behind `owrb domain validate` and `owrb systems validate`: resolves template globs, cross-checks parameter references, loads provider files, compares prompt placeholders against declared parameters, parses rules. `schema_export.py` regenerates `schemas/` from the models; CI fails if they drift (`owrb schemas generate --check`).
- **`src/owrb/runner.py`** — run-set orchestration behind `owrb run`: loads a suite (`SuiteConfig`), freezes scenarios first, then runs every system per instance with randomised order, a concurrency semaphore, and per-trial timeouts. Failures/timeouts become preserved `RunResult`s, never retries. Artefacts land in `runs/<run-set-id>/<instance>/<system>/<trial>/`. `adapters/` holds the registry (`create_adapter`) and the adapters: `generic_http` (dot-path response mapping), `command` (JSON over stdin/stdout), `anthropic_api`/`openai_api` (native web search, httpx with a `transport` test seam), and manual import via `owrb import`. `adapters/base.py` has the common answer-contract prompt (`build_input_text`) every system receives.
- **`src/owrb/evidence.py` / `evaluation.py`** — `EvidenceStore` is a content-addressed cache under `<run-set>/evidence/objects/` with SSRF checks on every redirect hop (`url_safety.py`, resolver injectable for tests), a per-host politeness interval, size caps, and SPEC §15.4 status classification (blocked/paywalled/missing/unextractable/invalid — never a crash). The shared per-scenario bundle is the union of all candidates' cited URLs, fetched once. `evaluation.py` runs deterministic checks (`validators/`), then with a judge (`judge.py`, anthropic/openai, `transport` test seam): claim decomposition → citation-support verdicts → rubric scoring, always blind to system identity. Framework criteria carry `framework.` IDs; hard failures cap quality at `hard_constraint_score_cap` (default 49) without erasing dimension scores. No judge configured → deterministic-only scores with `review_status: required`.
- **`src/owrb/stats.py` / `reporting.py`** — `owrb compare` computes per-system aggregates (mean/median/stdev, seeded percentile-bootstrap 95% CIs), paired win/tie/loss on shared scenario means, per-template breakdowns, and efficiency aggregates (manual imports excluded per SPEC §13.5) with a quality-cost Pareto frontier. `owrb report` renders a fully offline dashboard (`report/index.html`, inline CSS, no scripts/external assets), a per-trial `report.html` audit view (Jinja2 autoescape — answers are untrusted), and CSV/JSON exports of every dashboard number.
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
