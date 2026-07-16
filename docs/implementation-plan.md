# Coding-agent implementation plan

The coding agent should implement the repository in small, reviewable pull requests.

## Pull request 1: contracts and validation

- complete Pydantic models;
- generate JSON Schemas;
- implement domain and system validation;
- add clear error paths with file and field names;
- make CI pass.

## Pull request 2: scenario generation

- implement built-in providers;
- implement safe rules;
- add seeded generation and canonical serialisation;
- add generation report and duplicate detection.

## Pull request 3: harness

- implement run-set state machine;
- add generic HTTP, command, and manual adapters;
- add concurrency, timeout, and retry recording;
- write normalised artefacts atomically.

## Pull request 4: provider adapters

- add provider adapters behind optional dependencies;
- add recorded fixtures rather than live API calls in unit tests;
- document current web-search settings using official provider documentation.

## Pull request 5: evidence ledger

- safe URL validation;
- retrieval cache;
- HTML/text extraction;
- content hashing;
- blocked/paywalled status handling.

## Pull request 6: evaluation

- answer and citation parser;
- claim extraction;
- deterministic validators;
- judge abstraction;
- criterion scoring and hard caps.

## Pull request 7: reports

- per-run static HTML;
- run-set comparison;
- paired statistics and bootstrap intervals;
- CSV/JSON export.

## Pull request 8: Australian tourism baseline

- expand templates and parameter snapshots;
- run scenario quality checks;
- add baseline suite and methodology notes.

## General instructions

- do not add a database unless a documented requirement cannot be met by files;
- do not couple the core to Australian tourism;
- do not use a candidate ontology in evaluation;
- do not use unbounded web crawling;
- do not silently discard failed runs;
- include type hints, descriptive names, and tests for every public contract;
- update schemas and migration notes for every breaking format change.
