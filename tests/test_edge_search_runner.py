"""Pure helpers of the edge-search runner: source registry, pack annotation, quote checks
and submission validation. The network paths are exercised by the smoke test in the
runner's docstring, not here."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "edge_search_agent", Path(__file__).resolve().parent.parent / "runners" / "edge_search_agent.py"
)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

PACK = (
    "## [Deckchair Cinema | Darwin](https://example.nt/deckchair)\n\n"
    "The Deckchair Cinema is an open-air cinema on the Darwin waterfront. "
    "It operates **seasonally**, from mid-April to mid-November, with screenings nightly.\n"
    "\n---\n"
    "## [Darwin in 48 hours](https://example.nt/tw/48-hours)\n\n"
    "_This page matched the search but its content could not be retrieved. "
    "The page exists; try fetching it directly._\n"
    "\n---\n"
    "## [Mindil Beach Markets](https://example.nt/mindil)\n\n"
    "Markets run Thursday and Sunday evenings during the dry season (April to October). "
    "Entry is free. ![img](https://example.nt/x.jpg)\n"
)


def test_pack_annotation_numbers_retrievable_pages_only() -> None:
    registry = runner.SourceRegistry()
    annotated, ids = runner.annotate_pack(PACK, registry)
    assert ids == ["c1", "c2"]
    assert "## [c1] [Deckchair Cinema | Darwin](https://example.nt/deckchair)" in annotated
    assert "## [c2] [Mindil Beach Markets](https://example.nt/mindil)" in annotated
    assert "(unavailable, cannot be cited) [Darwin in 48 hours]" in annotated
    assert registry.by_id("c1")["title"] == "Deckchair Cinema | Darwin"
    # a second pack carrying the same page keeps its id and appends new text
    again, ids2 = runner.annotate_pack(
        "## [Deckchair Cinema | Darwin](https://example.nt/deckchair)\n\n"
        "Tickets cost $20 for adults.",
        registry,
    )
    assert ids2 == ["c1"] and "## [c1] " in again
    assert "tickets cost $20" in registry.by_id("c1")["norm"]


def test_quote_matching_tolerates_markdown_and_trimmed_ends() -> None:
    registry = runner.SourceRegistry()
    runner.annotate_pack(PACK, registry)
    page = registry.by_id("c1")["norm"]
    assert runner.quote_found("It operates seasonally, from mid-April to mid-November", page)
    assert runner.quote_found(
        "operates **seasonally**, from mid-april to mid-november, with screenings nightly.", page
    )
    # a long quote with a wrong tail still matches on its longest sentence
    assert runner.quote_found(
        "The Deckchair Cinema is an open-air cinema on the Darwin waterfront. "
        "It is closed on Mondays.",
        page,
    )
    assert not runner.quote_found("Screenings start at 7pm sharp every night of the year", page)
    assert not runner.quote_found("cinema", page), "too short to count as evidence"


def test_validation_builds_citations_from_markers_and_flags_problems() -> None:
    registry = runner.SourceRegistry()
    runner.annotate_pack(PACK, registry)
    answer = (
        "Go to the Deckchair Cinema, which runs from mid-April to mid-November [c1]. "
        "Mindil Beach Markets are free to enter [c2]. Parking is plentiful [unverified]. "
        "The wave lagoon costs $5 [c7]."
    )
    citations = [
        {"id": "c1", "quote": "It operates seasonally, from mid-April to mid-November"},
        {"id": "c2", "quote": "Parking costs $10 per hour"},
        {"id": "c9", "quote": "anything"},
    ]
    built, problems, stats = runner.validate_submission(answer, citations, registry)
    assert [c["id"] for c in built] == ["c1", "c2"]
    assert built[0]["quote"].startswith("It operates seasonally") and "quote" not in built[1]
    assert built[0]["url"] == "https://example.nt/deckchair" and built[0]["title"]
    assert stats["markers"] == 3
    assert stats["quotes_verified"] == 1 and stats["quotes_failed"] == 1
    assert stats["unknown_ids"] == 2  # c9 in citations, c7 in the answer
    joined = " ".join(problems)
    assert "c9" in joined and "[c7]" in joined and "quote for c2" in joined
    # a clean submission has no problems
    built, problems, _ = runner.validate_submission(
        "Runs mid-April to mid-November [c1].",
        [{"id": "c1", "quote": "from mid-April to mid-November, with screenings nightly"}],
        registry,
    )
    assert not problems and built[0].get("quote")


def test_validation_accepts_url_in_place_of_id_and_orders_by_id() -> None:
    registry = runner.SourceRegistry()
    runner.annotate_pack(PACK, registry)
    answer = "Markets are free [c2]. The cinema is seasonal [c1]."
    citations = [
        {"url": "https://example.nt/mindil", "quote": "Entry is free."},
        {"id": "c1", "quote": "It operates seasonally"},
    ]
    built, problems, _ = runner.validate_submission(answer, citations, registry)
    assert not problems
    assert [c["id"] for c in built] == ["c1", "c2"]


def test_markers_only_answer_gets_citations_without_quotes() -> None:
    registry = runner.SourceRegistry()
    runner.annotate_pack(PACK, registry)
    built = runner.citations_from_markers(
        "Free entry [c2], seasonal cinema [c1], unknown [c4].", registry
    )
    assert [c["id"] for c in built] == ["c1", "c2"]
    assert all("quote" not in c for c in built)


def test_markers_accept_grouped_ids() -> None:
    text = "Open daily [c1, c4]. Free [c2]; closed Mondays [c4; c9] and [c2]."
    assert runner.markers_in(text) == ["c1", "c4", "c2", "c9"]
    assert runner.markers_in("No markers here, only [unverified] and [see note].") == []


def _services_edge_search():
    """An EdgeSearch whose HTTP client answers /v1/entities/near from a fixture."""
    import json

    import httpx

    page = (
        "# Terrace Pharmacy\n\nPharmacy, 1.9 km N of Katherine South.\n\n- Type: pharmacy\n"
        "- Opening hours: Mon–Fri 08:30–17:00 (recorded as `Mo-Fr 08:30-17:00`)\n\n"
        "Source: austourism ontology (epoch 1255). Place data from OpenStreetMap."
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/v1/entities/near"
        body = json.loads(request.content)
        assert body["include_pages"] is True and body["category"] == "pharmacy"
        return httpx.Response(
            200,
            json={
                "place": {"name": "Katherine"},
                "radius_km": 10,
                "total_within_radius": 1,
                "results": [
                    {
                        "name": "Terrace Pharmacy",
                        "url": "http://edge/v1/entity/1/10363417",
                        "distance_km": 0.1,
                        "direction": "SW",
                        "types": ["pharmacy"],
                        "opening_hours": "Mon–Fri 08:30–17:00",
                        "page": page,
                    }
                ],
                "note": "Confirm before relying on them.",
            },
        )

    es = runner.EdgeSearch("http://edge", "k", 13, True, None, [], None)
    es.client = httpx.Client(base_url="http://edge", transport=httpx.MockTransport(handler))
    return es, calls


def test_services_near_registers_citable_sources() -> None:
    import json

    es, calls = _services_edge_search()
    out = json.loads(es.services_near("pharmacy", "Katherine"))
    assert out["results"][0]["source_id"] == "c1"
    assert out["results"][0]["opening_hours"] == "Mon–Fri 08:30–17:00"
    url = "http://edge/v1/entity/1/10363417"
    assert es.registry.by_url[url]["title"] == "Terrace Pharmacy"
    assert es.surfaced == [url] and es.last_urls == [url]
    # read_url on a registered entity serves the registered page: no gateway fetch.
    text = es.read_url(url)
    assert text.startswith("## [c1] [Terrace Pharmacy]") and calls == ["/v1/entities/near"]
    # A quote from the page validates against the registered source.
    citations, problems, _ = runner.validate_submission(
        "Terrace Pharmacy opens weekdays [c1].",
        [{"id": "c1", "quote": "Opening hours: Mon–Fri 08:30–17:00"}],
        es.registry,
    )
    assert not problems and citations[0]["url"] == url
