#!/usr/bin/env python3
"""Reference runner for OWRB ``command``-adapter systems (strategies 4/5/6).

Copy this file into your own ``runners/`` directory (the path the template's
``settings.command`` points at), then replace ``discover()`` with your agent:
attach an MCP server, call a CLI or API, browse the live web, or orchestrate
several of those. Everything else — the stdin/stdout contract, timing, error
handling — stays the same, so you can wire it into ``owrb run`` and smoke-test
the plumbing before writing any real discovery logic.

Contract (see systems/templates/README.md):

    stdin  <- {"scenario_instance_id": ..., "prompt": ..., "answer_contract": {...}}
    stdout -> {"answer": ..., "citations": [...], "metrics": {...}, "trace": [...]}

Exit non-zero to record the trial as failed; the OWRB runner preserves failures
and never retries. Any language works — this is just the reference shape.

Smoke test:

    echo '{"prompt": "hi", "answer_contract": {}}' \
        | python systems/templates/runners/reference_runner.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any


def discover(prompt: str, answer_contract: dict[str, Any]) -> dict[str, Any]:
    """REPLACE ME with your strategy. This stub returns a placeholder answer.

    ``answer_contract`` describes what a good answer looks like (format, required
    fields, citation expectations) — read it to shape your output. Return a dict:

    - ``answer``    the final text answer (required, non-empty string);
    - ``citations`` the URLs you actually used, each a bare string or
      ``{"url": ..., "title"?: ..., "answer_spans"?: [...]}`` — these are what
      the shared evidence bundle fetches and the judge scores support against;
    - metric hints (all optional, all reported to the dashboard):
      ``cost_usd``, ``searches``, ``input_tokens``, ``output_tokens``,
      ``retrieved_context_tokens``, ``tool_calls``. The command adapter does not
      compute cost, so report ``cost_usd`` here if you want it in efficiency.
    - ``trace`` an optional list of free-form step objects for the audit view.

    Example of a real return:

        return {
            "answer": "The lookout closes at 5pm.",
            "citations": [{"url": "https://example.gov.au/lookout", "title": "Lookout"}],
            "searches": 2,
            "cost_usd": 0.0031,
        }
    """
    return {
        "answer": (
            "PLACEHOLDER — replace discover() in reference_runner.py with your "
            f"MCP / CLI / API / browse / hybrid agent. Prompt was: {prompt[:120]}"
        ),
        "citations": [],
        "cost_usd": 0.0,
        "searches": 0,
        "trace": [{"step": "stub", "note": "no discovery performed"}],
    }


_METRIC_KEYS = (
    "cost_usd",
    "searches",
    "input_tokens",
    "output_tokens",
    "retrieved_context_tokens",
    "tool_calls",
)


def main() -> int:
    try:
        request = json.load(sys.stdin)
        prompt = request["prompt"]
        answer_contract = request.get("answer_contract", {})
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"invalid request on stdin: {error}", file=sys.stderr)
        return 1

    started = time.monotonic()
    try:
        result = discover(prompt, answer_contract)
    except Exception as error:
        # Surface any agent failure to the runner as a failed trial.
        print(f"discovery failed: {error}", file=sys.stderr)
        return 1
    latency_ms = int((time.monotonic() - started) * 1000)

    answer = result.get("answer", "")
    if not isinstance(answer, str) or not answer.strip():
        print("discover() returned no answer", file=sys.stderr)
        return 1

    metrics: dict[str, Any] = {"latency_ms": latency_ms}
    for key in _METRIC_KEYS:
        if result.get(key) is not None:
            metrics[key] = result[key]

    json.dump(
        {
            "answer": answer,
            "citations": result.get("citations", []),
            "metrics": metrics,
            "trace": result.get("trace", []),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
