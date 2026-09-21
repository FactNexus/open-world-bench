"""Decision-model judge: typed questions over a state, no text generation.

The first supported model is TypeSafe's jev, reached through OpenRouter's decisions
endpoint (``POST {base}/v1/systemone``) or TypeSafe's own API. A decision judge takes
over the claim-support verdicts and the rubric scoring; claim decomposition still
needs a text judge, because it produces text.

Question shapes (TypeSafe System One API):
  ``{"type": "choice", "instructions": ..., "criteria": {option: description}}``
  ``{"type": "score",  "instructions": ..., "criteria": [level descriptions, low→high]}``
  ``{"type": "noul",   "instructions": ...}``  -> probability that the answer is yes
Answers carry per-option probabilities and a 0–1 ``confidence`` (spread of the
distribution), which callers may use to escalate low-confidence items to a text judge.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from owrb.judge import JudgeError

_ATTEMPTS = 4
_DEFAULT_ENDPOINTS = {
    "openrouter_decisions": "https://openrouter.ai/api",
    "typesafe": "https://api.typesafe.ai",
}


class DecisionError(JudgeError):
    """A decision-model call failed after retries."""


class DecisionJudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = "none"  # none | openrouter_decisions | typesafe
    model: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    concurrency: int = Field(default=8, ge=1)
    # Claim verdicts whose confidence is below this are re-asked of the text judge
    # (0 disables escalation).
    escalate_below_confidence: float = Field(default=0.0, ge=0, le=1)


class DecisionClient(Protocol):
    identity: dict[str, Any]
    usage: dict[str, float]

    async def decide(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        ...


class HttpDecisionClient:
    """Calls the System One endpoint through OpenRouter or TypeSafe directly."""

    def __init__(self, config: DecisionJudgeConfig) -> None:
        key_variable = config.api_key_env or (
            "OPENROUTER_API_KEY" if config.adapter == "openrouter_decisions" else "TYPESAFE_API_KEY"
        )
        api_key = os.environ.get(key_variable)
        if not api_key:
            raise JudgeError(f"decision judge needs {key_variable} in the environment")
        base = (config.base_url or _DEFAULT_ENDPOINTS[config.adapter]).rstrip("/")
        self._endpoint = f"{base}/v1/systemone"
        self._headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        self._model = config.model
        self._timeout = config.timeout_seconds
        self.identity = {"adapter": config.adapter, "model": config.model}
        self.usage: dict[str, float] = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        }

    async def decide(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        body = {"model": self._model, "state": state, "questions": questions}
        last_error = ""
        for attempt in range(_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._endpoint, json=body, headers=self._headers)
            except httpx.HTTPError as error:
                last_error = f"{type(error).__name__}: {error}"
            else:
                if response.status_code == 200:
                    payload = response.json()
                    usage = payload.get("usage") or {}
                    self.usage["calls"] += 1
                    self.usage["input_tokens"] += float(usage.get("input_tokens") or 0)
                    self.usage["output_tokens"] += float(usage.get("output_tokens") or 0)
                    self.usage["cost_usd"] += float(usage.get("cost") or 0)
                    answers = payload.get("answers")
                    if not isinstance(answers, dict):
                        raise DecisionError("decision response carried no answers")
                    return answers
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in (408, 409, 429, 500, 502, 503, 504, 529):
                    raise DecisionError(f"decision request rejected: {last_error}")
            await asyncio.sleep(1.5 * (attempt + 1))
        raise DecisionError(f"decision request failed after {_ATTEMPTS} attempts: {last_error}")


def create_decision_judge(config: DecisionJudgeConfig) -> DecisionClient | None:
    if config.adapter == "none" or not config.model:
        return None
    if config.adapter in _DEFAULT_ENDPOINTS:
        return HttpDecisionClient(config)
    raise JudgeError(f"unknown decision judge adapter {config.adapter!r}")
