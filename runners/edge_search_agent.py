#!/usr/bin/env python3
"""OWRB command-adapter runner: a tool-calling agent over edge-search.

The model (any OpenAI-compatible chat model via OpenRouter) is given three
retrieval tools backed by an edge-search manifold — ranked URL search, a content
pack, and a fetch of one specific page — plus a ``submit_answer`` tool that ends
the episode. One script serves every model+edge-search system; fairness between
systems comes from varying only argv (model, manifold, --disable-ontology,
--tags), never code.

Runner 2.0 (2026-09-23) adds claim-level attribution. Every page the model reads is
registered as a numbered source (``[c3]`` in its heading); the answer must carry
the source id after each factual claim, or ``[unverified]`` where none was found;
``submit_answer`` takes, per cited source, a verbatim quote that the runner checks
against the text it served, and hands the submission back to the model to fix
(twice at most) when a quote is not found or an id is unknown. Citations in the
output are derived from the markers, so what the judge sees as "cited" is what the
model attributed, not a list assembled afterwards. ``runners/legacy/`` keeps the
1.x runner for before/after comparison.

    stdin  <- {"scenario_instance_id": ..., "prompt": ..., "answer_contract": {...}}
    stdout -> {"answer": ..., "citations": [...], "metrics": {...}, "trace": [...]}

Environment: OPENROUTER_API_KEY, EDGE_SEARCH_URL (default
http://127.0.0.1:8096 — an SSH tunnel to the edge-search host), EDGE_SEARCH_API_KEY.

Smoke test:
    echo '{"prompt": "Suggest a rainy-day activity in Port Macquarie for a \
family with a toddler.", "answer_contract": {}}' \
        | python runners/edge_search_agent.py --model google/gemini-3-flash-preview --manifold 13
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOOL_RESULT_CHARS = 24_000
READ_URL_MAX_TOKENS = 5000
MAX_RESUBMITS = 2
RUNNER_VERSION = "2.1"

SYSTEM_PROMPT = """\
You are a careful research assistant answering questions about Australian \
travel and tourism using a curated search index of Australian tourism sites.

How to work:
- `search` finds pages (titles and URLs). `read` returns the content of the \
best-matching pages for a focused query. `read_url` returns the content of one \
specific page you have seen in results. Every page you read is given a source id \
such as [c3] in its heading; only pages you have read can be cited.
- Do not answer from memory. Before you state a fact, find it in a page you have \
read. If you cannot find it, leave it out, or write it followed by [unverified].
- Cite at claim level: put the source id, for example [c3], immediately after \
each factual sentence, table cell or bullet that the page supports. Cite a page \
only for what it actually says; a page that is merely about the same place does \
not count. Every operational detail (opening hours, prices and their dates, \
distances, accessibility, seasonal closures) needs a source id.
- Reformulate and retry searches if the first results are weak. Prefer several \
focused searches over one broad one, and read the pages behind the claims you \
will make.
- When you have enough evidence, call `submit_answer` with the answer and, for \
every source id you cited, a verbatim quote from that page that supports the \
claims marked with it. Quotes are checked against the page text; a submission \
with a quote that is not in the page, or an unknown source id, is returned to you \
to fix.
- If the index cannot support a confident answer, say so in the answer and \
give the best-supported partial answer you can.
- Follow the answer format the task asks for.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the tourism index. Returns ranked URLs with "
            "titles and relevance scores, and the ontology concepts the query "
            "resolved to (when available). Search results cannot be cited: read "
            "a page first to get its source id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of results (default 8).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Retrieve readable page content for a query from the "
            "tourism index (a markdown pack of the best-matching pages, each "
            "headed by its source id and URL). Use after `search`, with a "
            "focused query, to read what the pages actually say.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Focused content query."},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "description": "Number of pages in the pack (default 4).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "Fetch one specific page that appeared in search or "
            "read results, by URL, to check exactly what it says before citing "
            "it. Returns the page content headed by its source id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "A URL from earlier results."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": "Submit the final answer and finish. The answer carries a "
            "source id such as [c3] after each factual claim; the citations give a "
            "verbatim quote for every source id used.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "The complete final answer in markdown, with "
                        "a source id like [c3] immediately after each factual claim "
                        "it supports, and [unverified] after any claim you could not "
                        "find in a page you read.",
                    },
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Source id from a page heading, e.g. c3.",
                                },
                                "quote": {
                                    "type": "string",
                                    "description": "A verbatim passage of up to about "
                                    "300 characters from that page that supports the "
                                    "claims marked with the id.",
                                },
                                "url": {"type": "string"},
                            },
                            "required": ["id", "quote"],
                        },
                        "description": "One entry per source id cited in the answer.",
                    },
                },
                "required": ["answer", "citations"],
            },
        },
    },
]

