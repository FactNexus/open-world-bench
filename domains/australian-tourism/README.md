# Australian tourism domain pack

Compares open-web research systems with curated Australian tourism retrieval
systems, including ontology and non-ontology variants. The pack itself does
not depend on an ontology and never defines benchmark truth from a candidate
index.

## Contents (v0.3.0)

- **76-location snapshot** (`values/locations.csv`): all states and
  territories, with tourism region, metro/regional/remote classification,
  coastal flag, and sampling weights. Hand-curated, approximate coordinates;
  generation input only — see [ATTRIBUTION.md](../../ATTRIBUTION.md).
- **Eight scenario templates** (`scenarios/`): nearby discovery, constrained
  day plan, destination comparison, accessibility-aware recommendation,
  weather/season adaptation, remote-area planning, multi-day multi-stop
  itineraries, and everyday essentials (non-tourism services — pharmacies,
  groceries, medical care — that travellers still need).
- **Value lists** (`values/`): travellers, interests, constraints, access
  needs, transport situations, season-linked forecast conditions, and
  everyday essential needs.
- **Compatibility rules** keep scenarios realistic: coastal interests require
  coastal locations, forecast conditions match their seasons, remote-area
  plans are based in remote locations, compared destinations differ,
  road-trip endpoints sit within a drivable straight-line distance band for
  the trip length (and Tasmania is never paired with the mainland), and
  child-specific needs only arise for parties travelling with children.
- **Advisory source policy** (`source-policy.yaml`).

## Public development suite

`suites/australian-tourism-dev.yaml` (seed 20260718) defines the public
100-instance development suite committed under `examples/dev-suite/`. It
regenerates byte-identically from the seed; a CI test enforces this. For
scored evaluations, generate fresh instances from an undisclosed seed.
