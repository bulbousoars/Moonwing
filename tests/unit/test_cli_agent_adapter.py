from __future__ import annotations

import json
import subprocess

import pytest

import moonwing.worker.cli_agent_adapter as cca
from moonwing.worker.cli_agent_adapter import (
    CliAgentAdapter,
    _extract_assistant_text,
    _find_json_object,
    render_react_prompt,
)
from moonwing.core.llm import LLMError


# --------------------------- prompt rendering ------------------------------


def test_render_prompt_includes_history_and_tools():
    messages = [
        {"role": "system", "content": "probe the target"},
        {"role": "user", "content": "Target: 10.0.0.5"},
        {"role": "assistant", "content": "thinking", "tool_calls": [
            {"id": "1", "name": "nmap_scan", "arguments": {"target": "10.0.0.5"}}
        ]},
        {"role": "tool", "tool_call_id": "1", "content": '{"ok": true, "output": {"raw": "80/tcp open"}}'},
    ]
    tools = [{"function": {"name": "httpx_probe", "description": "probe http",
                           "parameters": {"properties": {"url": {}}}}}]
    prompt = render_react_prompt(messages, tools)
    assert "probe the target" in prompt
    assert "Target: 10.0.0.5" in prompt
    assert "nmap_scan(" in prompt
    assert "80/tcp open" in prompt
    assert "httpx_probe(url): probe http" in prompt
    assert "tool_call" in prompt and "final" in prompt  # protocol present


# --------------------------- envelope extraction ---------------------------


def test_extract_claude_envelope():
    inner = '{"final": {"findings": []}}'
    stdout = json.dumps({"type": "result", "result": inner})
    assert _extract_assistant_text("anthropic", stdout) == inner


def test_extract_gemini_envelope():
    inner = '{"thought": "x", "tool_call": {"name": "httpx_probe", "arguments": {}}}'
    stdout = json.dumps({"response": inner})
    assert _extract_assistant_text("google", stdout) == inner


def test_extract_codex_jsonl_takes_last_agent_text():
    inner = '{"final": {"findings": [{"title": "x"}]}}'
    lines = [
        json.dumps({"type": "thinking", "text": "noise"}),
        json.dumps({"item": {"type": "agent_message", "text": inner}}),
    ]
    assert _extract_assistant_text("openai", "\n".join(lines)) == inner


def test_extract_falls_back_to_raw():
    raw = '{"tool_call": {"name": "nmap_scan", "arguments": {}}}'
    # not an envelope shape the extractor recognizes as wrapping
    assert _extract_assistant_text("anthropic", raw) == raw or "nmap_scan" in _extract_assistant_text("anthropic", raw)


# --------------------------- JSON object finding ---------------------------


def test_find_json_object_strips_fences():
    text = "```json\n{\"final\": {\"findings\": []}}\n```"
    obj = _find_json_object(text)
    assert obj == {"final": {"findings": []}}


def test_find_json_object_trims_prose():
    text = 'Here is my answer: {"thought": "ok", "final": {"findings": []}} done.'
    obj = _find_json_object(text)
    assert obj["thought"] == "ok"


# --------------------------- chat_with_tools (mocked CLI) ------------------


def _mock_cli(monkeypatch, inner_response: str, *, provider="anthropic", returncode=0, stderr=""):
    """Make subprocess.run return a claude-style envelope wrapping inner_response."""
    envelope = json.dumps({"type": "result", "result": inner_response})

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=command, returncode=returncode, stdout=envelope, stderr=stderr
        )

    monkeypatch.setattr(cca.subprocess, "run", fake_run)


def test_chat_returns_tool_call(monkeypatch):
    inner = json.dumps({"thought": "scan ports", "tool_call": {
        "name": "nmap_scan", "arguments": {"target": "10.0.0.5"}}})
    _mock_cli(monkeypatch, inner)
    adapter = CliAgentAdapter(provider="anthropic")
    resp = adapter.chat_with_tools(messages=[{"role": "user", "content": "go"}],
                                   tools=None, model="claude-x")
    assert resp.text == "scan ports"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "nmap_scan"
    assert resp.tool_calls[0].arguments == {"target": "10.0.0.5"}
    assert resp.tool_calls[0].id  # an id was minted


def test_chat_returns_final_as_findings_text(monkeypatch):
    inner = json.dumps({"thought": "done", "final": {"findings": [{"title": "open port"}]}})
    _mock_cli(monkeypatch, inner)
    adapter = CliAgentAdapter(provider="anthropic")
    resp = adapter.chat_with_tools(messages=[{"role": "user", "content": "go"}],
                                   tools=None, model="claude-x")
    assert resp.tool_calls == []
    parsed = json.loads(resp.text)
    assert parsed["findings"][0]["title"] == "open port"


def test_chat_bare_findings_passthrough(monkeypatch):
    inner = json.dumps({"findings": [{"title": "x"}]})
    _mock_cli(monkeypatch, inner)
    adapter = CliAgentAdapter(provider="anthropic")
    resp = adapter.chat_with_tools(messages=[{"role": "user", "content": "go"}],
                                   tools=None, model="claude-x")
    assert resp.tool_calls == []
    assert json.loads(resp.text)["findings"][0]["title"] == "x"


def test_chat_unparseable_becomes_final(monkeypatch):
    _mock_cli(monkeypatch, "I could not complete the scan.")
    adapter = CliAgentAdapter(provider="anthropic")
    resp = adapter.chat_with_tools(messages=[{"role": "user", "content": "go"}],
                                   tools=None, model="claude-x")
    assert resp.tool_calls == []
    assert "could not complete" in resp.text


def test_chat_nonzero_exit_raises_llmerror(monkeypatch):
    _mock_cli(monkeypatch, "irrelevant", returncode=1, stderr="boom")
    adapter = CliAgentAdapter(provider="anthropic")
    with pytest.raises(LLMError):
        adapter.chat_with_tools(messages=[{"role": "user", "content": "go"}],
                                tools=None, model="claude-x")


def test_chat_missing_binary_raises_llmerror(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no claude")
    monkeypatch.setattr(cca.subprocess, "run", boom)
    adapter = CliAgentAdapter(provider="anthropic")
    with pytest.raises(LLMError):
        adapter.chat_with_tools(messages=[{"role": "user", "content": "go"}],
                                tools=None, model="claude-x")
