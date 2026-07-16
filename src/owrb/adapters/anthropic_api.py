"""Anthropic provider adapter using the Messages API with native web search.

Provider-specific request/response handling stays inside this module
(SPEC.md 13.2). System settings:

- ``model`` (on the system definition) is required;
- ``search_enabled``: attach the provider web-search tool (default true);
- ``max_searches``: cap on provider searches per run;
- ``max_tokens``, ``temperature``, ``base_url``: request tuning.

The API key comes from the environment variable named by the system's
``environment.api_key`` entry (default ``ANTHROPIC_API_KEY``).
"""

from __future__ import annotations

from typing import Any

from owrb.adapters.base import (
    AdapterError,
    compute_cost_usd,
    resolve_environment_value,
    utc_now,
)
from owrb.models import Citation, RunMetrics, RunRequest, RunResult

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter:
    def __init__(self) -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise AdapterError(
                "the anthropic adapter requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self.transport: Any = None  # test seam: httpx.MockTransport

    async def run(self, request: RunRequest) -> RunResult:
        system = request.system
        if not system.model:
            raise AdapterError("anthropic system requires a model")
        api_key_variable = system.environment.get("api_key", "ANTHROPIC_API_KEY")
        api_key = resolve_environment_value(api_key_variable)
        settings = system.settings

        body: dict[str, Any] = {
            "model": system.model,
            "max_tokens": int(settings.get("max_tokens", _DEFAULT_MAX_TOKENS)),
            "messages": [{"role": "user", "content": request.input_text}],
        }
        if "temperature" in settings:
            body["temperature"] = settings["temperature"]
        if settings.get("search_enabled", True):
            tool: dict[str, Any] = {"type": "web_search_20250305", "name": "web_search"}
            if "max_searches" in settings:
                tool["max_uses"] = int(settings["max_searches"])
            body["tools"] = [tool]

        base_url = str(settings.get("base_url", _DEFAULT_BASE_URL)).rstrip("/")
        started_at = utc_now()
        async with self._httpx.AsyncClient(
            transport=self.transport, timeout=request.timeout_seconds
        ) as client:
            response = await client.post(
                f"{base_url}/v1/messages",
                json=body,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": _API_VERSION,
                    "content-type": "application/json",
                },
            )
        completed_at = utc_now()
        if response.status_code != 200:
            raise AdapterError(
                f"anthropic API returned {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()

        answer_parts: list[str] = []
        citations: list[Citation] = []
        trace: list[dict[str, Any]] = []
        searches = 0
        for block in payload.get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                answer_parts.append(block.get("text", ""))
                for raw_citation in block.get("citations") or []:
                    url = raw_citation.get("url")
                    if url:
                        spans = [raw_citation["cited_text"]] if raw_citation.get(
                            "cited_text"
                        ) else []
                        citations.append(
                            Citation(
                                id=f"c{len(citations) + 1}",
                                url=url,
                                title=raw_citation.get("title"),
                                answer_spans=spans,
                            )
                        )
            elif block_type == "server_tool_use":
                searches += 1
                trace.append(
                    {
                        "type": "search",
                        "tool": block.get("name"),
                        "input": block.get("input"),
                    }
                )

        answer = "\n".join(part for part in answer_parts if part)
        if not answer:
            raise AdapterError("anthropic response contained no text content")

        usage = payload.get("usage", {})
        reported_searches = usage.get("server_tool_use", {}).get("web_search_requests")
        metrics = RunMetrics(
            latency_ms=int((completed_at - started_at).total_seconds() * 1000),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            searches=reported_searches if reported_searches is not None else searches or None,
            unique_sources=len({citation.url for citation in citations}) or None,
        )
        metrics.cost_usd = compute_cost_usd(settings, metrics)

        return RunResult(
            scenario_instance_id=request.scenario.id,
            system_id=system.id,
            trial_id=request.trial_id,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
            answer=answer,
            citations=citations,
            metrics=metrics,
            trace=trace,
            provider_metadata={
                "provider": "anthropic",
                "model": payload.get("model", system.model),
                "response_id": payload.get("id"),
                "stop_reason": payload.get("stop_reason"),
            },
        )
