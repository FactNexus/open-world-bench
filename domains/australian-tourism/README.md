# Australian tourism domain pack

Compares open-web research systems with curated Australian tourism retrieval
systems, including ontology and non-ontology variants. The pack itself does
not depend on an ontology and never defines benchmark truth from a candidate
index.

## Contents (v0.2.0)

- **76-location snapshot** (`values/locations.csv`): all states and
  territories, with tourism region, metro/regional/remote classification,
  coastal flag, and sampling weights. Hand-curated, approximate coordinates;
  generation input only — see [ATTRIBUTION.md](../../ATTRIBUTION.md).
- **Six scenario templates** (`scenarios/`): nearby discovery, constrained
  day plan, destination comparison, accessibility-aware recommendation,
  weather/season adaptation, and remote-area planning.
- **Value lists** (`values/`): travellers, interests, constraints, access
  needs, transport situations, and season-linked forecast conditions.
- **Compatibility rules** keep scenarios realistic: coastal interests require
  coastal locations, forecast conditions match their seasons, remote-area
  plans are based in remote locations, and compared destinations differ.
- **Advisory source policy** (`source-policy.yaml`).

## Public development suite

`suites/australian-tourism-dev.yaml` (seed 20260718) defines the public
100-instance development suite committed under `examples/dev-suite/`. It
regenerates byte-identically from the seed; a CI test enforces this. For
scored evaluations, generate fresh instances from an undisclosed seed.
