"""OpenAI-compatible chat-completions adapter, with OpenRouter as the default.

This is the portable pattern for model gateways: OpenRouter, Together,
Fireworks, LiteLLM, vLLM, and most self-hosted proxies all speak the OpenAI
chat-completions wire format. Registering the same implementation twice keeps
one code path:

- ``adapter: openrouter`` — this adapter with OpenRouter defaults
  (base URL ``https://openrouter.ai/api/v1``, key from ``OPENROUTER_API_KEY``,
  the OpenRouter web plugin when ``search_enabled``, and OpenRouter's reported
  usage cost as ``cost_usd``);
- ``adapter: openai_compatible`` — the same code pointed at any other gateway
  via ``settings.base_url`` and ``environment.api_key``.

System settings:

- ``base_url``: API root ending before ``/chat/completions`` (defaulted for
  the openrouter flavour, required otherwise);
- ``search_enabled`` / ``max_searches``: attach OpenRouter's web plugin
  (openrouter flavour only — other gateways configure search via
  ``extra_body``);
- ``max_tokens``, ``temperature``: request tuning;
- ``extra_body``: mapping merged verbatim into the request body — the
  adaptation seam for gateway-specific fields (search options, routing
  hints, provider pinning);
- ``extra_headers``: additional plain-text headers (attribution headers such
  as OpenRouter's ``X-Title`` — never secrets);
- ``cost.input_per_mtok`` / ``cost.output_per_mtok``: fallback rates used
  only when the gateway does not report a cost itself.

Citations are read from OpenAI-style ``url_citation`` annotations on the
response message, which OpenRouter populates for web-enabled requests.
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

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_VARIABLE = "OPENROUTER_API_KEY"
_DEFAULT_MAX_TOKENS = 4096


class OpenAiCompatibleAdapter:
    def __init__(self, flavour: str = "openai_compatible") -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise AdapterError(
                f"the {flavour} adapter requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self._flavour = flavour
        self.transport: Any = None  # test seam: httpx.MockTransport

    @property
    def _is_openrouter(self) -> bool:
        return self._flavour == "openrouter"

    def _resolve_base_url(self, settings: dict[str, Any]) -> str:
        base_url = settings.get("base_url")
        if base_url is None and self._is_openrouter:
            base_url = OPENROUTER_BASE_URL
        if base_url is None:
            raise AdapterError(
                "openai_compatible systems must set settings.base_url "
                "(use adapter 'openrouter' for the OpenRouter default)"
            )
        return str(base_url).rstrip("/")

    def _resolve_api_key(self, environment: dict[str, str]) -> str:
        variable = environment.get("api_key")
        if variable is None and self._is_openrouter:
            variable = OPENROUTER_API_KEY_VARIABLE
        if variable is None:
            raise AdapterError(
                "openai_compatible systems must name their key variable in "
                "environment.api_key"
            )
        return resolve_environment_value(variable)

    async def run(self, request: RunRequest) -> RunResult:
        system = request.system
        if not system.model:
            raise AdapterError(f"{self._flavour} system requires a model")
        settings = system.settings
        api_key = self._resolve_api_key(system.environment)
        base_url = self._resolve_base_url(settings)

        body: dict[str, Any] = {
            "model": system.model,
            "max_tokens": int(settings.get("max_tokens", _DEFAULT_MAX_TOKENS)),
            "messages": [{"role": "user", "content": request.input_text}],
        }
        if "temperature" in settings:
            body["temperature"] = settings["temperature"]
        if self._is_openrouter:
            if settings.get("search_enabled", True):
                plugin: dict[str, Any] = {"id": "web"}
                if "max_searches" in settings:
                    plugin["max_results"] = int(settings["max_searches"])
                body["plugins"] = [plugin]
            # Ask OpenRouter to report token usage and actual cost.
            body["usage"] = {"include": True}
        extra_body = settings.get("extra_body")
        if isinstance(extra_body, dict):
            body.update(extra_body)

        headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        extra_headers = settings.get("extra_headers")
        if isinstance(extra_headers, dict):
            headers.update({str(key): str(value) for key, value in extra_headers.items()})

        started_at = utc_now()
        async with self._httpx.AsyncClient(
            transport=self.transport, timeout=request.timeout_seconds
        ) as client:
            response = await client.post(
                f"{base_url}/chat/completions", json=body, headers=headers
            )
        completed_at = utc_now()
        if response.status_code != 200:
            raise AdapterError(
                f"{self._flavour} API returned {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()

        choices = payload.get("choices") or []
        if not choices:
            raise AdapterError(f"{self._flavour} response contained no choices")
        message = choices[0].get("message") or {}
        answer = message.get("content") or ""
        if not answer.strip():
            raise AdapterError(f"{self._flavour} response contained no text content")

        citations: list[Citation] = []
        for annotation in message.get("annotations") or []:
            if annotation.get("type") != "url_citation":
                continue
            detail = annotation.get("url_citation") or {}
            url = detail.get("url")
            if not url:
                continue
            spans = [detail["content"]] if detail.get("content") else []
            citations.append(
                Citation(
                    id=f"c{len(citations) + 1}",
                    url=url,
                    title=detail.get("title"),
                    answer_spans=spans,
                )
            )

        usage = payload.get("usage") or {}
        metrics = RunMetrics(
            latency_ms=int((completed_at - started_at).total_seconds() * 1000),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            unique_sources=len({citation.url for citation in citations}) or None,
        )
        reported_cost = usage.get("cost")
        if isinstance(reported_cost, int | float):
            # OpenRouter reports the actual charge in USD credits; prefer it
            # over rates configured by hand.
            metrics.cost_usd = float(reported_cost)
        else:
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
            trace=[],
            provider_metadata={
                "provider": payload.get("provider") or self._flavour,
                "model": payload.get("model", system.model),
                "response_id": payload.get("id"),
                "stop_reason": choices[0].get("finish_reason"),
            },
        )
