# Northern campers domain pack

Compares curated WikiCamps camping data against open-web research systems for
caravanners and campers on long regional trips, on one corridor: Broome to
Katherine via the Gibb River Road and the Victoria Highway. Built for the
WikiCamps / OWRB assessment (brief amended 2026-09-16).

## Contents (v0.1.0)

- **26 corridor places** (`values/locations.csv`): towns, gorges, stations,
  roadhouses and parks from Broome to Nitmiluk, each with approximate
  coordinates (1 to 5 km; generation input only, never evidence), an access
  class (`sealed`, `gibb`, `track`) and a sampling weight.
- **9 route legs** (`values/legs.csv`): endpoints, route description, distance,
  whether four-wheel drive is required, whether it uses the Gibb, and a
  plausible day range. Legs, not single places, drive the touring templates.
- **6 traveller segments** (`values/travellers.csv`): the WikiCamps personas
  (Big Lapper, Grey Nomad Explorer, 4WD Adventurer, Caravan Comfort Seeker,
  Backpacker / Budget Nomad, Weekend Wanderer at low weight), each with the
  rigs it may travel in, minimum nights per stop and activities per day.
- **Rigs** (`values/rigs.yaml`): off-road caravan, 2WD campervan, 4WD, 2WD
  car and tent, with a `gibb_ok` flag; **interests** from Len Goold's list
  including community vibe, Indigenous heritage, rides and walks by length;
  **needs** (dump point, water fill, fuel, toilets, shade, reception,
  generators, pets, long vehicle, powered site, self-contained); **budget
  stances**; and **months** April to November with Gibb-open and shoulder flags.
- **Six templates** (`scenarios/`), one per brief question family:
  `corridor-trip-plan`, `overnight-suitability`, `hidden-poi`,
  `review-informed-judgement`, `remote-confidence-plan`, `budget-plan`.
- **Compatibility rules**: no two-wheel-drive rig on a four-wheel-drive-only
  leg; no Gibb leg in a month the road is normally closed (except in the
  confidence template, where shoulder months are the point); rigs match the
  traveller segment; budget stance matches the segment; trip days fit the leg
  and the daily distance cap.
- **Source policy** with a `structured_crowd_sourced` class (dated, voted,
  high-volume reports) at 0.7 for fees, conditions and suitability.

No prompt names WikiCamps or any candidate source.

## What the pack deliberately leaves out

Question family 7 of the brief (does equivalent information exist publicly
for these markers) is a property of the corpus, not a research task, and is
measured by the paired win/loss gap between the web-search arms and the
WikiCamps arms plus a separate export analysis. Accessibility and
child-specific scenarios are absent on WikiCamps' guidance about its users.

## Suite

`suites/northern-campers-dev.yaml`: seed 20260917, 100 instances, quotas
20/20/15/15/15/15, five arms (WikiCamps only; the `northern-exposure` tag;
the same with ontology; GPT-5.6 with OpenAI web search; Gemini with Google
Search grounding), Claude Opus 4.8 as judge, and gateway-fetched evidence for
the loopback md-site pages.
