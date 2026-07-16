# Open-World Research Benchmark: Implementation Specification

**Working name:** Open-World Research Benchmark  
**Package and CLI name:** `owrb`  
**Initial domain pack:** `australian-tourism`  
**Implementation language:** Python 3.12+  
**Repository style:** filesystem-first, command-line driven, static reports

## 1. Executive summary

Build an open-source benchmark for comparing agents that answer domain-specific questions by retrieving information from sources they choose themselves.

The benchmark must support a fair comparison between:

- general-purpose models using provider-native web search;
- research agents using browser or search tools;
- domain-specific RAG systems;
- curated indexes;
- ontology-assisted retrieval;
- MCP-based agents;
- custom systems exposed through an HTTP API or command-line adapter.

The benchmark is **open-world** because it does not provide a common corpus of source documents. A generated scenario is sent to each candidate system. Each system searches its own accessible knowledge sources and returns an answer with citations. The benchmark then evaluates:

- satisfaction of user constraints;
- factual support and citation correctness;
- completeness and usefulness;
- source suitability and freshness;
- clarity;
- retrieval and execution efficiency.

The Australian tourism domain pack is the first implementation and demonstration. The framework must not depend on the user’s Australian tourism ontology. That ontology is one experimental variable being tested.

## 2. Problem statement

Closed-corpus benchmarks can define a task, provide a known set of documents, and grade the answer against those documents. This is unsuitable for comparing a curated Australian tourism system with models such as Gemini or Claude using web search because:

- the systems do not use the same corpus;
- tourism facts change frequently;
- several different source sets can support equally good answers;
- recommendation tasks often have many valid answers;
- the value of a curated index or ontology may appear in efficiency, coverage, and source quality rather than a unique final answer.

OWRB therefore freezes the **question and experimental conditions**, not the body of knowledge.

## 3. Goals

### 3.1 Primary goals

1. Generate varied, realistic, reproducible scenarios from lightweight domain packs.
2. Run the same frozen scenario instances against several heterogeneous systems.
3. accept answers, citations, traces, and metrics through a common result contract.
4. Evaluate answers without requiring a single gold answer.
5. Separate answer quality from execution efficiency.
6. Produce auditable per-run reports and paired system comparisons.
7. Make it easy to add a new domain without writing code.
8. Support public development suites and private or freshly generated evaluation suites.

### 3.2 Secondary goals

- Support both API-driven and manually imported answers.
- Store sufficient evidence to review scores after web content changes.
- Provide deterministic validation where practical.
- Support repeated trials and statistical confidence intervals.
- Allow custom validators and parameter providers as optional extensions.

## 4. Non-goals for version 1

Version 1 will not:

- provide a general browser automation environment;
- reproduce consumer product user interfaces;
- guarantee full trace visibility from hosted search products;
- establish a permanent public leaderboard;
- judge booking transactions or irreversible actions;
- require every domain to provide a knowledge graph or ontology;
- require a human-authored reference answer for every scenario;
- make live web results perfectly reproducible months later.

## 5. Core principles

### 5.1 Domain-neutral core

The framework owns scenario generation, execution, evidence handling, evaluation, reporting, and comparison. Domain-specific information belongs in domain packs.

### 5.2 Lightweight domain packs

A domain pack must be useful without custom code. Its minimum contents are:

- a manifest;
- one parameter provider or parameter file;
- one scenario template.

Custom source policies, validators, and Python providers are optional.

### 5.3 Scenario freezing

Randomisation occurs before systems are run. The resulting scenario instance is persisted with:

- the random seed;
- all selected parameters;
- the rendered prompt;
- generation metadata;
- version identifiers for the domain pack and parameter datasets.

Every system receives the same rendered scenario.

### 5.4 No ontology-derived truth

A candidate system’s ontology, index, graph, or source catalogue must never be used as the benchmark’s hidden truth. Candidate citations should resolve to original or independently reviewable sources where possible.

### 5.5 Mixed evaluation

Use deterministic validators for mechanically testable constraints. Use evidence-based semantic evaluation for claims and recommendations. Use human review for low-confidence or disputed cases.

### 5.6 Quality and efficiency are separate

Do not hide cost or latency inside the main quality score. Report both and provide quality-efficiency frontier views.

### 5.7 Auditability

A score must be traceable to:

