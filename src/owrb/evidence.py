"""Evaluator evidence retrieval, caching, and per-scenario bundles (SPEC.md 15).

Every cited URL is fetched at most once per run set (content-addressed cache
under ``<run-set>/evidence/objects/``), with SSRF checks on every redirect
hop, a response size cap, a per-host politeness interval, and an identifying
user agent. Unreachable sources are classified (blocked, paywalled, missing,
unextractable, invalid) rather than treated as failures (SPEC.md 15.4).

The shared bundle for a scenario is the union of every candidate's cited
URLs, so all candidates are judged against the same web snapshot
(SPEC.md 15.2).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import orjson

from owrb.html_text import extract_text
from owrb.models import EvidenceRecord
from owrb.url_safety import Resolver, check_url, default_resolver

USER_AGENT = "owrb-evaluator/0.1 (benchmark evidence retrieval)"
MAX_REDIRECTS = 5
MAX_CONTENT_BYTES = 2_000_000
_TEXTUAL_TYPES = ("text/", "application/json", "application/xhtml+xml", "application/xml")


def evidence_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


class EvidenceStore:
    """Content-addressed evidence cache with polite, safe retrieval."""

    def __init__(
        self,
        directory: Path,
        transport: Any = None,
        resolver: Resolver = default_resolver,
        min_host_interval: float = 1.0,
        timeout_seconds: float = 20.0,
    ) -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise RuntimeError(
                "evidence retrieval requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self.directory = directory
        self.transport = transport
        self.resolver = resolver
        self.min_host_interval = min_host_interval
        self.timeout_seconds = timeout_seconds
        self._host_last_fetch: dict[str, float] = {}
        self._lock = asyncio.Lock()
        (directory / "objects").mkdir(parents=True, exist_ok=True)

    def _record_path(self, key: str) -> Path:
        return self.directory / "objects" / f"{key}.json"

    def _text_path(self, key: str) -> Path:
        return self.directory / "objects" / f"{key}.txt"

    def load_cached(self, url: str) -> tuple[EvidenceRecord, str] | None:
        key = evidence_key(url)
        record_path = self._record_path(key)
        if not record_path.is_file():
            return None
        record = EvidenceRecord.model_validate_json(record_path.read_text("utf-8"))
        text_path = self._text_path(key)
        text = text_path.read_text("utf-8") if text_path.is_file() else ""
        return record, text

    def _store(self, url: str, record: EvidenceRecord, text: str) -> None:
        key = evidence_key(url)
        self._record_path(key).write_bytes(
            orjson.dumps(record.model_dump(mode="json"), option=orjson.OPT_INDENT_2) + b"\n"
        )
        if text:
            self._text_path(key).write_text(text, encoding="utf-8")

    async def _politeness_delay(self, host: str) -> None:
        if self.min_host_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            last = self._host_last_fetch.get(host, 0.0)
            wait = self.min_host_interval - (now - last)
            self._host_last_fetch[host] = max(now, last + self.min_host_interval)
        if wait > 0:
            await asyncio.sleep(wait)

    async def get(self, url: str, force: bool = False) -> tuple[EvidenceRecord, str]:
        if not force:
            cached = self.load_cached(url)
            if cached is not None:
                return cached
        record, text = await self._fetch(url)
        self._store(url, record, text)
        return record, text

    async def _fetch(self, url: str) -> tuple[EvidenceRecord, str]:
        current_url = url
        async with self._httpx.AsyncClient(
            transport=self.transport,
            timeout=self.timeout_seconds,
            follow_redirects=False,
            headers={"user-agent": USER_AGENT},
        ) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                problem = check_url(current_url, self.resolver)
                if problem is not None:
                    return _record(url, current_url, "invalid", warning=problem), ""
                await self._politeness_delay(urlsplit(current_url).hostname or "")
                try:
                    response = await client.get(current_url)
                except self._httpx.HTTPError as error:
                    return (
                        _record(url, current_url, "missing", warning=f"fetch error: {error}"),
                        "",
                    )
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        return (
                            _record(
                                url,
                                current_url,
                                "missing",
                                http_status=response.status_code,
                                warning="redirect without location",
                            ),
                            "",
                        )
                    current_url = urljoin(current_url, location)
                    continue
                return self._classify(url, current_url, response)
            return _record(url, current_url, "missing", warning="too many redirects"), ""

    def _classify(
        self, requested_url: str, final_url: str, response: Any
    ) -> tuple[EvidenceRecord, str]:
        status_code = int(response.status_code)
        content_type = str(response.headers.get("content-type", "")).split(";")[0].strip()
        if status_code in (401, 403, 429):
            return (
                _record(requested_url, final_url, "blocked", status_code, content_type),
                "",
            )
        if status_code == 402:
            return (
                _record(requested_url, final_url, "paywalled", status_code, content_type),
                "",
            )
        if status_code >= 400:
            return (
                _record(requested_url, final_url, "missing", status_code, content_type),
                "",
            )
        body = response.content[: MAX_CONTENT_BYTES + 1]
        truncated = len(body) > MAX_CONTENT_BYTES
        if truncated:
            body = body[:MAX_CONTENT_BYTES]
        if content_type and not content_type.startswith(_TEXTUAL_TYPES):
            return (
                _record(
                    requested_url,
                    final_url,
                    "unextractable",
                    status_code,
                    content_type,
                    warning=f"non-text content type {content_type!r}",
                ),
                "",
            )
        raw = body.decode(response.encoding or "utf-8", errors="replace")
        if content_type in ("text/html", "application/xhtml+xml", ""):
            text, title = extract_text(raw)
        else:
            text, title = raw, None
        text = text.strip()
        if not text:
            return (
                _record(
                    requested_url,
                    final_url,
                    "unextractable",
                    status_code,
                    content_type,
                    warning="no extractable text",
                ),
                "",
            )
        record = EvidenceRecord(
            url=requested_url,
            final_url=final_url if final_url != requested_url else None,
            status="reachable",
            http_status=status_code,
            content_type=content_type or None,
            content_hash=hashlib.sha256(body).hexdigest(),
            title=title,
            retrieved_at=datetime.now(tz=UTC),
            text_length=len(text),
            warning="content truncated at size limit" if truncated else None,
        )
        return record, text


def _record(
    requested_url: str,
    final_url: str,
    status: str,
    http_status: int | None = None,
    content_type: str | None = None,
    warning: str | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        url=requested_url,
        final_url=final_url if final_url != requested_url else None,
        status=status,  # type: ignore[arg-type]
        http_status=http_status,
        content_type=content_type or None,
        retrieved_at=datetime.now(tz=UTC),
        warning=warning,
    )


async def build_evidence_bundle(
    store: EvidenceStore,
    scenario_id: str,
    urls: list[str],
    bundle_directory: Path,
    force: bool = False,
) -> dict[str, EvidenceRecord]:
    """Retrieve the union of candidate-cited URLs once and freeze the bundle."""
    records: dict[str, EvidenceRecord] = {}
    for url in sorted(set(urls)):
        record, _text = await store.get(url, force=force)
        records[url] = record
    bundle_directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "scenario_instance_id": scenario_id,
        "sources": {
            url: {"key": evidence_key(url), **record.model_dump(mode="json")}
            for url, record in records.items()
        },
    }
    (bundle_directory / f"{scenario_id}.bundle.json").write_bytes(
        orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    )
    return records