SERVICES_TOOL = {
    "type": "function",
    "function": {
        "name": "services_near",
        "description": "Find everyday services near a place from the index's ontology: pharmacies, "
        "GPs and medical centres, urgent care, hospitals, supermarkets, fuel, banks, ATMs, "
        "dentists, "
        "post offices, public toilets. Returns named services nearest first with distance and "
        "direction from the place and opening hours where recorded. Each result is registered as a "
        "readable source with its own source id, so it can be cited directly. Data comes from "
        "OpenStreetMap and may be out of date; say so when you rely on it.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Kind of service, e.g. 'pharmacy', 'GP', 'urgent care', "
                    "'supermarket', 'fuel', 'ATM'.",
                },
                "place": {
                    "type": "string",
                    "description": "Town, city or suburb, e.g. 'Katherine' or 'Mount Gambier, SA'.",
                },
                "radius_km": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Search radius in km (default 10).",
                },
            },
            "required": ["category", "place"],
        },
    },
}

SERVICES_PROMPT = """
- For practical needs such as a pharmacy, GP or urgent care, groceries, fuel, cash or a \
post office, call `services_near` with the kind of service and the town. Tourism pages \
rarely cover these. Its results are sources you can cite by id; state any opening hours \
exactly as recorded and note that they should be confirmed.
"""

_PACK_HEADER = re.compile(r"^## \[(.*?)\]\((https?://[^)\s]+)\)\s*$", re.M)
_UNAVAILABLE = "could not be retrieved"
_FRONT_MATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n\s*", re.S)
# A marker is [c3]; a claim with several sources may carry [c1, c4] or [c1; c4].
_MARKER_GROUP = re.compile(r"\[(\s*c\d+\s*(?:[,;]\s*c\d+\s*)*)\]")
_MARKER_ID = re.compile(r"c\d+")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://)[^)]*\)")
_MIN_QUOTE_CHARS = 12
_MIN_PIECE_CHARS = 40


def normalise(text: str) -> str:
    """Fold markdown and whitespace so a quote can be matched against served text."""
    text = _MD_IMAGE.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"[#>|]", " ", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip().casefold()


def quote_found(quote: str, page_norm: str) -> bool:
    """True when the quote, or a long sentence of it, appears verbatim in the page."""
    q = normalise(quote)
    if len(q) < _MIN_QUOTE_CHARS:
        return False
    if q in page_norm:
        return True
    pieces = [
        p.strip() for p in re.split(r"(?<=[.!?;:])\s+", q) if len(p.strip()) >= _MIN_PIECE_CHARS
    ]
    pieces.sort(key=len, reverse=True)
    return any(p in page_norm for p in pieces[:3])


