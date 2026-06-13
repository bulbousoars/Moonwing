"""Confirm execute_via_api still parses JSON findings via the new adapter layer."""

from __future__ import annotations

import json

import httpx
import pytest

from moonwing.worker.api_executor import (
    APIExecutionError,
    APIExecutionResult,
    execute_via_api,
)


def _patch_adapter(monkeypatch, payload_text: str, *, status: int = 200):
    """Replace get_adapter with one whose chat_with_tools returns canned text."""

    def fake_get_adapter(provider, *, client=None):
        class _FakeAdapter:
            def __init__(self):
                self.provider = provider

            def chat_with_tools(self, **kwargs):
                if status != 200:
                    from moonwing.core.llm import LLMError

                    raise LLMError("boom", status_code=status, provider=provider)

                from moonwing.core.llm import LLMResponse, LLMUsage

                return LLMResponse(
                    text=payload_text,
                    tool_calls=[],
                    usage=LLMUsage(input_tokens=1, output_tokens=2, total_tokens=3, raw={"a": 1}),
                    raw={},
                )

            def list_models(self, **kwargs):
                return []

        return _FakeAdapter()

    monkeypatch.setattr("moonwing.worker.api_executor.get_adapter", fake_get_adapter)


def test_execute_via_api_returns_parsed_payload(monkeypatch):
    _patch_adapter(monkeypatch, json.dumps({"findings": [{"title": "x", "severity": "low"}]}))

    result = execute_via_api(
        provider="openai",
        model="gpt-x",
        api_key="k",
        job_family="network_scan",
        source_ref="1.2.3.4",
    )
    assert isinstance(result, APIExecutionResult)
    assert result.provider == "openai"
    assert result.raw_payload["findings"][0]["title"] == "x"
    assert result.usage == {"a": 1}


def test_execute_via_api_injects_findings_key_when_missing(monkeypatch):
    _patch_adapter(monkeypatch, json.dumps({"notes": "no findings produced"}))
    result = execute_via_api(
        provider="anthropic",
        model="claude-x",
        api_key="k",
        job_family="source_hunt",
        source_ref="https://example.com/repo.git",
    )
    assert result.raw_payload["findings"] == []


def test_execute_via_api_raises_on_bad_json(monkeypatch):
    _patch_adapter(monkeypatch, "not json at all")
    with pytest.raises(APIExecutionError):
        execute_via_api(
            provider="openai",
            model="m",
            api_key="k",
            job_family="network_scan",
            source_ref="1.2.3.4",
        )


def test_execute_via_api_strips_code_fences(monkeypatch):
    """Some providers wrap JSON in ```json fences; the executor must strip them."""
    _patch_adapter(
        monkeypatch,
        "```json\n" + json.dumps({"findings": [{"title": "y", "severity": "high"}]}) + "\n```",
    )
    result = execute_via_api(
        provider="openai",
        model="m",
        api_key="k",
        job_family="network_scan",
        source_ref="x",
    )
    assert result.raw_payload["findings"][0]["title"] == "y"


def test_execute_via_api_surfaces_upstream_errors(monkeypatch):
    _patch_adapter(monkeypatch, "", status=401)
    with pytest.raises(APIExecutionError) as info:
        execute_via_api(
            provider="openai",
            model="m",
            api_key="bad",
            job_family="network_scan",
            source_ref="x",
        )
    assert info.value.status_code == 401


def test_execute_via_api_rejects_unsupported_provider():
    with pytest.raises(APIExecutionError):
        execute_via_api(
            provider="unknown",
            model="m",
            api_key="k",
            job_family="network_scan",
            source_ref="x",
        )
