from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from moonwing.core.llm import LLMError, get_adapter
from moonwing.core.llm.anthropic_adapter import AnthropicAdapter
from moonwing.core.llm.google_adapter import GoogleAdapter
from moonwing.core.llm.openai_compatible_adapter import OpenAICompatibleAdapter


def _mock_client(handler) -> httpx.Client:
    """Build an httpx.Client backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


# ----------------------- factory --------------------------------------


def test_factory_supports_each_provider():
    for p in ("anthropic", "openai", "openrouter", "ollama", "google"):
        adapter = get_adapter(p)
        assert adapter.provider == p


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError):
        get_adapter("nonexistent")


# ----------------------- Anthropic ------------------------------------


def test_anthropic_chat_returns_text_and_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "claude-test"
        assert body["system"] == "be terse"
        assert body["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "thinking..."},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "nmap_scan",
                        "input": {"target": "1.2.3.4"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 12, "output_tokens": 5},
            },
        )

    adapter = AnthropicAdapter(client=_mock_client(handler))
    resp = adapter.chat_with_tools(
        messages=[
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "scan it"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "nmap_scan",
                    "description": "scan",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model="claude-test",
        api_key="k",
    )
    assert resp.text == "thinking..."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "nmap_scan"
    assert resp.tool_calls[0].arguments == {"target": "1.2.3.4"}
    assert resp.usage.input_tokens == 12
    assert resp.stop_reason == "tool_use"


def test_anthropic_raises_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    adapter = AnthropicAdapter(client=_mock_client(handler))
    with pytest.raises(LLMError) as info:
        adapter.chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            model="m",
            api_key="k",
        )
    assert info.value.status_code == 401
    assert info.value.provider == "anthropic"


def test_anthropic_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-sonnet-4-6"}, {"id": "claude-opus-4-7"}]},
        )

    adapter = AnthropicAdapter(client=_mock_client(handler))
    assert adapter.list_models(api_key="k") == ["claude-sonnet-4-6", "claude-opus-4-7"]


# ----------------------- OpenAI-compatible ---------------------------


def test_openai_chat_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "nmap_scan"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "nmap_scan",
                                        "arguments": '{"target": "1.2.3.4"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 7, "total_tokens": 11},
            },
        )

    adapter = OpenAICompatibleAdapter("openai", client=_mock_client(handler))
    resp = adapter.chat_with_tools(
        messages=[{"role": "user", "content": "go"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "nmap_scan",
                    "description": "scan",
                    "parameters": {"type": "object"},
                },
            }
        ],
        model="gpt-test",
        api_key="k",
    )
    assert resp.text == ""
    assert resp.tool_calls[0].name == "nmap_scan"
    assert resp.tool_calls[0].arguments == {"target": "1.2.3.4"}
    assert resp.usage.input_tokens == 4
    assert resp.stop_reason == "tool_calls"


def test_openai_handles_malformed_tool_arguments_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "x", "arguments": "not json"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    adapter = OpenAICompatibleAdapter("openai", client=_mock_client(handler))
    resp = adapter.chat_with_tools(
        messages=[{"role": "user", "content": "go"}],
        tools=None,
        model="m",
        api_key="k",
    )
    # Adapter survives — surfaces the raw text so the caller can debug.
    assert resp.tool_calls[0].arguments == {"_raw": "not json"}


def test_openrouter_sends_referer_header():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ]
            },
        )

    adapter = OpenAICompatibleAdapter("openrouter", client=_mock_client(handler))
    adapter.chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model="x",
        api_key="k",
    )
    assert "http-referer" in {h.lower() for h in captured["headers"]}
    assert "x-title" in {h.lower() for h in captured["headers"]}


# ----------------------- Google --------------------------------------


def test_google_chat_parses_function_call():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["systemInstruction"]["parts"][0]["text"] == "be terse"
        assert body["tools"][0]["function_declarations"][0]["name"] == "nmap_scan"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "calling tool..."},
                                {
                                    "functionCall": {
                                        "name": "nmap_scan",
                                        "args": {"target": "1.2.3.4"},
                                    }
                                },
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 3,
                    "candidatesTokenCount": 9,
                    "totalTokenCount": 12,
                },
            },
        )

    adapter = GoogleAdapter(client=_mock_client(handler))
    resp = adapter.chat_with_tools(
        messages=[
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "scan"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "nmap_scan",
                    "description": "scan",
                    "parameters": {"type": "object"},
                },
            }
        ],
        model="gemini-test",
        api_key="k",
    )
    assert "calling tool" in resp.text
    assert resp.tool_calls[0].name == "nmap_scan"
    assert resp.tool_calls[0].arguments == {"target": "1.2.3.4"}
    assert resp.usage.input_tokens == 3
