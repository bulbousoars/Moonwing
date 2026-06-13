"""Provider reachability / auth / tool-use health checks.

The doctor is read-only and idempotent — safe to invoke from a CLI, an
admin UI button, or a startup hook. Designed to give a single per-provider
verdict the operator can act on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from moonwing.core.llm import LLMError, get_adapter
from moonwing.core.tools import DEFAULT_REGISTRY


@dataclass
class ProviderHealth:
    provider: str
    model: str | None
    reachable: bool
    auth_ok: bool
    tool_use_ok: bool
    models_listed: int | None = None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PING_MESSAGES = [
    {"role": "system", "content": "Respond with the single word 'pong'."},
    {"role": "user", "content": "ping"},
]


def _tool_use_ping() -> list[dict[str, Any]]:
    """A one-tool prompt that forces the model to emit a tool call.

    Using ``meta_note`` (parameter-light, no side effects) so we exercise
    the round-trip without burning credits on a real probe.
    """
    return [
        {
            "role": "system",
            "content": (
                "You MUST respond by calling the meta_note tool exactly once "
                "with note='ok'. Do not produce any other text."
            ),
        },
        {"role": "user", "content": "Run the health check."},
    ]


def check_provider(
    *,
    provider: str,
    api_key: str,
    model: str | None = None,
    timeout: int = 30,
) -> ProviderHealth:
    """Run reachability, auth, and tool-use probes for one provider."""
    health = ProviderHealth(
        provider=provider, model=model, reachable=False, auth_ok=False, tool_use_ok=False
    )

    try:
        adapter = get_adapter(provider)
    except ValueError as exc:
        health.error = str(exc)
        return health

    # 1. list_models — covers reachability + auth in one shot.
    try:
        ids = adapter.list_models(api_key=api_key, timeout=timeout)
        health.reachable = True
        health.auth_ok = True
        health.models_listed = len(ids)
        if model and ids and model not in ids:
            health.detail["model_missing_from_catalog"] = True
    except LLMError as exc:
        health.error = str(exc)
        # 401/403 means we *reached* the server but auth is wrong.
        if exc.status_code in (401, 403):
            health.reachable = True
        return health
    except Exception as exc:  # network / DNS / TLS
        health.error = f"{type(exc).__name__}: {exc}"
        return health

    if not model:
        return health

    # 2. tool-use ping — forces a tool_call with the meta_note schema.
    try:
        meta_note_schema = DEFAULT_REGISTRY.get("meta_note").to_openai_schema()
        resp = adapter.chat_with_tools(
            messages=_tool_use_ping(),
            tools=[meta_note_schema],
            model=model,
            api_key=api_key,
            max_tokens=128,
            timeout=timeout,
        )
        health.tool_use_ok = any(tc.name == "meta_note" for tc in resp.tool_calls)
        if not health.tool_use_ok:
            health.detail["text_response_only"] = (resp.text or "")[:160]
    except LLMError as exc:
        health.error = f"tool-use ping failed: {exc}"
    except Exception as exc:
        health.error = f"tool-use ping crashed: {type(exc).__name__}: {exc}"

    return health


def check_providers(specs: list[dict[str, Any]], *, timeout: int = 30) -> list[ProviderHealth]:
    """Bulk variant: ``specs`` is ``[{"provider", "api_key", "model"}]``."""
    out: list[ProviderHealth] = []
    for s in specs:
        out.append(
            check_provider(
                provider=s["provider"],
                api_key=s["api_key"],
                model=s.get("model"),
                timeout=timeout,
            )
        )
    return out
