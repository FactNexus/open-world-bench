# System templates — one per open-world discovery strategy

An OWRB *system* is a black box: it receives the frozen scenario prompt and
returns an answer with citations and metrics. Scoring is strategy-agnostic —
the shared evidence bundle and blind judge don't care *how* the answer was
produced — so these templates cover every way an agent can reach open-world
content by changing only the adapter and its settings.

Copy a file, edit the marked fields, add it to a suite's `systems:` list, and
validate. Mix several in one suite to compare strategies head-to-head on the
same scenarios, with cost and latency reported alongside quality.

| # | Strategy | File | Adapter | You edit |
|---|----------|------|---------|----------|
| 1 | Own trained weights (closed book) | `parametric-only.yaml` | `openrouter` | model id |
| 2 | Own private database (RAG) | `private-rag.yaml` | `generic_http` | endpoint |
| 3 | Direct search service | `native-search.yaml` | `openrouter` | model id |
| 4 | Service via MCP / CLI / API | `external-tool.yaml` | `command` | runner argv |
| 5 | Visit & scan public web | `web-browse.yaml` | `command` | runner argv |
| 6 | Combination of the above | `hybrid.yaml` | `command` | runner argv |

Strategies 1 and 3 are the same file with `search_enabled` flipped. Strategies
2, 4, 5, and 6 are all the same bring-your-own-agent contract behind two
adapters (`generic_http` over HTTP, `command` over stdin/stdout). The
`runners/*.py` scripts named in the `command` templates are yours to write —
they are not shipped.

## The bring-your-own-agent contract (templates 2, 4, 5, 6)

`generic_http` sends this as the POST body; `command` sends it on stdin:

```jsonc
// in  ->  your service / runner
{"scenario_instance_id": "...", "prompt": "...", "answer_contract": {...}}
// out <-  what you return
{"answer": "...",
 "citations": [{"url": "...", "title": "...", "answer_spans": ["..."]}],
 "metrics":  {"cost_usd": 0.0, "latency_ms": 0, "searches": 0,
              "retrieved_context_tokens": 0},
 "trace":    [{"...": "..."}]}
```

- `answer` is required and must be a non-empty string.
- `citations` may be bare URL strings or objects; only `url` is required.
  These URLs are what the shared evidence bundle fetches and the judge scores
  support against — cite the sources you actually used.
- `metrics` is any subset of `RunMetrics` (`latency_ms`,
  `time_to_first_token_ms`, `input_tokens`, `output_tokens`,
  `retrieved_context_tokens`, `tool_calls`, `searches`, `unique_sources`,
  `cost_usd`). Unknown keys are ignored.
- `trace` is optional and free-form (a list of objects).

## Reference runner (strategies 4, 5, 6)

`runners/reference_runner.py` is a runnable starting point for the `command`
templates. It handles the stdin/stdout contract, timing, and error handling —
you replace one function, `discover(prompt, answer_contract)`, with your agent
(attach an MCP server, call a CLI/API, browse the web, or orchestrate several).

```bash
# smoke-test the plumbing before writing any discovery logic
echo '{"prompt": "hi", "answer_contract": {}}' \
    | python systems/templates/runners/reference_runner.py

# then copy it to the path your template's command points at, and edit discover()
cp systems/templates/runners/reference_runner.py runners/agent.py
```

The stub returns a clearly-marked `PLACEHOLDER` answer so you can confirm
`owrb run` invokes your runner and parses its output end-to-end first. Any
language works — this is just the reference shape; honour the contract above.

## Reporting your metrics

Cost and latency flow into the comparison and the quality/cost Pareto frontier
from `metrics` — so "how much / how long" is answered as long as your system
reports them.

- `generic_http` fills `latency_ms` if you omit it and computes `cost_usd`
  from `settings.cost.{input_per_mtok,output_per_mtok}` when you don't report
  a cost yourself.
- **`command` fills `latency_ms` but does not compute cost** — a runner must
  put `cost_usd` in `metrics` if you want cost reported.
- The `openrouter` gateway reports real `cost_usd` from its usage payload
  automatically.

## The `strategy` label

Each template declares a `strategy:` — one of `parametric`, `native_search`,
`private_index`, `external_tool`, `web_browse`, or `hybrid`. `compare` and
`report` read it to group results: the dashboard adds a **Quality by strategy**
rollup (trials pooled across every system sharing a strategy), tags each system
and each Pareto-frontier entry with its strategy, and exports `by-strategy.csv`.
That is what turns "N systems" into "here is the winning *strategy* per suite
and the cost/latency tradeoff between strategies."

`strategy` is the comparison axis; the `capabilities:` block still describes the
underlying mechanism (and remains a useful honest label — e.g. `mcp: true`).
Systems that omit `strategy` are grouped under `unspecified`.

## Using a template

```bash
# validate a copy
uv run owrb systems validate systems/templates/native-search.yaml

# then reference it from a suite's systems: list, e.g.
#   systems:
#     - systems/templates/parametric-only.yaml
#     - systems/templates/native-search.yaml
#     - systems/templates/private-rag.yaml
```
