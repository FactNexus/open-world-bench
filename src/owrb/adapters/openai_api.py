"""OpenAI provider adapter using the Responses API with native web search.

Provider-specific request/response handling stays inside this module
(SPEC.md 13.2). System settings mirror the anthropic adapter:
``search_enabled`` (default true), ``max_tokens``, ``temperature``, and
``base_url``. The API key comes from the environment variable named by the
system's ``environment.api_key`` entry (default ``OPENAI_API_KEY``).
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

_DEFAULT_BASE_URL = "https://api.openai.com"


class OpenAiAdapter:
    def __init__(self) -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise AdapterError(
                "the openai adapter requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self.transport: Any = None  # test seam: httpx.MockTransport

    async def run(self, request: RunRequest) -> RunResult:
        system = request.system
        if not system.model:
            raise AdapterError("openai system requires a model")
        api_key_variable = system.environment.get("api_key", "OPENAI_API_KEY")
        api_key = resolve_environment_value(api_key_variable)
        settings = system.settings

        body: dict[str, Any] = {"model": system.model, "input": request.input_text}
        if "temperature" in settings:
            body["temperature"] = settings["temperature"]
        if "max_tokens" in settings:
            body["max_output_tokens"] = int(settings["max_tokens"])
        if settings.get("search_enabled", True):
            body["tools"] = [{"type": "web_search"}]

        base_url = str(settings.get("base_url", _DEFAULT_BASE_URL)).rstrip("/")
        started_at = utc_now()
        async with self._httpx.AsyncClient(
            transport=self.transport, timeout=request.timeout_seconds
        ) as client:
            response = await client.post(
                f"{base_url}/v1/responses",
                json=body,
                headers={
                    "authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
            )
        completed_at = utc_now()
        if response.status_code != 200:
            raise AdapterError(
                f"openai API returned {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()

        answer_parts: list[str] = []
        citations: list[Citation] = []
        trace: list[dict[str, Any]] = []
        searches = 0
        for item in payload.get("output", []):
            item_type = item.get("type")
            if item_type == "web_search_call":
                searches += 1
                trace.append(
                    {
                        "type": "search",
                        "tool": "web_search",
                        "input": item.get("action"),
                    }
                )
            elif item_type == "message":
                for content in item.get("content", []):
                    if content.get("type") != "output_text":
                        continue
                    text = content.get("text", "")
                    answer_parts.append(text)
                    for annotation in content.get("annotations") or []:
                        if annotation.get("type") != "url_citation":
                            continue
                        url = annotation.get("url")
                        if not url:
                            continue
                        start = annotation.get("start_index")
                        end = annotation.get("end_index")
                        spans = (
                            [text[start:end]]
                            if isinstance(start, int) and isinstance(end, int)
                            else []
                        )
                        citations.append(
                            Citation(
                                id=f"c{len(citations) + 1}",
                                url=url,
                                title=annotation.get("title"),
                                answer_spans=spans,
                            )
                        )

        answer = "\n".join(part for part in answer_parts if part)
        if not answer:
            raise AdapterError("openai response contained no output text")

        usage = payload.get("usage", {})
        metrics = RunMetrics(
            latency_ms=int((completed_at - started_at).total_seconds() * 1000),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            searches=searches or None,
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
                "provider": "openai",
                "model": payload.get("model", system.model),
                "response_id": payload.get("id"),
                "status": payload.get("status"),
            },
        )