class SourceRegistry:
    """Pages the model has read, numbered in order of first appearance."""

    def __init__(self) -> None:
        self.by_url: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []

    def register(self, url: str, title: str | None, text: str) -> str:
        url = url.strip()
        source = self.by_url.get(url)
        if source is None:
            source = {
                "id": f"c{len(self.order) + 1}",
                "url": url,
                "title": None,
                "text": "",
                "norm": "",
            }
            self.by_url[url] = source
            self.order.append(url)
        if title and not source["title"]:
            source["title"] = title.strip()
        text = text.strip()
        if text and text not in source["text"]:
            source["text"] = (source["text"] + "\n\n" + text) if source["text"] else text
            source["norm"] = normalise(source["text"])
        return str(source["id"])

    def by_id(self, source_id: str) -> dict[str, Any] | None:
        for source in self.by_url.values():
            if source["id"] == source_id:
                return source
        return None

    def ids(self) -> set[str]:
        return {str(source["id"]) for source in self.by_url.values()}


def annotate_pack(pack: str, registry: SourceRegistry) -> tuple[str, list[str]]:
    """Register each retrievable page of a content pack; return it with ids in the headings."""
    out: list[str] = []
    ids: list[str] = []
    for block in re.split(r"\n---\n", pack):
        match = _PACK_HEADER.search(block)
        if not match:
            out.append(block)
            continue
        title, url = match.group(1).strip(), match.group(2)
        body = block[match.end() :].strip()
        if not body or _UNAVAILABLE in body:
            out.append(f"## (unavailable, cannot be cited) [{title}]({url})\n\n" + body)
            continue
        source_id = registry.register(url, title, body)
        ids.append(source_id)
        out.append(f"## [{source_id}] [{title}]({url})\n\n{body}")
    return "\n---\n".join(out), ids


def markers_in(answer: str) -> list[str]:
    """Source ids marked in the answer, in order of first appearance."""
    seen: list[str] = []
    for group in _MARKER_GROUP.findall(answer):
        for source_id in _MARKER_ID.findall(group):
            if source_id not in seen:
                seen.append(source_id)
    return seen


def _id_order(source_id: str) -> int:
    try:
        return int(source_id[1:])
    except ValueError:
        return 10**9


def validate_submission(
    answer: str, raw_citations: Any, registry: SourceRegistry
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """Check markers and quotes against the sources read; build the output citations.

    Returns (citations, problems, stats). Problems are phrased for the model, which
    gets them back when the submission is rejected. Citations cover every source id
    marked in the answer (in id order), each with its quote when that quote was
    found in the page, followed by any quoted source the answer did not mark.
    """
    problems: list[str] = []
    stats = {"markers": 0, "quotes_verified": 0, "quotes_failed": 0, "unknown_ids": 0, "unused": 0}
    known = registry.ids()
    markers = markers_in(answer)
    stats["markers"] = len(markers)
    quotes: dict[str, str] = {}
    for item in raw_citations if isinstance(raw_citations, list) else []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("id") or "").strip()
        if not source_id and item.get("url"):
            source = registry.by_url.get(str(item["url"]).strip())
            source_id = str(source["id"]) if source else ""
        if source_id not in known:
            stats["unknown_ids"] += 1
            problems.append(
                f"citation id {source_id or item.get('url') or '?'} is not a source id from a page "
                "you read; cite only ids shown in page headings"
            )
            continue
        quotes[source_id] = str(item.get("quote") or "").strip()
    for source_id in markers:
        if source_id not in known:
            stats["unknown_ids"] += 1
            problems.append(f"[{source_id}] in the answer is not a source id from a page you read")
        elif source_id not in quotes:
            problems.append(
                f"[{source_id}] is cited in the answer but has no entry with a quote in citations"
            )
    verified: set[str] = set()
    for source_id, quote in quotes.items():
        source = registry.by_id(source_id)
        if source is not None and quote and quote_found(quote, str(source["norm"])):
            verified.add(source_id)
            stats["quotes_verified"] += 1
        else:
            stats["quotes_failed"] += 1
            problems.append(
                f"the quote for {source_id} is not found in that page's text; copy an exact "
                "passage from the page, or cite a page that does say it"
            )
        if source_id not in markers:
            stats["unused"] += 1
    if quotes and not markers:
        problems.append(
            "the answer has no inline source markers such as [c2]; place the source id "
            "immediately after each factual claim it supports"
        )
    ordered = sorted((s for s in markers if s in known), key=_id_order)
    ordered += sorted((s for s in quotes if s not in markers), key=_id_order)
    citations: list[dict[str, Any]] = []
    for source_id in ordered:
        source = registry.by_id(source_id)
        if source is None:
            continue
        entry: dict[str, Any] = {"id": source_id, "url": source["url"]}
        if source["title"]:
            entry["title"] = source["title"]
        if source_id in verified:
            entry["quote"] = quotes[source_id]
        citations.append(entry)
    return citations, problems, stats