- the exact scenario;
- the exact answer;
- the cited evidence available at evaluation time;
- the evaluator configuration;
- criterion-level results.

## 6. Intended users

- developers comparing RAG and search architectures;
- tourism technology vendors;
- destination marketing organisations;
- researchers evaluating retrieval agents;
- model vendors;
- teams assessing whether a domain ontology adds measurable value.

## 7. High-level architecture

```text
+---------------------+
| Domain pack         |
| templates + values  |
+----------+----------+
           |
           v
+---------------------+
| Scenario generator  |
| seeded + reproducible|
+----------+----------+
           |
           v
+---------------------+        +---------------------+
| Scenario instances  |------->| System adapters     |
+---------------------+        | web/RAG/MCP/manual  |
                               +----------+----------+
                                          |
                                          v
                               +---------------------+
                               | Normalised run      |
                               | answer/cites/metrics|
                               +----------+----------+
                                          |
                     +--------------------+--------------------+
                     |                    |                    |
                     v                    v                    v
             deterministic        evidence/claim       rubric/pairwise
             validators           evaluation           evaluation
                     |                    |                    |
                     +--------------------+--------------------+
                                          |
                                          v
                               +---------------------+
                               | Scores and reports  |
                               +---------------------+
```

## 8. Repository structure

```text
.
├── README.md
├── SPEC.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
├── benchmark.example.yaml
├── src/owrb/
│   ├── cli.py
│   ├── models.py
│   ├── generation.py
│   ├── runner.py
│   ├── evidence.py
│   ├── evaluation.py
│   ├── reporting.py
│   ├── adapters/
│   ├── providers/
│   └── validators/
├── schemas/
├── domains/
│   └── australian-tourism/
├── systems/
├── suites/
├── examples/
├── docs/
├── tests/
└── runs/                    # gitignored
```

The framework should remain filesystem-first. A database may be added later for a hosted leaderboard but is not required for local execution.

## 9. Core data model

### 9.1 Domain pack

Describes a domain, available scenario templates, parameter providers, defaults, and optional policies.

Required fields:

- `schema_version`
- `id`
- `name`
- `description`
- `templates`
- `parameters`

Optional fields:

- `default_locale`
- `default_timezone`
- `source_policy`
- `validators`
- `licence`
- `attribution`
- `metadata`

### 9.2 Scenario template

A reusable task definition containing:

- a Jinja-style prompt template;
- parameter references;
- generation constraints;
- answer requirements;
- evaluation criteria;
- tags and difficulty hints.

A template must not contain a gold answer.

### 9.3 Scenario instance

An immutable generated task containing:

- globally unique instance ID;
- domain and template versions;
- seed;
- selected parameters;
- rendered prompt;
- answer contract;
- evaluation plan;
- generation timestamp;
- parameter source hashes.

### 9.4 System definition

Describes how to invoke one candidate system and what telemetry it can expose.

Fields include:

- system ID and display name;
- adapter type;
- provider/model identifiers;
- declared capabilities;
- environment-variable references for secrets;
- timeout and retry policy;
- answer extraction configuration;
- cost metadata if the provider does not return costs.

### 9.5 Normalised run result

Every adapter must emit the same logical record:

- scenario instance ID;
- system ID;
- start and end time;
- final answer as Markdown or plain text;
- citations, including URL and cited text range when available;
- provider-native response metadata;
- token, cost, latency, and tool-call metrics where available;
- normalised trace events where available;
- warnings for unavailable telemetry;
- status: `completed`, `failed`, `timeout`, or `manual`.

### 9.6 Evaluation result

Contains:

- criterion-level scores and explanations;
- dimension scores;
- hard-constraint failures;
- evidence retrieval status;
- judge identity and configuration;
- confidence and disagreement indicators;
- overall quality score;
- efficiency metrics kept outside the quality score;
- review status.

## 10. Scenario generation

### 10.1 Generation sequence

1. Load and validate the domain pack.
2. Select a scenario template according to suite configuration.
3. Create a deterministic random generator from the suite seed and instance index.
4. Resolve each template parameter from its provider.
5. Apply compatibility rules and rejection sampling.
6. Render the prompt.
7. Materialise the evaluation plan.
8. Persist the scenario instance before running any system.

### 10.2 Parameter providers

