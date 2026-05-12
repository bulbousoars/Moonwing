from moonwing.services.ai_provider_probe import (
    PROVIDER_DEFAULT_MODELS,
    ProviderProbeError,
    build_probe_request,
    require_api_key,
)


def test_build_probe_request_for_openai_compatible_provider():
    request = build_probe_request(
        provider="openai",
        api_key="sk-test",
        model="gpt-test",
    )

    assert request["url"] == "https://api.openai.com/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer sk-test"
    assert request["json"]["model"] == "gpt-test"
    assert request["json"]["response_format"] == {"type": "json_object"}


def test_build_probe_request_for_anthropic_provider():
    request = build_probe_request(
        provider="anthropic",
        api_key="claude-test",
        model="claude-test-model",
    )

    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "claude-test"
    assert request["json"]["model"] == "claude-test-model"


def test_build_probe_request_rejects_missing_cloud_api_key():
    try:
        build_probe_request(provider="google", api_key="", model="gemini-test")
    except ProviderProbeError as exc:
        assert "API key is required" in str(exc)
    else:
        raise AssertionError("missing API key should fail")


def test_ollama_does_not_require_api_key():
    assert require_api_key("ollama") is False
    assert PROVIDER_DEFAULT_MODELS["ollama"]


def test_cli_binary_executable_absolute_file(tmp_path):
    exe = tmp_path / "fakeclaude"
    exe.write_text("#!/bin/sh\necho hi\n")
    exe.chmod(0o755)
    from moonwing.services.ai_provider_probe import cli_binary_executable

    assert cli_binary_executable(str(exe)) is True


def test_cli_binary_executable_missing_absolute(tmp_path):
    missing = tmp_path / "nope"
    from moonwing.services.ai_provider_probe import cli_binary_executable

    assert cli_binary_executable(str(missing)) is False


def test_discover_ai_cli_tools_shape(monkeypatch):
    from moonwing.config import Settings
    from moonwing.services import ai_provider_probe as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(mod.os.path, "isfile", lambda _p: False)

    r = mod.discover_ai_cli_tools(Settings(), process_label="test-proc")
    assert r["process_label"] == "test-proc"
    assert "note" in r
    assert len(r["tools"]) == 3
    assert all(not t["available"] for t in r["tools"])
    assert r["tools"][0]["id"] == "claude"