def citations_from_markers(answer: str, registry: SourceRegistry) -> list[dict[str, Any]]:
    """Citations for an answer that carries markers but reached no valid submit call."""
    citations: list[dict[str, Any]] = []
    for source_id in sorted((s for s in markers_in(answer) if s in registry.ids()), key=_id_order):
        source = registry.by_id(source_id)
        if source is not None:
            entry = {"id": source_id, "url": source["url"]}
            if source["title"]:
                entry["title"] = source["title"]
            citations.append(entry)
    return citations


class EdgeSearch:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        manifold_id: int,
        disable_ontology: bool,
        ontology_mode: str | None,
        tags: list[str],
        tag_mode: str | None,
    ) -> None:
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )
        self.manifold_id = manifold_id
        self.disable_ontology = disable_ontology
        self.ontology_mode = ontology_mode
        self.tags = tags
        self.tag_mode = tag_mode
        self.searches = 0
        self.ok_calls = 0
        self.read_urls: list[str] = []
        self.seen_urls: set[str] = set()
        # Every URL a search or read returned to the model, in first-seen order, and the
        # URLs the latest call returned: the "surfaced" layer of breadth, logged per call.
        self.surfaced: list[str] = []
        self.last_urls: list[str] = []
        self.registry = SourceRegistry()

    def _scope(self) -> dict[str, Any]:
        return {
            "manifold_id": self.manifold_id,
            "disable_ontology": self.disable_ontology,
            **({"ontology_mode": self.ontology_mode} if self.ontology_mode else {}),
            **({"tags": self.tags} if self.tags else {}),
            **({"tag_mode": self.tag_mode} if self.tags and self.tag_mode else {}),
        }

    def _surface(self, urls: list[str]) -> None:
        self.last_urls = list(dict.fromkeys(urls))
        for u in self.last_urls:
            if u not in self.surfaced:
                self.surfaced.append(u)

    def search(self, query: str, top_k: int = 8) -> str:
        self.searches += 1
        r = self.client.post(
            "/v1/urls",
            json={"query": query, "top_k": max(1, min(int(top_k or 8), 20)), **self._scope()},
        )
        r.raise_for_status()
        self.ok_calls += 1
        data = r.json()
        results = [
            {"url": x["url"], "title": x.get("title"), "score": round(x["score"], 3)}
            for x in data.get("results", [])
        ]
        self.seen_urls.update(x["url"] for x in results)
        self._surface([x["url"] for x in results])
        out: dict[str, Any] = {
            "results": results,
            "note": "Read a page (read or read_url) before citing it; "
            "cite by the source id in its heading.",
        }
        if data.get("concepts"):
            out["resolved_concepts"] = [c.get("label") for c in data["concepts"] if c.get("label")]
        return json.dumps(out)

    def read(self, query: str, top_k: int = 4) -> str:
        self.searches += 1
        r = self.client.post(
            "/v1/content",
            json={
                "query": query,
                "top_k": max(1, min(int(top_k or 4), 8)),
                "token_limit": 5000,
                **self._scope(),
            },
        )
        r.raise_for_status()
        self.ok_calls += 1
        content = r.json().get("content", "")
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        content = content[:MAX_TOOL_RESULT_CHARS]
        self._surface(re.findall(r"\]\((https?://[^)\s]+)\)", content))
        for url in re.findall(r"\]\((https?://[^)\s]+)\)", content):
            self.seen_urls.add(url)
            if url not in self.read_urls:
                self.read_urls.append(url)
        annotated, _ids = annotate_pack(content, self.registry)
        return annotated

    def services_near(self, category: str, place: str, radius_km: float = 10.0) -> str:
        self.searches += 1
        r = self.client.post(
            "/v1/entities/near",
            json={
                "manifold_id": self.manifold_id,
                "category": category,
                "place": place,
                "radius_km": max(1.0, min(float(radius_km or 10), 100.0)),
                "limit": 10,
                "include_pages": True,
            },
        )
        if r.status_code in (400, 404):
            return json.dumps(
                {
                    "error": (
                        r.json()
                        if r.headers.get("content-type", "").startswith("application/json")
                        else r.text
                    )
                }
            )
        r.raise_for_status()
        self.ok_calls += 1
        data = r.json()
        out = []
        urls = []
        for res in data.get("results", []):
            url, page = res.get("url"), res.get("page")
            if not url or not page:
                continue
            urls.append(url)
            self.seen_urls.add(url)
            if url not in self.read_urls:
                self.read_urls.append(url)
            sid = self.registry.register(url, res.get("name"), page)
            out.append(
                {
                    "source_id": sid,
                    "name": res.get("name"),
                    "distance_km": res.get("distance_km"),
                    "direction": res.get("direction"),
                    "types": res.get("types"),
                    "opening_hours": res.get("opening_hours") or "not recorded",
                    "url": url,
                }
            )
        self._surface(urls)
        return json.dumps(
            {
                "place": (data.get("place") or {}).get("name"),
                "radius_km": data.get("radius_km"),
                "total_within_radius": data.get("total_within_radius"),
                "results": out,
                "note": "Each result is a source you can cite by its source_id. "
                + (data.get("note") or ""),
            }
        )

    def read_url(self, url: str) -> str:
        url = (url or "").strip()
        known = self.registry.by_url.get(url)
        if known is not None and known.get("text"):
            # Already registered (e.g. a services_near result): serve what the model was given.
            return f"## [{known['id']}] [{known.get('title') or url}]({url})\n\n{known['text']}"
        if url not in self.seen_urls:
            return json.dumps({"error": "read_url accepts only a URL returned by search or read"})
        self.searches += 1
        r = self.client.post(
            "/v1/gateway/fetch",
            json={
                "manifold_id": self.manifold_id,
                "url": url,
                "accept": "markdown",
                "mode": "content",
                "max_tokens": READ_URL_MAX_TOKENS,
            },
        )
        r.raise_for_status()
        self.ok_calls += 1
        payload = r.json()
        status = int(payload.get("status") or 0)
        content = payload.get("content")
        if not isinstance(content, str):
            content = json.dumps(content) if content is not None else ""
        content = _FRONT_MATTER.sub("", content, count=1).strip()[:MAX_TOOL_RESULT_CHARS]
        if status >= 400 or not content:
            return json.dumps({"error": f"page could not be fetched (status {status or 'none'})"})
        headings = (line[2:].strip() for line in content.splitlines() if line.startswith("# "))
        title = next(headings, url)
        if url not in self.read_urls:
            self.read_urls.append(url)
        source_id = self.registry.register(url, title, content)
        return f"## [{source_id}] [{title}]({url})\n\n{content}"