Version 1 must support no-code providers:

- `csv`: select a row from a CSV file;
- `yaml_list`: select an item from a YAML list;
- `values`: select from inline values;
- `range`: sample an integer or decimal range;
- `date_window`: sample a date or relative season from a permitted window;
- `derived`: calculate a value from previously selected parameters using a restricted expression language.

Optional extension providers:

- `python`: invoke a registered Python provider;
- `http_snapshot`: select from a previously downloaded and versioned dataset;
- `osm_snapshot`: select from an imported OSM-derived location snapshot.

The benchmark must not depend on a live Overpass or Nominatim call during a scored run. A separate import command may create a versioned local snapshot.

### 10.3 Compatibility rules

Templates may declare simple rules, for example:

```yaml
rules:
  - expression: "not (traveller.requires_wheelchair_access and activity.physical_level == 'high')"
  - expression: "max_distance_km >= location.minimum_practical_radius_km"
```

Use a safe expression evaluator. Do not use Python `eval`.

### 10.4 Randomness and replay

- Use a documented pseudorandom generator.
- Derive each scenario seed from the suite seed, template ID, and instance index.
- Persist all selected values; replay must not require the original provider.
- Re-generating with the same versions and seed must produce byte-equivalent instance JSON except for explicitly excluded timestamps.

### 10.5 Generation quality controls

The generator must support:

- maximum rejection attempts;
- template-level validation;
- duplicate detection by canonical prompt hash;
- minimum geographic spread;
- tag quotas;
- difficulty quotas;
- dry-run generation reports.

## 11. Domain pack requirements

### 11.1 Minimum viable domain pack

A domain author supplies:

```text
domains/<domain-id>/
├── domain.yaml
├── values/
│   └── locations.csv
└── scenarios/
    └── example.yaml
```

That is sufficient for validation and scenario generation.

### 11.2 Optional additions

```text
├── source-policy.yaml
├── validators/
├── providers/
├── fixtures/
├── README.md
└── ATTRIBUTION.md
```

### 11.3 Source policy

A source policy is advisory rather than a fixed whitelist. It can define source classes and context-sensitive authority, for example:

- a park authority is preferred for closure and access rules;
- an attraction operator is authoritative for its own opening hours and accessibility claims;
- a transport operator is authoritative for schedules and fares;
- an official tourism body is suitable for discovery but may not be the best source for current operating details;
- user-generated material can support subjective experience but should not be the sole support for safety, legal, access, or price claims.

The policy may influence the source-quality dimension but must not automatically reject an otherwise well-supported answer.

### 11.4 Custom validators

Custom validators are optional. A pack without them must still be fully usable through the generic evaluator.

A validator receives:

- scenario instance;
- answer;
- extracted claims and recommendations;
- citations and retrieved evidence;
- run metadata.

It returns criterion findings, not a replacement total score.

## 12. Australian tourism domain pack

### 12.1 Purpose

The first domain pack tests whether a curated Australian tourism system and ontology provide measurable value compared with general web-search systems.

### 12.2 Initial scenario families

Version 1 should include at least:

1. **Nearby discovery** — find a small number of experiences matching interests and traveller constraints within a radius.
2. **Constrained day plan** — construct a feasible day using several activities and practical constraints.
3. **Destination comparison** — compare two destinations for a given traveller profile.
4. **Accessibility-aware recommendation** — find options supported by explicit accessibility evidence.
5. **Weather or season adaptation** — adapt recommendations to stated conditions without requiring live weather in the first release.
6. **Remote-area planning** — identify sparse-information and transport limitations.

Only the first three are required for the MVP scaffold.

### 12.3 Initial parameter sets

- Australian town, city, or tourism region;
- traveller archetype;
- interest;
- travel radius;
- number of requested recommendations;
- transport mode;
- mobility or accessibility requirement;
- broad season;
- budget band;
- trip duration.

### 12.4 Location source

Use a checked-in, versioned location snapshot for generation. The snapshot may be built from OpenStreetMap or another suitable source. Record:

- source name;
- source retrieval date;
- licence and attribution;
- source record identifier where available;
- coordinates and administrative region;
- optional sampling weights.

Do not use the location snapshot as evidence that a tourism recommendation is correct. It is a scenario-generation input only.

## 13. System adapters

### 13.1 Adapter interface

