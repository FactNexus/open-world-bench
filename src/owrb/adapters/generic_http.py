"""Generic HTTP adapter: POST a normalised request, map the JSON response.

System settings:

- ``endpoint``: target URL; ``${VAR}`` placeholders resolve from the environment.
- ``method``: HTTP method, default POST.
- ``headers``: optional extra headers (values support ``${VAR}``).
- ``response_mapping``: dot-paths (``$.answer``) locating answer, citations,
  metrics, and trace in the response body.

If the system declares an ``api_key`` environment reference, its value is sent
as a bearer token. Secret values never appear in the returned RunResult.
"""

from __future__ import annotations

import re
from typing import Any

from owrb.adapters.base import (
    AdapterError,
    compute_cost_usd,
    resolve_environment_value,
    substitute_environment,
    utc_now,
)
from owrb.models import Citation, RunMetrics, RunRequest, RunResult

_PATH_TOKEN = re.compile(r"\.([A-Za-z0-9_-]+)|\[(\d+)\]")


def resolve_json_path(payload: Any, path: str) -> Any:
    """Resolve a minimal JSONPath subset: ``$.field.nested[0].value``."""
    if not path.startswith("$"):
        raise AdapterError(f"response mapping path must start with '$': {path!r}")
    position = 1
    current = payload
    while position < len(path):
        match = _PATH_TOKEN.match(path, position)
        if match is None:
            raise AdapterError(f"invalid response mapping path: {path!r}")
        key, index = match.group(1), match.group(2)
        if key is not None:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        else:
            if not isinstance(current, list) or int(index) >= len(current):
                return None
            current = current[int(index)]
        position = match.end()
    return current


def _parse_citations(raw: Any) -> list[Citation]:
    citations: list[Citation] = []
    if not isinstance(raw, list):
        return citations
    for position, item in enumerate(raw, start=1):
        if isinstance(item, str):
            citations.append(Citation(id=f"c{position}", url=item))
        elif isinstance(item, dict) and "url" in item:
            citations.append(
                Citation(
                    id=str(item.get("id", f"c{position}")),
                    url=str(item["url"]),
                    title=item.get("title"),
                    source_name=item.get("source_name"),
                    answer_spans=[str(span) for span in item.get("answer_spans", [])],
                )
            )
    return citations


def _parse_metrics(raw: Any) -> RunMetrics:
    if not isinstance(raw, dict):
        return RunMetrics()
    known_fields = set(RunMetrics.model_fields)
    return RunMetrics.model_validate(
        {key: value for key, value in raw.items() if key in known_fields}
    )


class GenericHttpAdapter:
    def __init__(self) -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise AdapterError(
                "the generic_http adapter requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self.transport: Any = None  # test seam: httpx.MockTransport

    async def run(self, request: RunRequest) -> RunResult:
        settings = request.system.settings
        endpoint = settings.get("endpoint")
        if not isinstance(endpoint, str):
            raise AdapterError("generic_http system requires settings.endpoint")
        endpoint = substitute_environment(endpoint)

        headers: dict[str, str] = {"content-type": "application/json"}
        for name, value in settings.get("headers", {}).items():
            headers[str(name)] = substitute_environment(str(value))
        api_key_variable = request.system.environment.get("api_key")
        if api_key_variable:
            headers["authorization"] = f"Bearer {resolve_environment_value(api_key_variable)}"

        body = {
            "scenario_instance_id": request.scenario.id,
            "prompt": request.input_text,
            "answer_contract": request.scenario.answer_contract.model_dump(mode="json"),
        }
        started_at = utc_now()
        async with self._httpx.AsyncClient(
            transport=self.transport, timeout=request.timeout_seconds
        ) as client:
            response = await client.request(
                settings.get("method", "POST"), endpoint, json=body, headers=headers
            )
        completed_at = utc_now()
        response.raise_for_status()
        payload = response.json()

        mapping = settings.get("response_mapping", {})
        answer = resolve_json_path(payload, mapping.get("answer", "$.answer"))
        if not isinstance(answer, str) or not answer:
            raise AdapterError("response did not contain an answer at the mapped path")
        citations = _parse_citations(
            resolve_json_path(payload, mapping.get("citations", "$.citations"))
        )
        metrics = _parse_metrics(resolve_json_path(payload, mapping.get("metrics", "$.metrics")))
        if metrics.latency_ms is None:
            metrics.latency_ms = int((completed_at - started_at).total_seconds() * 1000)
        if metrics.cost_usd is None:
            metrics.cost_usd = compute_cost_usd(settings, metrics)
        raw_trace = resolve_json_path(payload, mapping.get("trace", "$.trace"))
        trace = [event for event in raw_trace if isinstance(event, dict)] if isinstance(
            raw_trace, list
        ) else []

        return RunResult(
            scenario_instance_id=request.scenario.id,
            system_id=request.system.id,
            trial_id=request.trial_id,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
            answer=answer,
            citations=citations,
            metrics=metrics,
            trace=trace,
        )
