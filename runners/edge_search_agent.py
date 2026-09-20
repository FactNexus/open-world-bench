#!/usr/bin/env python3
"""OWRB command-adapter runner: a tool-calling agent over edge-search.

The model (any OpenAI-compatible chat model via OpenRouter) is given two
retrieval tools backed by an edge-search manifold — ranked URL search and a
content pack — plus a ``submit_answer`` tool that ends the episode. One
script serves every model+edge-search system; fairness between systems comes
from varying only argv (model, manifold, --disable-ontology, --tags), never code.

    stdin  <- {"scenario_instance_id": ..., "prompt": ..., "answer_contract": {...}}
    stdout -> {"answer": ..., "citations": [...], "metrics": {...}, "trace": [...]}

Environment: OPENROUTER_API_KEY, EDGE_SEARCH_URL (default
http://127.0.0.1:8096 — the the edge-search host SSH tunnel), EDGE_SEARCH_API_KEY.

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

SYSTEM_PROMPT = """\
You are a careful research assistant answering questions about Australian \
travel and tourism using a curated search index of Australian tourism sites.

Rules:
- Use the `search` tool to find relevant pages and the `read` tool to read \
their content before answering. Do not answer from memory alone; ground every \
factual claim in pages you actually read.
- Reformulate and retry searches if the first results are weak. Prefer \
several focused searches over one broad one.
- When you have enough evidence, call `submit_answer`. The `citations` list \
must contain only URLs whose content you actually used, and every operational \
detail in the answer (opening hours, prices, distances, accessibility, \
seasonal closures) must be supported by one of them.
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
            "resolved to (when available).",
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
            "tourism index (a markdown pack of the best-matching pages, with "
            "source URLs). Use after `search`, with a focused query, to read "
            "what the pages actually say.",
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
            "name": "submit_answer",
            "description": "Submit the final answer and finish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "The complete final answer."},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "title": {"type": "string"},
                            },
                            "required": ["url"],
                        },
                        "description": "URLs actually used as evidence.",
                    },
                },
                "required": ["answer", "citations"],
            },
        },
    },
]


class EdgeSearch:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        manifold_id: int,
        disable_ontology: bool,
        ontology_mode: str | None = None,
        tags: list[str] | None = None,
        tag_mode: str | None = None,
    ):
        self.manifold_id = manifold_id
        self.disable_ontology = disable_ontology
        self.ontology_mode = ontology_mode
        # Tag scoping: only pages carrying these tags are retrievable. Same
        # mechanism the MCP server applies to a partner session server-side.
        self.tags = [t for t in (tags or []) if t]
        self.tag_mode = tag_mode
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )
        self.searches = 0
        # Retrieval calls that returned HTTP 200. Zero of these across the whole
        # episode means edge-search was unreachable (every call errored) — the
        # trial must fail rather than let the model answer from memory.
        self.ok_calls = 0
        # URLs whose content the model has actually seen via `read` —
        # citation fallback when it answers without calling submit_answer.
        self.read_urls: list[str] = []

    def search(self, query: str, top_k: int = 8) -> str:
        self.searches += 1
        r = self.client.post(
            "/v1/urls",
            json={
                "manifold_id": self.manifold_id,
                "query": query,
                "top_k": max(1, min(int(top_k or 8), 20)),
                "disable_ontology": self.disable_ontology,
                **({"ontology_mode": self.ontology_mode} if self.ontology_mode else {}),
                **({"tags": self.tags} if self.tags else {}),
                **({"tag_mode": self.tag_mode} if self.tags and self.tag_mode else {}),
            },
        )
        r.raise_for_status()
        self.ok_calls += 1
        data = r.json()
        out: dict[str, Any] = {
            "results": [
                {"url": x["url"], "title": x.get("title"), "score": round(x["score"], 3)}
                for x in data.get("results", [])
            ]
        }
        if data.get("concepts"):
            out["resolved_concepts"] = [c.get("label") for c in data["concepts"] if c.get("label")]
        return json.dumps(out)

    def read(self, query: str, top_k: int = 4) -> str:
        self.searches += 1
        r = self.client.post(
            "/v1/content",
            json={
                "manifold_id": self.manifold_id,
                "query": query,
                "top_k": max(1, min(int(top_k or 4), 8)),
                "token_limit": 5000,
                "disable_ontology": self.disable_ontology,
                **({"ontology_mode": self.ontology_mode} if self.ontology_mode else {}),
                **({"tags": self.tags} if self.tags else {}),
                **({"tag_mode": self.tag_mode} if self.tags and self.tag_mode else {}),
            },
        )
        r.raise_for_status()
        self.ok_calls += 1
        content = r.json().get("content", "")
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        content = content[:MAX_TOOL_RESULT_CHARS]
        for url in re.findall(r"\]\((https?://[^)\s]+)\)", content):
            if url not in self.read_urls:
                self.read_urls.append(url)
        return content


