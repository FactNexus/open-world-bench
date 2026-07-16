import asyncio
from pathlib import Path

import httpx

from owrb.evidence import EvidenceStore, build_evidence_bundle, evidence_key
from owrb.html_text import extract_text

HTML_PAGE = """
<html><head><title>Blue Mountains walks</title><style>.x{color:red}</style></head>
<body><script>alert('ignored')</script>
<h1>Scenic walks</h1><p>The Grand Clifftop Walk is open daily.</p>
<ul><li>Katoomba Falls</li><li>Echo Point</li></ul>
</body></html>
"""


def resolve_public(host: str) -> list[str]:
    return ["93.184.216.34"]


def make_store(tmp_path: Path, handler) -> tuple[EvidenceStore, dict]:  # type: ignore[no-untyped-def]
    calls = {"count": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return handler(request)

    store = EvidenceStore(
        tmp_path / "evidence",
        transport=httpx.MockTransport(counting_handler),
        resolver=resolve_public,
        min_host_interval=0,
    )
    return store, calls


def test_html_text_extraction_strips_script_and_keeps_structure() -> None:
    text, title = extract_text(HTML_PAGE)
    assert title == "Blue Mountains walks"
    assert "Grand Clifftop Walk is open daily" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_reachable_page_is_extracted_hashed_and_cached(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=HTML_PAGE)

    store, calls = make_store(tmp_path, handler)
    record, text = asyncio.run(store.get("https://example.com/walks"))
    assert record.status == "reachable"
    assert record.title == "Blue Mountains walks"
    assert record.content_hash and len(record.content_hash) == 64
    assert "Katoomba Falls" in text
    assert calls["count"] == 1

    cached_record, cached_text = asyncio.run(store.get("https://example.com/walks"))
    assert calls["count"] == 1, "second call must be served from the cache"
    assert cached_record.content_hash == record.content_hash
    assert cached_text == text

    asyncio.run(store.get("https://example.com/walks", force=True))
    assert calls["count"] == 2, "force must bypass the cache"


def test_error_statuses_are_classified_not_raised(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mapping = {
            "/blocked": 403,
            "/paywalled": 402,
            "/missing": 404,
            "/rate-limited": 429,
        }
        return httpx.Response(mapping[request.url.path])

    store, _calls = make_store(tmp_path, handler)

    def status_of(path: str) -> str:
        record, _text = asyncio.run(store.get(f"https://example.com{path}"))
        return record.status

    assert status_of("/blocked") == "blocked"
    assert status_of("/paywalled") == "paywalled"
    assert status_of("/missing") == "missing"
    assert status_of("/rate-limited") == "blocked"


def test_non_text_and_empty_content_is_unextractable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/image":
            return httpx.Response(
                200, content=b"\x89PNG", headers={"content-type": "image/png"}
            )
        return httpx.Response(200, html="<html><body><script>x()</script></body></html>")

    store, _calls = make_store(tmp_path, handler)
    image_record, _ = asyncio.run(store.get("https://example.com/image"))
    assert image_record.status == "unextractable"
    empty_record, _ = asyncio.run(store.get("https://example.com/empty"))
    assert empty_record.status == "unextractable"


def test_redirects_are_followed_and_validated(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        if request.url.path == "/evil":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/meta"})
        return httpx.Response(200, html="<html><body><p>Arrived.</p></body></html>")

    store, _calls = make_store(tmp_path, handler)
    record, text = asyncio.run(store.get("https://example.com/start"))
    assert record.status == "reachable"
    assert record.final_url == "https://example.com/final"
    assert "Arrived" in text

    evil_record, _ = asyncio.run(store.get("https://example.com/evil"))
    assert evil_record.status == "invalid"
    assert "not a public unicast" in (evil_record.warning or "")


def test_unsafe_url_is_never_fetched(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("unsafe URL must not reach the transport")

    store, calls = make_store(tmp_path, handler)
    record, _ = asyncio.run(store.get("http://127.0.0.1/secrets"))
    assert record.status == "invalid"
    assert calls["count"] == 0


def test_bundle_freezes_union_of_urls(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><body><p>Source.</p></body></html>")

    store, calls = make_store(tmp_path, handler)
    urls = ["https://example.com/a", "https://example.com/b", "https://example.com/a"]
    records = asyncio.run(
        build_evidence_bundle(store, "scenario-1", urls, tmp_path / "bundles")
    )
    assert set(records) == {"https://example.com/a", "https://example.com/b"}
    assert calls["count"] == 2, "duplicate URLs must be fetched once"
    bundle_path = tmp_path / "bundles" / "scenario-1.bundle.json"
    assert bundle_path.is_file()
    assert evidence_key("https://example.com/a") in bundle_path.read_text("utf-8")