Each adapter implements:

```python
class SystemAdapter(Protocol):
    def run(self, request: RunRequest) -> RunResult:
        ...
```

The implementation may be synchronous or asynchronous internally, but the harness must expose an async execution path for concurrency.

### 13.2 Required adapter types

MVP:

- `generic_http`: POST a normalised request and map the response;
- `command`: invoke a local executable or script;
- `manual_import`: import an answer and citations captured elsewhere;
- `openai` or equivalent provider adapter;
- `anthropic` or equivalent provider adapter;
- `google` or equivalent provider adapter.

Provider adapters must isolate unstable provider-specific search configuration from the core framework.

### 13.3 Capability declaration

Systems declare capabilities such as:

- `web_search`
- `browser`
- `domain_index`
- `ontology`
- `mcp`
- `citations`
- `trace`
- `token_metrics`
- `cost_metrics`

These declarations are descriptive and included in reports. They do not grant tools.

### 13.4 Prompt contract

Every system receives:

- the same scenario prompt;
- a short common answer contract;
- no domain pack internals;
- no scoring rubric beyond user-visible response requirements;
- no other systems’ outputs.

The common answer contract should require:

- a direct answer;
- explicit handling of every stated constraint;
- citations close to supported claims;
- clear uncertainty where information cannot be verified;
- no claim that a recommendation is current unless supported by a current source.

### 13.5 Manual import

Manual import is necessary for systems that cannot be reliably invoked through an API. It must accept:

- `answer.md`;
- optional `citations.json`;
- optional manually entered latency and product metadata;
- a declaration that the result is manual.

Manual runs must be reported separately from automated runs when comparing efficiency.

## 14. Execution protocol

### 14.1 Suite configuration

A suite identifies:

- domain pack and version;
- templates and quotas;
- seed and instance count;
- systems;
- repetitions per system-instance pair;
- concurrency;
- timeouts;
- evaluation configuration;
- evidence caching policy.

### 14.2 Fair comparison

For each scenario instance:

- run all systems within the shortest practical time window;
- randomise system execution order;
- start each system with a fresh conversation or session;
- disable cross-run memory where possible;
- record model version, reasoning settings, temperature, and tool configuration;
- run the same number of repetitions;
- preserve failures and timeouts rather than silently retrying until success.

Retries caused by transport failure may be allowed but must be recorded.

### 14.3 Run artefacts

```text
runs/<run-set-id>/<scenario-instance-id>/<system-id>/<trial-id>/
├── config.json
├── scenario.json
├── answer.md
├── citations.json
├── trace.jsonl
├── metrics.json
├── provider-response.json        # optional and redacted
├── evaluation.json
└── report.html
```

Secrets and raw authentication headers must never be written to run artefacts.

## 15. Evidence handling

### 15.1 Citation model

A citation record should include:

- citation ID;
- URL;
- title if supplied;
- source name;
- access timestamp;
- claims or answer spans associated with the citation;
- provider-supplied snippet if available;
- retrieval status;
- canonical URL and content hash after evaluator retrieval.

### 15.2 Shared evaluation evidence bundle

After all candidate systems have completed a scenario instance, the evaluator builds one shared evidence bundle for that scenario. The bundle contains:

- the union of candidate-cited URLs;
- independently retrieved corroborating sources for critical claims and hard constraints;
- source metadata, extracted text, hashes, and retrieval status.

The frozen bundle is then used when evaluating every candidate answer for that scenario. This reduces evaluation drift and prevents one system from being judged against a materially different web snapshot. Candidate citations remain attributable to the candidate; independently discovered evaluator sources are labelled separately.

The independent corroboration step should focus on material, disputed, time-sensitive, and hard-constraint claims rather than attempting to crawl the whole domain. It must use the same retrieval budget for every candidate answer.

### 15.3 Evaluator evidence retrieval

The evaluator independently retrieves cited and corroborating URLs where lawful and practical. It stores:

- final URL after redirects;
- retrieval timestamp;
- HTTP status and content type;
- extracted text;
- content hash;
- publication or update date when detected;
- robots or access restrictions;
- a bounded snapshot or text extract sufficient for audit.

Evidence retrieval must use caching, polite rate limits, size limits, and user-agent identification.

### 15.4 Unavailable sources

