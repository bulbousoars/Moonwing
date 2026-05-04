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
