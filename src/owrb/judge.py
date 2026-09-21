"""LLM judge clients for semantic evaluation (SPEC.md 16.6-16.7).

Judges receive scenario, answer, claims, citations, and evidence extracts —
never the candidate system's identity. The MVP supports one configurable
judge (anthropic or openai); the ``JudgeClient`` protocol keeps room for
panels, pairwise judging, and self-consistency later.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from owrb.adapters.base import resolve_environment_value

_UNCONFIGURED_MODELS = frozenset({"", "replace-me"})
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JudgeError(RuntimeError):
    """Raised when the judge cannot be reached or returns unusable output."""


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = "none"
    model: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int = Field(default=4096, ge=1)


class JudgeClient(Protocol):
    identity: dict[str, Any]

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        ...


def extract_json(text: str) -> Any:
    """Parse JSON from a judge response, tolerating code fences and prose."""
    candidates = [text.strip()]
    candidates.extend(match.strip() for match in _JSON_FENCE.findall(text))
    for start_char, end_char in (("[", "]"), ("{", "}")):
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    salvaged = _salvage_truncated_array(text)
    if salvaged is not None:
        return salvaged
    raise JudgeError(
        f"judge response was not valid JSON ({len(text)} chars): head {text[:200]!r} tail {text[-160:]!r}"
    )


def _salvage_truncated_array(text: str) -> list[Any] | None:
    """Recover the complete objects of a JSON array cut off by the output token cap.

    A judge asked for an array of objects that runs past max_tokens ends
    mid-object. Everything before the last complete top-level object is
    still well-formed; return those objects (at least one) so the trial is
    judged on them rather than falling back to deterministic checks."""
    start = text.find("[")
    if start == -1:
        return None
    depth, in_string, escape, last_complete = 0, False, False, -1
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                last_complete = index
        elif char == "]" and depth == 0:
            break
    if last_complete == -1:
        return None
    try:
        value = json.loads(text[start : last_complete + 1] + "]")
    except json.JSONDecodeError:
        value = _parse_objects_individually(text, start, last_complete)
    return value if isinstance(value, list) and value else None


def _parse_objects_individually(text: str, start: int, end: int) -> list[Any] | None:
    """Parse each top-level object of an array on its own, skipping malformed ones."""
    items: list[Any] = []
    depth, in_string, escape, obj_start = 0, False, False, -1
    for index in range(start, end + 1):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                obj_start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and obj_start != -1:
                try:
                    items.append(json.loads(text[obj_start : index + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = -1
    return items or None


class AnthropicJudge:
    def __init__(self, config: JudgeConfig) -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise JudgeError(
                "the anthropic judge requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self._config = config
        self.transport: Any = None  # test seam: httpx.MockTransport
        self.identity = {"adapter": "anthropic", "model": config.model}

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        api_key = resolve_environment_value(self._config.api_key_env or "ANTHROPIC_API_KEY")
        base_url = (self._config.base_url or "https://api.anthropic.com").rstrip("/")
        async with self._httpx.AsyncClient(transport=self.transport, timeout=120) as client:
            response = await client.post(
                f"{base_url}/v1/messages",
                json={
                    "model": self._config.model,
                    "max_tokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        if response.status_code != 200:
            raise JudgeError(
                f"anthropic judge returned {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        parts = [
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)


class OpenAiJudge:
    def __init__(self, config: JudgeConfig) -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise JudgeError(
                "the openai judge requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self._config = config
        self.transport: Any = None  # test seam: httpx.MockTransport
        self.identity = {"adapter": "openai", "model": config.model}

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        api_key = resolve_environment_value(self._config.api_key_env or "OPENAI_API_KEY")
        base_url = (self._config.base_url or "https://api.openai.com").rstrip("/")
        async with self._httpx.AsyncClient(transport=self.transport, timeout=120) as client:
            response = await client.post(
                f"{base_url}/v1/responses",
                json={
                    "model": self._config.model,
                    "instructions": system_prompt,
                    "input": user_prompt,
                    "temperature": self._config.temperature,
                    "max_output_tokens": self._config.max_tokens,
                },
                headers={
                    "authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
            )
        if response.status_code != 200:
            raise JudgeError(
                f"openai judge returned {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        parts: list[str] = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
        return "\n".join(part for part in parts if part)


class OpenAiCompatibleJudge:
    """Chat-completions judge for OpenRouter and other OpenAI-compatible gateways.

    ``adapter: openrouter`` fills in the OpenRouter base URL and key variable;
    ``adapter: openai_compatible`` requires ``base_url`` (and ``api_key_env``)
    so the same judge works against any gateway speaking the format.
    """

    _OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, config: JudgeConfig, flavour: str = "openrouter") -> None:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without extras
            raise JudgeError(
                f"the {flavour} judge requires the 'http' extra: pip install owrb[http]"
            ) from error
        self._httpx = httpx
        self._config = config
        self._flavour = flavour
        self.transport: Any = None  # test seam: httpx.MockTransport
        self.identity = {"adapter": flavour, "model": config.model}
        if flavour != "openrouter" and not config.base_url:
            raise JudgeError(
                "an openai_compatible judge requires base_url "
                "(use adapter 'openrouter' for the OpenRouter default)"
            )

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        default_key = "OPENROUTER_API_KEY" if self._flavour == "openrouter" else None
        key_variable = self._config.api_key_env or default_key
        if key_variable is None:
            raise JudgeError("an openai_compatible judge requires api_key_env")
        api_key = resolve_environment_value(key_variable)
        base_url = (self._config.base_url or self._OPENROUTER_BASE_URL).rstrip("/")
        # Transport failures, 429/5xx, and malformed 200 bodies (a truncated
        # stream parses as invalid JSON) are transient — retry with backoff
        # rather than aborting a long evaluation pass.
        last_error: Exception | None = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(2**attempt)
            try:
                async with self._httpx.AsyncClient(
                    transport=self.transport, timeout=120
                ) as client:
                    response = await client.post(
                        f"{base_url}/chat/completions",
                        json={
                            "model": self._config.model,
                            "temperature": self._config.temperature,
                            "max_tokens": self._config.max_tokens,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                        },
                        headers={
                            "authorization": f"Bearer {api_key}",
                            "content-type": "application/json",
                        },
                    )
            except self._httpx.HTTPError as error:
                last_error = error
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = JudgeError(
                    f"{self._flavour} judge returned {response.status_code}: "
                    f"{response.text[:300]}"
                )
                continue
            if response.status_code != 200:
                raise JudgeError(
                    f"{self._flavour} judge returned {response.status_code}: "
                    f"{response.text[:300]}"
                )
            try:
                payload = response.json()
            except ValueError as error:
                last_error = error
                continue
            choices = payload.get("choices") or []
            message = choices[0].get("message") if choices else None
            content = (message or {}).get("content") or ""
            if not content.strip():
                raise JudgeError(f"{self._flavour} judge response contained no text content")
            return str(content)
        raise JudgeError(f"{self._flavour} judge unavailable after retries: {last_error}")


def create_judge(config: JudgeConfig) -> JudgeClient | None:
    """Build the configured judge, or None when no usable judge is configured."""
    if config.model is None or config.model in _UNCONFIGURED_MODELS:
        return None
    if config.adapter == "anthropic":
        return AnthropicJudge(config)
    if config.adapter == "openai":
        return OpenAiJudge(config)
    if config.adapter in ("openrouter", "openai_compatible"):
        return OpenAiCompatibleJudge(config, flavour=config.adapter)
    return None