A citation must not automatically fail because the evaluator cannot retrieve it. Mark it as:

- reachable;
- blocked;
- paywalled;
- dynamic/unextractable;
- missing;
- invalid.

Use provider snippets or archived copies only when clearly labelled. Unverifiable claims should reduce confidence and may enter human review.

### 15.5 Candidate-owned indexes

A domain RAG system may cite its own internal record, but for full evidence credit it should also expose the original source URL and, ideally, retrieval date and source extract. Internal identifiers alone are insufficient for independent audit.

## 16. Evaluation methodology

### 16.1 Main quality dimensions

Recommended default weighting:

| Dimension | Weight |
|---|---:|
| Constraint satisfaction | 25 |
| Citation support and grounded factuality | 25 |
| Factual correctness beyond cited claims | 20 |
| Coverage and practical usefulness | 15 |
| Source suitability and freshness | 10 |
| Clarity and uncertainty handling | 5 |

Total quality score: 0–100.

Domain packs may adjust weights within bounded limits, but comparison reports must show both the domain weighting and raw dimension scores.

### 16.2 Efficiency metrics

Report separately:

- wall-clock latency;
- time to first token where available;
- input and output tokens;
- retrieved-context tokens or bytes where available;
- search/tool calls;
- unique sources consulted;
- provider cost;
- retries and failures.

Derived measures may include:

- quality per dollar;
- quality per 10,000 tokens;
- quality per minute;
- Pareto-efficient systems.

Do not collapse these into the primary quality score by default.

### 16.3 Deterministic evaluation

Use deterministic checks for:

- answer presence;
- requested item count;
- required sections or output format;
- parseable cited URLs;
- duplicate recommendations;
- explicit numeric constraints when extractable;
- coordinates and radius when reliable coordinates can be resolved;
- date arithmetic;
- forbidden or required fields.

### 16.4 Claim and citation evaluation

1. Decompose the answer into material factual claims and recommendations.
2. Map each claim to nearby or explicitly referenced citations.
3. Retrieve cited evidence.
4. Judge whether the evidence supports, contradicts, or does not address the claim.
5. Judge whether the source is appropriate for that type of claim.
6. Check whether time-sensitive claims are supported by sufficiently fresh evidence.
7. Calculate citation precision, citation coverage, and unsupported-claim rate.

### 16.5 Independent corroboration

For critical or suspicious claims, the evaluator should perform bounded independent search rather than relying only on candidate-selected citations. Corroboration findings must be added to the shared scenario evidence bundle before final candidate scoring.

Independent retrieval is not treated as a perfect gold corpus. The report must distinguish between a claim that is contradicted, a claim that is unsupported within the retrieval budget, and a claim that is genuinely false.

### 16.6 Rubric evaluation

Use an LLM judge for qualities that cannot be determined mechanically, including:

- whether the response satisfies the traveller’s underlying need;
- whether constraints are handled coherently;
- whether recommendations are sufficiently diverse;
- whether caveats are proportionate;
- whether the plan is practical.

The judge receives the scenario, answer, extracted claims, citations, retrieved evidence summaries, and criterion. It must not receive the candidate system identity.

### 16.7 Judge configuration

MVP may use one configurable judge model. The architecture must support:

- multiple judges;
- blind pairwise judging;
- self-consistency repeats;
- disagreement thresholds;
- human adjudication.

Never use the candidate’s own answer as the sole reference. Prefer a judge model different from the candidate models when practical.

### 16.8 Hard constraints

Scenario criteria may be marked `hard`. A confirmed hard-constraint violation should:

- be shown prominently;
- cap the overall score at a configurable threshold, default 49;
- not erase the underlying dimension scores.

Example hard constraints include recommending an inaccessible activity where wheelchair access is mandatory or exceeding an explicit maximum travel radius without warning.

### 16.9 Pairwise comparison

In addition to absolute scoring, the framework should support blind A/B comparison for usefulness and answer preference. Randomise answer order and report paired win rate with ties.

### 16.10 Statistical reporting

For a system across a suite report:

- mean and median score;
- standard deviation;
- bootstrap 95% confidence interval;
- completion and failure rate;
- per-template and per-tag results;
- paired difference against each other system;
- pairwise win/tie/loss rate;
- efficiency distributions.

## 17. Evaluation rubric format

Each scenario template defines criteria such as:

