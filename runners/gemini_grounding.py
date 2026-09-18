#!/usr/bin/env python3
"""OWRB command-adapter runner: Gemini with native Google Search grounding.

Approximates the consumer Gemini product: one generateContent call with the
``google_search`` tool enabled, citations taken from the response's
groundingMetadata (the sources Google's own search actually surfaced) — not
a third-party web plugin.

    stdin  <- {"scenario_instance_id": ..., "prompt": ..., "answer_contract": {...}}
    stdout -> {"answer": ..., "citations": [...], "metrics": {...}, "trace": [...]}

Environment: GEMINI_API_KEY (Google AI Studio).

Smoke test:
    echo '{"prompt": "What are the top attractions in Port Macquarie?", \
"answer_contract": {}}' | python runners/gemini_grounding.py --model gemini-flash-latest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _redact(message: str) -> str:
    """Strip the API key from error text — it rides in the request URL as ?key=."""
    return re.sub(r"key=[^&\s'\"]+", "key=***", message)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-flash-latest")
    # Pricing (USD) so the runner can self-report cost_usd. Defaults are the
    # gemini-3.6-flash introductory rates + Google Search grounding list price
    # (2026); override per system/run as prices change.
    ap.add_argument("--price-in-per-mtok", type=float, default=0.75)
    ap.add_argument("--price-out-per-mtok", type=float, default=3.75)
    ap.add_argument("--price-per-1k-grounding", type=float, default=14.0)
    args = ap.parse_args()

    request = json.loads(sys.stdin.read())
    prompt = request.get("prompt", "")
    if not prompt:
        print("empty prompt", file=sys.stderr)
        return 1

    api_key = os.environ["GEMINI_API_KEY"]
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0},
    }

    response = None
    last_err: Exception | None = None
    with httpx.Client(timeout=300.0) as client:
        for attempt in range(3):
            try:
                r = client.post(
                    f"{BASE}/{args.model}:generateContent",
                    params={"key": api_key},
                    json=body,
                )
                r.raise_for_status()
                response = r.json()
                break
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status < 500 and status != 429:
                    print(f"gemini error: {_redact(str(e))}", file=sys.stderr)
                    return 1
                last_err = e
                time.sleep(2 ** (attempt + 1))
    if response is None:
        print(f"gemini unavailable after retries: {_redact(str(last_err))}", file=sys.stderr)
        return 1

    try:
        candidate = response["candidates"][0]
    except (KeyError, IndexError):
        print(f"no candidates in response: {json.dumps(response)[:500]}", file=sys.stderr)
        return 1

    answer = "".join(
        part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
    ).strip()
    if not answer:
        # Grounded generations occasionally come back with no text part (a
        # finishReason such as SAFETY or RECITATION, or an empty candidate). One
        # fresh attempt recovers most of these; if it is still empty, fail with
        # the reason recorded.
        reason = candidate.get("finishReason")
        try:
            with httpx.Client(timeout=300.0) as client:
                r = client.post(f"{BASE}/{args.model}:generateContent", params={"key": api_key}, json=body)
                r.raise_for_status()
                retry = r.json()
            cand2 = retry["candidates"][0]
            answer = "".join(part.get("text", "") for part in cand2.get("content", {}).get("parts", [])).strip()
            if answer:
                candidate, response = cand2, retry
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            print(f"retry after empty answer failed: {_redact(str(e))}", file=sys.stderr)
    if not answer:
        print(f"empty answer (finishReason={reason})", file=sys.stderr)
        return 1

    grounding = candidate.get("groundingMetadata") or {}
    citations: list[dict] = []
    seen: set[str] = set()
    # Grounding chunks cite Google's redirect links (vertexaisearch.cloud.google.com),
    # which the evaluator cannot read. Resolve each to the page it lands on so the
    # citation names the real source; keep the redirect if resolution fails.
    resolver = httpx.Client(follow_redirects=True, timeout=10.0, headers={"user-agent": "owrb-runner/0.1"})
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        url = web.get("uri")
        if not url:
            continue
        if "vertexaisearch.cloud.google.com" in url:
            try:
                head = resolver.head(url)
                if head.url and str(head.url) != url:
                    url = str(head.url)
            except httpx.HTTPError:
                pass
        if url not in seen:
            seen.add(url)
            citation: dict = {"url": url}
            if web.get("title"):
                citation["title"] = web["title"]
            citations.append(citation)
    resolver.close()

    usage = response.get("usageMetadata") or {}
    searches = len(grounding.get("webSearchQueries") or [])
    input_tokens = usage.get("promptTokenCount", 0)
    # Gemini bills thinking tokens as output, so fold them into output_tokens for
    # an accurate billable count (thoughts often exceed the visible answer).
    output_tokens = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    cost_usd = round(
        (input_tokens * args.price_in_per_mtok + output_tokens * args.price_out_per_mtok) / 1e6
        + searches * args.price_per_1k_grounding / 1000,
        6,
    )
    print(
        json.dumps(
            {
                "answer": answer,
                "citations": citations,
                "metrics": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "searches": searches,
                    "cost_usd": cost_usd,
                },
                "trace": [
                    {
                        "step": 0,
                        "action": "generate_grounded",
                        "web_search_queries": grounding.get("webSearchQueries") or [],
                    }
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
