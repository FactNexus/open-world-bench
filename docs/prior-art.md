# Prior art and design influences

This specification combines ideas from several benchmark families while addressing a different target.

## Harvey LAB

Harvey LAB uses a filesystem-first task, harness, evaluation, and static reporting structure. Its tasks include instructions, documents, and rubric criteria. OWRB adopts the organisational clarity but replaces the supplied corpus with open-world retrieval and an evidence ledger.

- Repository: https://github.com/harveyai/harvey-labs
- Architecture: https://github.com/harveyai/harvey-labs/blob/main/docs/architecture.md

## WebArena

WebArena demonstrates reproducible agent evaluation through controlled, self-hosted websites. OWRB does not initially emulate the web; it instead records live-web evidence and run conditions.

- Repository: https://github.com/web-arena-x/webarena

## BrowseComp

BrowseComp focuses on persistent web retrieval for difficult, short-answer questions. OWRB targets longer, recommendation-oriented domain answers with multiple acceptable outcomes.

- Paper: https://arxiv.org/abs/2504.12516

## GAIA

GAIA uses real-world questions requiring browsing, reasoning, and tool use. OWRB adds domain parameterisation, generated scenarios, citation auditing, and efficiency comparison.

- Paper: https://arxiv.org/abs/2311.12983

## DeepResearch Bench

DeepResearch Bench evaluates multi-step research reports and citation quality. OWRB uses similar evidence-oriented ideas but aims for lightweight domain packs and generated, scenario-driven testing.

- Repository: https://github.com/Ayanami0730/deep_research_bench
- Paper: https://arxiv.org/abs/2506.11763

## AndroidWorld

AndroidWorld demonstrates dynamically instantiated tasks with random parameters and durable task structure. OWRB applies the dynamic-instantiation idea to open-world research scenarios.

- Repository: https://github.com/google-research/android_world