def chat(
    client: httpx.Client,
    model: str,
    messages: list[dict],
    force_submit: bool,
    tools: list[dict] | None = None,
) -> dict:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools if tools is not None else TOOLS,
        "usage": {"include": True},
        "temperature": 0,
    }
    if force_submit:
        body["tool_choice"] = {"type": "function", "function": {"name": "submit_answer"}}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = client.post(OPENROUTER_URL, json=body)
            r.raise_for_status()
            data = r.json()
            # OpenRouter can return HTTP 200 with an error body and no "choices"
            # (a provider hiccup or rate limit surfaced in-band). Treat that as a
            # transient and retry rather than crashing on data["choices"].
            if "choices" in data:
                return data
            last_err = RuntimeError(f"no choices in response: {json.dumps(data)[:200]}")
        except (httpx.HTTPStatusError, httpx.TransportError) as e:  # transient 429/5xx/network
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status < 500 and status not in (400, 429):
                raise
            if status == 400:
                # A 400 from OpenRouter is usually the upstream provider rejecting the
                # request shape (tool-call sequence, tool_choice). It can be routing-
                # dependent, so retry like a transient; carry the body for diagnosis.
                body_text = getattr(e.response, "text", "")[:400]
                last_err = RuntimeError(f"400 from OpenRouter: {body_text}")
            else:
                last_err = e
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"OpenRouter unavailable after retries: {last_err}")


