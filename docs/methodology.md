# Methodology and limitations

This document describes how the Open-World Research Benchmark (OWRB) produces
its numbers for the Australian tourism baseline release, and what those
numbers can and cannot support. [SPEC.md](../SPEC.md) is the normative
requirements document; this is the companion read for interpreting results.

## What is being measured

OWRB compares research agents that must find their own evidence. Each
candidate system receives the same frozen scenario prompt and answers from
whatever knowledge sources it can reach — provider-native web search, a
curated index, a RAG pipeline, or a manual product session. The evaluator
then scores:

- **Quality (0–100)** — constraint satisfaction, citation support, factual
  consistency with cited evidence, coverage, source suitability, and clarity,
  weighted per SPEC.md §16.1.
- **Efficiency** — latency, tokens, searches, and cost, reported separately
  and never folded into the quality score. Manual imports are excluded from
  efficiency aggregates.

The benchmark deliberately does **not** provide a shared corpus or a gold
answer. Tourism facts change, several source sets can support equally good
answers, and recommendation tasks admit many valid responses. What is frozen
is the question and the experimental conditions, not the knowledge.

## Scenario generation

Scenarios are generated from the `australian-tourism` domain pack (v0.3.0):
eight templates (nearby discovery, constrained day plan, destination
comparison, accessibility-aware recommendation, weather/season adaptation,
remote-area planning, multi-day multi-stop itineraries, and everyday
essentials), a 76-location snapshot, and value lists for travellers,
interests, constraints, access needs, transport situations, forecast
conditions, and everyday non-tourism needs (pharmacies, groceries, medical
care). The everyday-essentials family deliberately probes services that are
not tourism content but that travellers depend on; the multi-stop family
requires route-level synthesis across several locations, with endpoint
pairs constrained to a drivable straight-line distance band for the trip
length.

Generation is deterministic. Each attempt seed derives from
`sha256(suite_seed:template_id:index:attempt)`, so the same seed and pack
version reproduce byte-identical instances (except the generation
timestamp), and adding templates or instances never perturbs existing ones.
Compatibility rules (evaluated by a whitelisted expression interpreter,
never `eval`) reject unrealistic combinations — a coastal-scenery interest
in an inland town, a weather condition in an impossible season, a
remote-area plan based in a capital city — and rejection resampling is
itself deterministic.

The **public development suite** is the 100 instances committed under
[`examples/dev-suite/`](../examples/dev-suite), generated from
[`suites/australian-tourism-dev.yaml`](../suites/australian-tourism-dev.yaml)
(seed 20260718). A CI test regenerates the suite from the seed and fails if
the committed instances drift from the pack. Following SPEC.md §24, this
suite is for development and public comparison; scored evaluation runs
should generate fresh instances from undisclosed seeds shortly before the
run window.

### The location snapshot

The snapshot (`values/locations.csv`) is hand-curated by OWRB contributors:
76 cities, towns, and tourism regions across all eight states and
territories, with tourism region, a metro/regional/remote classification, a
coastal flag, and sampling weights favouring major destinations.
Coordinates are approximate (roughly 1 km) and are a **scenario-generation
input only** — they are never used as evidence that a recommendation is
correct, and no OpenStreetMap or other database extracts were used (so no
ODbL obligations attach; a future replacement built from OSM must add the
attribution described in [ATTRIBUTION.md](../ATTRIBUTION.md)).

## Execution protocol

For each scenario instance the runner executes every automated system with
per-instance randomised order, a fresh session per trial, identical prompts
(scenario plus the short common answer contract of SPEC.md §13.4), the same
repetition count, and per-trial timeouts. Failures and timeouts are
preserved as scored-zero results, never silently retried. Manual product
sessions are imported with `owrb import` and flagged as manual throughout.

## Evaluation

Evaluation is mixed (SPEC.md §16):

1. **Deterministic checks** — answer presence, citation presence and
   parseability, requested item count, duplicate recommendations.
2. **Evidence retrieval** — the union of every candidate's cited URLs is
   fetched once per scenario into a shared, frozen bundle, with SSRF
   protection, caching, size caps, and polite rate limits. Unreachable
   sources are classified (blocked, paywalled, missing, unextractable,
   invalid), not auto-failed.
