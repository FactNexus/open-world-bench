import asyncio
import json
import sys

import httpx
import pytest
from conftest import make_scenario, make_system

from owrb.adapters import AdapterError, create_adapter
from owrb.adapters.anthropic_api import AnthropicAdapter
from owrb.adapters.base import build_input_text
from owrb.adapters.command import CommandAdapter
from owrb.adapters.generic_http import GenericHttpAdapter, resolve_json_path
from owrb.adapters.openai_api import OpenAiAdapter
from owrb.models import RunRequest

ECHO_COMMAND = [
    sys.executable,
    "-c",
    (
        "import json, sys; request = json.load(sys.stdin); "
        "print(json.dumps({"
        "'answer': 'Answer for ' + request['scenario_instance_id'], "
        "'citations': [{'url': 'https://example.com/a', 'title': 'A'}], "
        "'metrics': {'input_tokens': 10, 'output_tokens': 5}, "
        "'trace': [{'type': 'search', 'query': 'red'}]}))"
    ),
]


def make_request(system_overrides: dict | None = None, timeout: int = 30) -> RunRequest:
    return RunRequest(
        scenario=make_scenario(),
        system=make_system(**(system_overrides or {})),
        trial_id="t01",
        input_text=build_input_text(make_scenario()),
        timeout_seconds=timeout,
    )


def test_build_input_text_contains_prompt_and_contract() -> None:
    scenario = make_scenario()
    text = build_input_text(scenario)
    assert scenario.prompt in text
    assert "Response requirements:" in text
    assert "citations" in text.lower()
    assert "markdown" in text


def test_resolve_json_path() -> None:
    payload = {"answer": "x", "nested": {"items": [{"value": 42}]}}
    assert resolve_json_path(payload, "$.answer") == "x"
    assert resolve_json_path(payload, "$.nested.items[0].value") == 42
    assert resolve_json_path(payload, "$.missing") is None
    with pytest.raises(AdapterError):
        resolve_json_path(payload, "answer")


def test_command_adapter_round_trip() -> None:
    request = make_request({"settings": {"command": ECHO_COMMAND}})
    result = asyncio.run(CommandAdapter().run(request))
    assert result.status == "completed"
    assert result.answer == "Answer for minimal.pick-colour.000001"
    assert result.citations[0].url == "https://example.com/a"
    assert result.metrics.input_tokens == 10
    assert result.metrics.latency_ms is not None
    assert result.trace == [{"type": "search", "query": "red"}]


def test_command_adapter_failure_is_reported() -> None:
    request = make_request(
        {"settings": {"command": [sys.executable, "-c", "import sys; sys.exit(3)"]}}
    )
    with pytest.raises(AdapterError, match="exited with code 3"):
        asyncio.run(CommandAdapter().run(request))


def test_command_adapter_invalid_json_is_reported() -> None:
    request = make_request(
        {"settings": {"command": [sys.executable, "-c", "print('not json')"]}}
    )
    with pytest.raises(AdapterError, match="invalid JSON"):
        asyncio.run(CommandAdapter().run(request))


def test_generic_http_adapter_maps_response_and_sends_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_HTTP_KEY", "super-secret-value")
    captured: dict = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["auth"] = http_request.headers.get("authorization")
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "result": {"text": "The colour red is warm."},
                "sources": [{"url": "https://example.com/red"}],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        )

    adapter = GenericHttpAdapter()
    adapter.transport = httpx.MockTransport(handler)
    request = make_request(
        {
            "adapter": "generic_http",
            "settings": {
                "endpoint": "https://agent.example/api",
                "response_mapping": {
                    "answer": "$.result.text",
                    "citations": "$.sources",
                    "metrics": "$.usage",
                },
            },
            "environment": {"api_key": "TEST_HTTP_KEY"},
        }
    )
    result = asyncio.run(adapter.run(request))
    assert captured["auth"] == "Bearer super-secret-value"
    assert captured["body"]["scenario_instance_id"] == "minimal.pick-colour.000001"
    assert result.answer == "The colour red is warm."
    assert result.citations[0].url == "https://example.com/red"
    assert result.metrics.input_tokens == 7
    assert "super-secret-value" not in result.model_dump_json()