def chat(client: httpx.Client, model: str, messages: list[dict], force_submit: bool) -> dict:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--manifold", type=int, required=True)
    ap.add_argument("--disable-ontology", action="store_true")
    ap.add_argument("--ontology-mode", choices=["off", "gated", "always"], default=None)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--tags", default="", help="comma-separated tag names; restricts retrieval to pages carrying them")
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

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    trace: list[dict] = []
    metrics = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "tool_calls": 0}
    answer: str | None = None
    citations: list[dict] = []

    rollbacks = 0
    for step in range(args.max_steps):
        force_submit = step == args.max_steps - 1
        try:
            response = chat(or_client, args.model, messages, force_submit)
        except RuntimeError as e:
            # Gemini (via OpenRouter) can leave the conversation carrying a corrupted
            # "thought signature" after an aborted response; every retry that re-sends
            # that turn fails with the same 400. Roll back to before the last assistant
            # turn and let the model redo that step (at most twice per episode).
            if "thought signature" in str(e).lower() and rollbacks < 2:
                while messages and messages[-1].get("role") != "assistant":
                    messages.pop()
                if messages and messages[-1].get("role") == "assistant":
                    messages.pop()
                rollbacks += 1
                trace.append({"step": step, "action": "rollback_after_corrupted_signature"})
                continue
            raise
        usage = response.get("usage") or {}
        metrics["input_tokens"] += usage.get("prompt_tokens") or 0
        metrics["output_tokens"] += usage.get("completion_tokens") or 0
        metrics["cost_usd"] += usage.get("cost") or 0.0

        message = response["choices"][0]["message"]
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            text = (message.get("content") or "").strip()
            if answer is None and text:
                # Keep the plain text as a fallback answer, but nudge once
                # for a proper submit_answer with citations.
                answer = text
                trace.append({"step": step, "action": "plain_answer_nudged"})
                messages.append(
                    {
                        "role": "user",
                        "content": "Call the submit_answer tool now with your "
                        "final answer and the citations (URLs) you used.",
                    }
                )
                continue
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
                answer = (fn_args.get("answer") or "").strip()
                citations = [
                    c
                    for c in fn_args.get("citations") or []
                    if isinstance(c, dict) and c.get("url")
                ]
                trace.append({"step": step, "action": "submit_answer", "citations": len(citations)})
                done = True
                break

            try:
                if name == "search":
                    result = es.search(fn_args.get("query", ""), fn_args.get("top_k", 8))
                elif name == "read":
                    result = es.read(fn_args.get("query", ""), fn_args.get("top_k", 4))
                else:
                    result = json.dumps({"error": f"unknown tool {name}"})
            except httpx.HTTPError as e:
                result = json.dumps({"error": f"tool failed: {e}"})
            trace.append({"step": step, "action": name, "args": fn_args})
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
                "no edge-search retrieval performed; failing trial to avoid a "
                "memory-only answer",
                file=sys.stderr,
            )
        print("trace: " + json.dumps(trace)[:4000], file=sys.stderr)
        return 1

    if not answer and es.ok_calls:
        # The step budget was spent on retrieval and the forced submit_answer did
        # not produce one (some providers ignore tool_choice). One closing call with
        # no tools available makes the model write the answer from what it has read;
        # citations come from the pages it actually read. Same budget for every arm.
        trace.append({"action": "final_answer_without_tools"})
        try:
            closing = list(messages) + [{"role": "user", "content": "You have no more tool calls. Write your final answer now in markdown, using only what you have already retrieved, and list the URLs you relied on."}]
            body = {"model": args.model, "messages": closing, "usage": {"include": True}, "temperature": 0}
            r = or_client.post(OPENROUTER_URL, json=body); r.raise_for_status(); data = r.json()
            usage = data.get("usage") or {}
            metrics["input_tokens"] += usage.get("prompt_tokens") or 0
            metrics["output_tokens"] += usage.get("completion_tokens") or 0
            metrics["cost_usd"] += usage.get("cost") or 0.0
            answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            answer = answer.strip()
        except (httpx.HTTPError, ValueError, KeyError) as e:
            print(f"closing call failed: {e}", file=sys.stderr)
    if not answer:
        print("agent produced no answer", file=sys.stderr)
        print("trace: " + json.dumps(trace)[:4000], file=sys.stderr)
        return 1
    if not citations and es.read_urls:
        citations = [{"url": url} for url in es.read_urls[:6]]
        trace.append({"action": "citations_fallback_from_read", "count": len(citations)})

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
