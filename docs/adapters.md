# System adapters

## Purpose

Adapters make unlike systems appear alike to the benchmark. They should be thin translation layers, not agent frameworks.

## Normalised request

```json
{
  "scenario_instance_id": "...",
  "prompt": "...",
  "answer_contract": {
    "citations_required": true,
    "format": "markdown"
  },
  "timeout_seconds": 300
}
```

## Normalised response

```json
{
  "status": "completed",
  "answer": "...",
  "citations": [
    {
      "id": "cite-1",
      "url": "https://example.org/page",
      "title": "Example page"
    }
  ],
  "metrics": {
    "latency_ms": 12345,
    "input_tokens": 1000,
    "output_tokens": 800,
    "tool_calls": 4
  },
  "trace_events": []
}
```

## Provider-native web search

Provider-specific search tool configuration changes over time. Keep it in adapter packages and pin/test supported SDK versions. The core should only see the normalised result.

## Cost reporting

When a provider does not return a charge, adapters compute `cost_usd` via `compute_cost_usd` from system-declared rates in `settings.cost`: `input_per_mtok` and `output_per_mtok` for tokens, plus an optional `search_per_1k` surcharge for web-search / grounding tool fees (which often dominate token cost). Rates are configuration, not code, so they can be updated per system as prices change. `command` runners are opaque to the core and must report `cost_usd` in their own `metrics`.

## Generic HTTP adapter

POST the request to a configured endpoint. Support mappings from JSONPath-like expressions to answer, citations, trace, and metrics.

## Command adapter

Invoke a configured executable with a request JSON file and require it to write a response JSON file. Execute in a sandbox where practical.

## Manual adapter

Create a run from a human-captured answer. Manual results participate in quality comparison, but efficiency comparisons must clearly mark missing or user-entered metrics.