def test_anthropic_adapter_parses_answer_citations_and_search_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    captured: dict = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["api_key"] = http_request.headers.get("x-api-key")
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_01",
                "model": "claude-fable-5",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "server_tool_use", "name": "web_search", "input": {"query": "red"}},
                    {
                        "type": "text",
                        "text": "Red is a warm colour.",
                        "citations": [
                            {
                                "type": "web_search_result_location",
                                "url": "https://example.com/colours",
                                "title": "Colour theory",
                                "cited_text": "red is warm",
                            }
                        ],
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "server_tool_use": {"web_search_requests": 1},
                },
            },
        )

    adapter = AnthropicAdapter()
    adapter.transport = httpx.MockTransport(handler)
    request = make_request(
        {
            "adapter": "anthropic",
            "model": "claude-fable-5",
            "settings": {
                "search_enabled": True,
                "max_searches": 5,
                "cost": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
            },
        }
    )
    result = asyncio.run(adapter.run(request))
    assert captured["api_key"] == "anthropic-secret"
    assert captured["body"]["tools"][0]["type"] == "web_search_20250305"
    assert captured["body"]["tools"][0]["max_uses"] == 5
    assert result.answer == "Red is a warm colour."
    assert result.citations[0].url == "https://example.com/colours"
    assert result.citations[0].answer_spans == ["red is warm"]
    assert result.metrics.searches == 1
    assert result.metrics.cost_usd == pytest.approx(0.0009)
    assert result.provider_metadata["response_id"] == "msg_01"
    assert "anthropic-secret" not in result.model_dump_json()


def test_anthropic_adapter_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    request = make_request({"adapter": "anthropic", "model": "claude-fable-5"})
    with pytest.raises(AdapterError, match="ANTHROPIC_API_KEY"):
        asyncio.run(AnthropicAdapter().run(request))


def test_openai_adapter_parses_responses_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    captured: dict = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["auth"] = http_request.headers.get("authorization")
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_01",
                "model": "gpt-test",
                "status": "completed",
                "output": [
                    {"type": "web_search_call", "action": {"query": "red colour"}},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Red is a primary colour.",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/primary",
                                        "title": "Primary colours",
                                        "start_index": 0,
                                        "end_index": 3,
                                    }
                                ],
                            }
                        ],
                    },
                ],
                "usage": {"input_tokens": 50, "output_tokens": 20},
            },
        )

    adapter = OpenAiAdapter()
    adapter.transport = httpx.MockTransport(handler)
    request = make_request({"adapter": "openai", "model": "gpt-test"})
    result = asyncio.run(adapter.run(request))
    assert captured["auth"] == "Bearer openai-secret"
    assert captured["body"]["tools"] == [{"type": "web_search"}]
    assert result.answer == "Red is a primary colour."
    assert result.citations[0].url == "https://example.com/primary"
    assert result.citations[0].answer_spans == ["Red"]
    assert result.metrics.searches == 1
    assert "openai-secret" not in result.model_dump_json()


def test_create_adapter_registry() -> None:
    assert isinstance(create_adapter(make_system(adapter="command")), CommandAdapter)
    assert isinstance(create_adapter(make_system(adapter="generic_http")), GenericHttpAdapter)
    assert isinstance(
        create_adapter(make_system(adapter="provider_specific", provider="anthropic")),
        AnthropicAdapter,
    )
    assert isinstance(
        create_adapter(make_system(adapter="provider_specific", provider="openai")),
        OpenAiAdapter,
    )
    with pytest.raises(AdapterError, match="manual_import"):
        create_adapter(make_system(adapter="manual_import"))
    with pytest.raises(AdapterError, match="unknown adapter"):
        create_adapter(make_system(adapter="mystery"))
