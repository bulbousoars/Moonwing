"""Tool registry — the surface the ReAct loop binds against.

A ``ToolSpec`` is a single callable security tool (nmap, httpx, semgrep,
sandboxed exploit attempt, …) annotated with:

  * a domain — coarse grouping for prompt construction and policy gating
  * a JSON-Schema for its arguments — used to render provider-native
    tool definitions (OpenAI/Anthropic) and to validate model output
  * capability tags — ``needs_sandbox``, ``network_egress``, ``fs_write``,
    ``long_running`` — let runtime profiles permit or deny categories
  * a handler — the Python callable that actually does the work

Tools never write to the DB directly; results go back through the ReAct
loop, which decides what to persist (findings, KG nodes, artifacts).
"""

from __future__ import annotations

from .registry import (
    DEFAULT_REGISTRY,
    Capability,
    ToolContext,
    ToolDomain,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    UnknownToolError,
)
from .seed import register_seed_tools

__all__ = [
    "Capability",
    "DEFAULT_REGISTRY",
    "ToolContext",
    "ToolDomain",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "UnknownToolError",
    "register_seed_tools",
]


# Eager-register the built-in tools so importers see a populated registry.
register_seed_tools(DEFAULT_REGISTRY)