3. **Claim and citation judging** — an LLM judge decomposes the answer into
   material claims, maps them to nearby citations, and judges support
   against the retrieved evidence extracts. Citation precision, coverage,
   and unsupported-claim rate are recorded per trial.
4. **Rubric judging** — the same judge scores template criteria
   (feasibility, adaptation, accessibility evidence, and so on) with
   explanations and confidence.

The judge is blind to candidate identity: prompts contain the scenario,
answer, citations, and evidence — never the system name. The baseline
configuration uses one judge model (`claude-opus-4-8`), deliberately a
different model family tier from the baseline candidate; SPEC.md §16.7's
panel, self-consistency, and pairwise extensions remain future work.

Confirmed hard-constraint violations (an inaccessible recommendation where
access was mandatory, an infeasible plan for the stated transport) cap the
trial's quality score at 49 without erasing dimension scores. Trials with
low-confidence judgments, judge failures, or no judge at all are marked
`review_status: required`.

## Comparison and statistics

`owrb compare` reports, per system: mean/median/stdev quality with a seeded
percentile-bootstrap 95% confidence interval, completion and hard-failure
rates, per-template and per-dimension breakdowns, and efficiency
aggregates. System pairs are compared on shared scenarios (paired mean
difference with bootstrap CI, win/tie/loss counts). The quality-cost Pareto
frontier lists systems not dominated on both mean cost and mean quality.

## Reproducibility and web drift

A live-web benchmark cannot make answers permanently reproducible; it can
make experiments traceable. OWRB records the scenario inputs and parameter
file hashes, candidate and judge identities and configuration, run and
evaluation timestamps, cited URLs, retrieved-evidence hashes and statuses,
and every criterion-level result. Comparisons are valid **within a run
set**, where all systems answered the same instances in the same time
window against the same evidence bundle. Comparing quality scores across
run sets executed weeks apart conflates system differences with web drift —
prefer re-running all systems together.

## Known limitations

- **The web moves.** A citation that supported a claim at answer time may
  change or vanish before evaluation. The shared bundle freezes what the
  evaluator saw, but it is a snapshot, not ground truth.
- **Judge subjectivity.** Rubric and claim-support scores come from one LLM
  judge. Blinding removes identity bias but not model-family taste; a
  candidate from the same vendor as the judge may share stylistic priors.
  Pairwise blind judging and multi-judge panels are specified but not yet
  implemented.
- **Unverifiable evidence.** Blocked and paywalled sources are recorded,
  not penalised as false — but claims resting only on them reduce measured
  citation precision. Systems citing paywalled authorities are somewhat
  disadvantaged.
- **Efficiency telemetry is uneven.** Token and cost metrics depend on what
  providers expose; search-request fees (as opposed to token costs) are not
  included in computed `cost_usd`. Efficiency comparisons are strongest
  within a provider and indicative across providers.
- **Deterministic count checks are heuristic.** Recommendation counting
  parses markdown structure and carries reduced confidence; the rubric
  judge re-checks the criterion when configured.
- **Coordinates are approximate**, so the `geo.radius` validator idea from
  SPEC.md §17 is not implemented; radius criteria are judged from evidence
  text instead.
- **Scenario realism is curated, not guaranteed.** Compatibility rules
  remove the combinations we thought of. Residual oddities (an interest
  that is merely unusual rather than impossible for a location) are part of
  the benchmark's open-world character.
- **Baseline coverage.** The committed baseline configuration exercises one
  web-search system; ontology-assisted and RAG candidates are the intended
  comparison targets and require their operators to supply adapters or
  manual imports.

## Contamination posture

The development suite, templates, and parameter files are public. Official
evaluations should generate fresh instances from undisclosed seeds close to
the run window (SPEC.md §24), publish prompt hashes with results, and
disclose the generation timestamp. The parameter space (76 locations × 5
travellers × 8 interests × constraints × templates) is broad enough that
memorising the dev suite does not cover a fresh draw.
