from __future__ import annotations

from moonwing.core.llm import LLMError, LLMResponse, LLMToolCall, LLMUsage
from moonwing.services.llm_doctor import check_provider


class _FakeAdapter:
    provider = "openai"

    def __init__(self, *, models, tool_use_response=None, list_models_error=None):
        self._models = models
        self._tool_use_response = tool_use_response
        self._list_models_error = list_models_error

    def list_models(self, *, api_key, timeout=30):
        if self._list_models_error is not None:
            raise self._list_models_error
        return self._models

    def chat_with_tools(self, **kwargs):
        if self._tool_use_response is None:
            return LLMResponse(text="ok", tool_calls=[], usage=LLMUsage())
        return self._tool_use_response


def _install_fake(monkeypatch, adapter):
    monkeypatch.setattr(
        "moonwing.services.llm_doctor.get_adapter", lambda provider: adapter
    )


def test_doctor_healthy_path(monkeypatch):
    _install_fake(
        monkeypatch,
        _FakeAdapter(
            models=["gpt-5.5", "gpt-4o"],
            tool_use_response=LLMResponse(
                text="",
                tool_calls=[LLMToolCall(id="x", name="meta_note", arguments={"note": "ok"})],
                usage=LLMUsage(),
            ),
        ),
    )
    h = check_provider(provider="openai", api_key="k", model="gpt-5.5")
    assert h.reachable is True
    assert h.auth_ok is True
    assert h.tool_use_ok is True
    assert h.models_listed == 2
    assert h.error is None


def test_doctor_marks_auth_failure(monkeypatch):
    _install_fake(
        monkeypatch,
        _FakeAdapter(
            models=[],
            list_models_error=LLMError("bad key", status_code=401, provider="openai"),
        ),
    )
    h = check_provider(provider="openai", api_key="bad", model="gpt-5.5")
    # 401 means reachable but auth failed.
    assert h.reachable is True
    assert h.auth_ok is False
    assert h.tool_use_ok is False
    assert "bad key" in (h.error or "")


def test_doctor_marks_unreachable_on_network_error(monkeypatch):
    _install_fake(
        monkeypatch,
        _FakeAdapter(models=[], list_models_error=ConnectionError("no route to host")),
    )
    h = check_provider(provider="openai", api_key="k", model="gpt-5.5")
    assert h.reachable is False
    assert h.auth_ok is False
    assert "ConnectionError" in (h.error or "")


def test_doctor_flags_text_only_response_as_tool_use_failure(monkeypatch):
    _install_fake(
        monkeypatch,
        _FakeAdapter(
            models=["gpt-5.5"],
            tool_use_response=LLMResponse(
                text="pong", tool_calls=[], usage=LLMUsage()
            ),
        ),
    )
    h = check_provider(provider="openai", api_key="k", model="gpt-5.5")
    assert h.reachable is True
    assert h.auth_ok is True
    assert h.tool_use_ok is False
    assert h.detail.get("text_response_only") == "pong"


def test_doctor_skips_tool_use_when_no_model_specified(monkeypatch):
    _install_fake(monkeypatch, _FakeAdapter(models=["x"]))
    h = check_provider(provider="openai", api_key="k", model=None)
    assert h.reachable is True
    assert h.auth_ok is True
    assert h.tool_use_ok is False  # never attempted
    assert h.error is None


def test_doctor_flags_model_missing_from_catalog(monkeypatch):
    _install_fake(
        monkeypatch,
        _FakeAdapter(
            models=["gpt-4o"],
            tool_use_response=LLMResponse(
                text="",
                tool_calls=[LLMToolCall(id="x", name="meta_note", arguments={"note": "ok"})],
                usage=LLMUsage(),
            ),
        ),
    )
    h = check_provider(provider="openai", api_key="k", model="gpt-5.5")
    assert h.detail.get("model_missing_from_catalog") is True