CLOSING_INSTRUCTION = (
    "You have no more tool calls. Write your final answer now in markdown, using only "
    "what you have already read, with the source id (for example [c3]) after each "
    "factual claim and [unverified] after any claim you could not find in a page."
)


def _add_usage(metrics: dict[str, Any], data: dict[str, Any]) -> None:
    usage = data.get("usage") or {}
    metrics["input_tokens"] += usage.get("prompt_tokens") or 0
    metrics["output_tokens"] += usage.get("completion_tokens") or 0
    metrics["cost_usd"] += usage.get("cost") or 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--manifold", type=int, required=True)
    ap.add_argument("--disable-ontology", action="store_true")
    ap.add_argument("--ontology-mode", choices=["off", "gated", "always"], default=None)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument(
        "--services",
        action="store_true",
        help="Give the model the services_near tool: ontology entities near a place "
        "(edge-search /v1/entities/near).",
    )
    ap.add_argument(
        "--tags",
        default="",
        help="comma-separated tag names; restricts retrieval to pages carrying them",
    )
    ap.add_argument("--tag-mode", choices=["any", "all"], default=None)
    args = ap.parse_args()

    request = json.loads(sys.stdin.read())
    prompt = request.get("prompt", "")
    if not prompt:
        print("empty prompt", file=sys.stderr)
        return 1

    es = EdgeSearch(
        os.environ.get("EDGE_SEARCH_URL", "http://127.0.0.1:8096"),
        os.environ["EDGE_SEARCH_API_KEY"],
        args.manifold,
        args.disable_ontology,
        args.ontology_mode,
        [t.strip() for t in args.tags.split(",") if t.strip()],
        args.tag_mode,
    )
    or_client = httpx.Client(
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        timeout=180.0,
    )

    tools = TOOLS + [SERVICES_TOOL] if args.services else TOOLS
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT + (SERVICES_PROMPT if args.services else "")},
        {"role": "user", "content": prompt},
    ]
    trace: list[dict] = [
        {"action": "runner", "version": RUNNER_VERSION, "services": bool(args.services)}
    ]
    metrics = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "tool_calls": 0}
    answer: str | None = None
    citations: list[dict] = []
    submitted = False
    resubmits = 0

    rollbacks = 0
    # Retrieval has --max-steps turns; a rejected submission earns up to MAX_RESUBMITS
    # further turns, all with submit_answer forced, so the fix does not eat retrieval.
    for step in range(args.max_steps + MAX_RESUBMITS):
        force_submit = step >= args.max_steps - 1
        try:
            response = chat(or_client, args.model, messages, force_submit, tools)
        except RuntimeError as e:
            # Gemini (via OpenRouter) can leave the conversation carrying a corrupted
            # "thought signature" after an aborted response, and every retry that
            # re-sends it fails with the same 400. Recovery ladder: roll back the last
            # assistant turn and redo the step; then strip every reasoning field from
            # the history (the signatures live there) and retry; then, if the model has
            # read something, close with a no-tools answer over the stripped history.
            if "thought signature" not in str(e).lower():
                raise
            if rollbacks == 0:
                while messages and messages[-1].get("role") != "assistant":
                    messages.pop()
                if messages and messages[-1].get("role") == "assistant":
                    messages.pop()
                rollbacks += 1
                trace.append({"step": step, "action": "rollback_after_corrupted_signature"})
                continue
            if rollbacks == 1:
                reasoning_keys = (
                    "reasoning",
                    "reasoning_details",
                    "reasoning_content",
                    "thought_signature",
                    "extra_content",
                )
                for m in messages:
                    if m.get("role") == "assistant":
                        for k in reasoning_keys:
                            m.pop(k, None)
                rollbacks += 1
                trace.append(
                    {"step": step, "action": "stripped_reasoning_after_corrupted_signature"}
                )
                continue
            if es.ok_calls and answer is None:
                trace.append({"step": step, "action": "closing_answer_after_corrupted_signature"})
                keep = ("role", "content", "tool_calls", "tool_call_id", "name")
                plain = [{k: v for k, v in m.items() if k in keep} for m in messages]
                closing = plain + [{"role": "user", "content": CLOSING_INSTRUCTION}]
                body = {"model": args.model, "messages": closing, "usage": {"include": True}}
                r = or_client.post(OPENROUTER_URL, json={**body, "temperature": 0})
                r.raise_for_status()
                data = r.json()
                _add_usage(metrics, data)
                choice = (data.get("choices") or [{}])[0]
                answer = ((choice.get("message") or {}).get("content") or "").strip()
                if answer:
                    break
            raise
        _add_usage(metrics, response)

        message = response["choices"][0]["message"]
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            text = (message.get("content") or "").strip()
            if answer is None and text:
                # Keep the plain text as a fallback answer, but nudge once for a
                # proper submit_answer with quotes for the sources it marked.
                answer = text
                trace.append({"step": step, "action": "plain_answer_nudged"})
                messages.append(
                    {
                        "role": "user",
                        "content": "Call the submit_answer tool now with the full answer "
                        "(keep the [cN] source markers after each claim) and, in "
                        "citations, a verbatim quote from the page for every source id "
                        "you cited.",
                    }
                )
                continue
            if text:
                answer = text
            trace.append({"step": step, "action": "plain_answer"})
            break

        done = False
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                fn_args = {}
            metrics["tool_calls"] += 1

            if name == "submit_answer":
                candidate = (fn_args.get("answer") or "").strip()
                built, problems, stats = validate_submission(
                    candidate, fn_args.get("citations"), es.registry
                )
                if problems and resubmits < MAX_RESUBMITS:
                    resubmits += 1
                    trace.append(
                        {
                            "step": step,
                            "action": "submit_rejected",
                            "problems": problems[:12],
                            **stats,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(
                                {
                                    "accepted": False,
                                    "problems": problems,
                                    "instruction": "Fix these and call submit_answer again "
                                    "with the complete answer and all citations.",
                                }
                            ),
                        }
                    )
                    continue
                answer = candidate
                citations = built
                trace.append(
                    {
                        "step": step,
                        "action": "submit_answer",
                        "citations": len(citations),
                        "resubmits": resubmits,
                        "problems_at_accept": problems[:12],
                        **stats,
                    }
                )
                submitted = True
                done = True
                continue

            try:
                if name == "search":
                    result = es.search(fn_args.get("query", ""), fn_args.get("top_k", 8))
                elif name == "read":
                    result = es.read(fn_args.get("query", ""), fn_args.get("top_k", 4))
                elif name == "read_url":
                    result = es.read_url(fn_args.get("url", ""))
                elif name == "services_near" and args.services:
                    result = es.services_near(
                        fn_args.get("category", ""),
                        fn_args.get("place", ""),
                        fn_args.get("radius_km", 10),
                    )
                else:
                    result = json.dumps({"error": f"unknown tool {name}"})
            except httpx.HTTPError as e:
                result = json.dumps({"error": f"tool failed: {e}"})
            entry: dict[str, Any] = {"step": step, "action": name, "args": fn_args}
            if name in ("search", "read", "services_near") and es.last_urls:
                entry["urls"] = es.last_urls
            es.last_urls = []
            trace.append(entry)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result[:MAX_TOOL_RESULT_CHARS],
                }
            )
        if done:
            break

    # Integrity guard: this is an edge-search-backed system, so an answer is
    # only valid if at least one retrieval actually succeeded. Zero successful
    # calls means the backend was unreachable (every attempt errored) or the
    # model never grounded — either way, fail the trial instead of emitting a
    # memory-only answer that would score as if edge-search had answered.
    if es.ok_calls == 0:
        if es.searches:
            print(
                f"all {es.searches} edge-search retrieval call(s) failed "
                "(backend unreachable?); failing trial to avoid a memory-only answer",
                file=sys.stderr,
            )
        else:
            print(
                "no edge-search retrieval performed; failing trial to avoid a memory-only answer",
                file=sys.stderr,
            )
        print("trace: " + json.dumps(trace)[:4000], file=sys.stderr)
        return 1

    if not answer and es.ok_calls:
        # The step budget was spent on retrieval and the forced submit_answer did
        # not produce one (some providers ignore tool_choice). One closing call with
        # no tools available makes the model write the answer from what it has read;
        # citations come from the markers it places. Same budget for every arm.
        trace.append({"action": "final_answer_without_tools"})
        try:
            closing = list(messages) + [{"role": "user", "content": CLOSING_INSTRUCTION}]
            body = {"model": args.model, "messages": closing, "usage": {"include": True}}
            r = or_client.post(OPENROUTER_URL, json={**body, "temperature": 0})
            r.raise_for_status()
            data = r.json()
            _add_usage(metrics, data)
            choice = (data.get("choices") or [{}])[0]
            answer = ((choice.get("message") or {}).get("content") or "").strip()
        except (httpx.HTTPError, ValueError, KeyError) as e:
            print(f"closing call failed: {e}", file=sys.stderr)
    if not answer:
        print("agent produced no answer", file=sys.stderr)
        print("trace: " + json.dumps(trace)[:4000], file=sys.stderr)
        return 1
    if not submitted:
        # A plain or closing answer: attribute from its markers; no quotes were given.
        citations = citations_from_markers(answer, es.registry)
        if citations:
            trace.append({"action": "citations_from_markers", "count": len(citations)})
    if not citations and es.read_urls:
        citations = [{"url": url} for url in es.read_urls[:6]]
        trace.append({"action": "citations_fallback_from_read", "count": len(citations)})
    trace.append(
        {
            "action": "sources",
            "read": len(es.registry.order),
            "cited": len(citations),
            "with_quote": sum(1 for c in citations if c.get("quote")),
            "unverified_marks": len(re.findall(r"\[unverified\]", answer)),
        }
    )
    # Everything any search or read put in front of the model, first-seen order.
    trace.append({"action": "surfaced", "count": len(es.surfaced), "urls": es.surfaced[:400]})
    # The pages read, so breadth of consideration can be measured without replaying retrieval.
    trace.append(
        {
            "action": "sources_read",
            "pages": [
                {"id": src["id"], "url": url, "title": src["title"]}
                for url, src in es.registry.by_url.items()
            ],
        }
    )

    print(
        json.dumps(
            {
                "answer": answer,
                "citations": citations,
                "metrics": {**metrics, "searches": es.searches},
                "trace": trace,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