```yaml
criteria:
  - id: constraint-distance
    dimension: constraint_satisfaction
    title: Recommendations respect the maximum travel radius
    description: >
      Recommendations should be within the stated radius of the base location,
      unless the answer clearly labels and justifies an exception.
    hard: true
    weight: 1.0
    validator: geo.radius

  - id: evidence-current
    dimension: citation_support
    title: Current operational claims are cited
    description: >
      Opening, access, booking, price, and closure claims must have nearby
      citations to sources suitable for those claims.
    hard: false
    weight: 1.0
```

Generic criteria can be inherited from the framework. Templates should define only what differs from the defaults.

## 18. Reporting

### 18.1 Per-run report

Show:

- scenario;
- answer with citation links;
- criterion and dimension scores;
- unsupported or contradicted claims;
- source table with authority and freshness findings;
- hard failures;
- metrics;
- trace summary;
- evaluator confidence and review status.

### 18.2 Comparison dashboard

Show:

- overall quality by system;
- dimension radar or grouped bar data;
- per-template heatmap;
- paired win matrix;
- cost, latency, and token plots;
- quality-cost frontier;
- failure rates;
- ontology versus non-ontology ablation comparison;
- downloadable JSON and CSV summaries.

Static HTML is sufficient for version 1.

## 19. Command-line interface

Required commands:

```text
owrb domain validate <path>
owrb domain list
owrb scenarios generate --domain <id> --count <n> --seed <seed>
owrb scenarios inspect <instance-id>
owrb systems validate <path>
owrb run --suite <suite.yaml>
owrb import --scenario <id> --system <id> --answer <file>
owrb evaluate --run-set <id>
owrb compare --run-set <id>
owrb report --run-set <id>
owrb evidence refresh --run-set <id>
```

All commands must support `--json` machine-readable output and non-zero exit codes on failure.

## 20. Configuration and secrets

- YAML for human-authored configuration.
- JSON for immutable generated instances and run artefacts.
- Pydantic models are the canonical runtime contract.
- JSON Schemas are generated from Pydantic and checked into `schemas/`.
- Secrets are referenced as environment variables, never embedded in YAML.
- `.env` may be supported for local development but must be gitignored.

## 21. Security and privacy

- Treat retrieved web content as untrusted.
- Do not execute code from retrieved pages.
- Apply strict size, MIME, redirect, and timeout limits.
- Protect against SSRF in evaluator URL retrieval; block loopback, link-local, private ranges, and cloud metadata endpoints.
- Redact secrets and personal data from raw provider responses.
- Make trace storage configurable.
- Candidate systems that execute local tools should run in a sandbox with explicit network egress policy.
- Do not create scenarios that require disclosing private traveller information.

## 22. Reproducibility and web drift

A live-web benchmark cannot guarantee permanent answer reproducibility. It can guarantee experimental traceability.

Record:

- scenario generation inputs and hashes;
- candidate and judge versions;
- exact run and evaluation timestamps;
- citation URLs;
- evaluator-retrieved evidence hashes;
- source date metadata;
- configuration and package version;
- environment summary.

Run competing systems in paired windows. For published results, retain the evidence ledger and disclose that the web is time-dependent.

Optionally support a later `replay` mode using stored evidence snapshots. Replay scores evidence use and synthesis, but it is not equivalent to testing live retrieval.

## 23. Contrast sets and sensitivity testing

The framework should later support automatically generated contrast sets: paired scenarios that differ in one important parameter, such as `private car` versus `no car`, or `general traveller` versus `wheelchair user`. Contrast scoring measures whether a system changes its recommendations appropriately instead of returning a generic destination answer.

Contrast sets are optional and must not increase the minimum domain-pack requirements. They can be generated automatically from ordinary parameter providers and template metadata.

## 24. Avoiding benchmark contamination

- Publish templates and a development suite.
- Generate official evaluation instances from undisclosed seeds or shortly before evaluation.
- Keep generated evaluation prompts private until the run window closes.
- Use broad parameter spaces and template variants.
- Track exact prompt hashes.
- Avoid fixed, easily memorised lists of questions.
- Publish scenario instances with final results for audit when appropriate.

## 25. Implementation technology

Recommended stack:

