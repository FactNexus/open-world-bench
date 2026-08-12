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
import sys
import time

import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-flash-latest")
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
                    print(f"gemini error: {e}", file=sys.stderr)
                    return 1
                last_err = e
                time.sleep(2 ** (attempt + 1))
    if response is None:
        print(f"gemini unavailable after retries: {last_err}", file=sys.stderr)
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
        print("empty answer", file=sys.stderr)
        return 1

    grounding = candidate.get("groundingMetadata") or {}
    citations: list[dict] = []
    seen: set[str] = set()
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        url = web.get("uri")
        if url and url not in seen:
            seen.add(url)
            citation: dict = {"url": url}
            if web.get("title"):
                citation["title"] = web["title"]
            citations.append(citation)

    usage = response.get("usageMetadata") or {}
    searches = len(grounding.get("webSearchQueries") or [])
    print(
        json.dumps(
            {
                "answer": answer,
                "citations": citations,
                "metrics": {
                    "input_tokens": usage.get("promptTokenCount", 0),
                    "output_tokens": usage.get("candidatesTokenCount", 0),
                    "searches": searches,
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