- Python 3.12+
- `uv` for environments and locking
- Pydantic 2 for contracts
- Typer for CLI
- PyYAML for configuration
- Jinja2 for prompt templates
- `httpx` for HTTP and evidence retrieval
- `tenacity` for bounded retries
- `selectolax` or equivalent for HTML extraction
- `tldextract` for domain analysis
- `orjson` for artefacts
- `jinja2` plus embedded assets for static reports
- `pytest`, `ruff`, and `mypy` for quality

Provider SDKs should be optional extras so users do not install every vendor dependency.

## 26. Implementation milestones

### Milestone 0 — Contracts and repository skeleton

Deliver:

- package layout;
- Pydantic contracts;
- JSON Schemas;
- domain and system validation;
- CI;
- Australian tourism sample pack.

Acceptance:

- `uv sync` succeeds;
- tests pass;
- sample domain validates;
- schema examples round-trip.

### Milestone 1 — Scenario generation

Deliver:

- built-in no-code providers;
- seeded generation;
- compatibility rules;
- instance persistence;
- duplicate detection;
- generation report.

Acceptance:

- same seed and versions reproduce identical instances;
- invalid combinations are rejected;
- 100 Australian tourism scenarios generate without duplicates or unresolved placeholders.

### Milestone 2 — Run harness and adapters

Deliver:

- async runner;
- generic HTTP, command, and manual adapters;
- at least two provider adapters;
- trace and metric normalisation;
- run-set orchestration.

Acceptance:

- one suite can run at least three systems;
- failures and timeouts are preserved;
- no secret appears in artefacts.

### Milestone 3 — Evidence and evaluation

Deliver:

- citation extraction and normalisation;
- safe evidence retrieval and caching;
- deterministic checks;
- claim decomposition;
- citation support judging;
- rubric judging;
- evaluation artefacts.

Acceptance:

- unsupported claims are surfaced with evidence;
- blocked sources are handled without crashing;
- scoring is blind to candidate identity;
- all criterion results are auditable.

### Milestone 4 — Reporting and comparison

Deliver:

- per-run static report;
- suite comparison report;
- paired statistics and bootstrap intervals;
- quality and efficiency views;
- CSV/JSON exports.

Acceptance:

- report works offline after generation;
- ontology ablation results can be compared directly;
- all chart data is exportable.

### Milestone 5 — Baseline Australian tourism release

Deliver:

- expanded location snapshot;
- at least six scenario templates;
- 100-instance public development suite;
- baseline runs for selected systems;
- methodology and limitations document.

Acceptance:

- domain pack contains no dependency on a candidate ontology;
- all locations and templates include provenance/version metadata;
- manual review of a sample shows acceptable scenario realism.

## 27. MVP definition of done

The MVP is complete when a user can:

1. validate the Australian tourism domain pack;
2. generate a reproducible set of scenarios;
3. run or import answers from at least three systems;
4. retrieve and cache cited evidence;
5. receive criterion-level quality scores and separate efficiency metrics;
6. compare systems in a static HTML report;
7. reproduce the scenario set and inspect every score input.

## 28. Required tests

- schema validation tests;
- deterministic generation tests;
- safe-expression tests;
- duplicate-prompt tests;
- adapter contract tests using fixtures;
- timeout and retry tests;
- citation parsing tests;
- SSRF and URL safety tests;
- evidence cache tests;
- evaluator identity-blinding tests;
- report snapshot tests;
- statistical calculation tests.

## 29. Key design decisions already made

1. The framework is domain-parameterised, not tourism-specific.
2. Australian tourism is the first domain pack.
3. Scenario instances are frozen before systems run.
4. Systems may use different knowledge sources.
5. The candidate ontology is not benchmark truth.
6. Domain packs are no-code by default.
7. Gold answers are optional, not required.
8. Evaluation combines deterministic checks, evidence support, and rubric judging.
9. Quality and efficiency are reported separately.
10. Filesystem artefacts and static reports are the version 1 persistence model.

## 30. Open implementation decisions

The coding agent should document its choice for:

- safe expression engine;
- HTML extraction library;
- citation syntax parser;
- judge API abstraction;
- report chart library;
- whether Pydantic models or JSON Schema generation is the primary source file;
- evidence snapshot retention limits;
- default judge model;
- cost catalogue maintenance.

These choices must not change the public contracts without a schema-version increment.
