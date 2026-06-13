"""Core types for the tool registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable


class ToolDomain(StrEnum):
    """Coarse grouping used for prompt construction and policy gating."""

    SCAN = "scan"        # active probes (nmap, masscan)
    RECON = "recon"      # passive enumeration (httpx, whatweb, sslscan, dns)
    HUNT = "hunt"        # source-code vulnerability search (semgrep, taint)
    EXPLOIT = "exploit"  # PoC compilation, attempted exploitation
    OPS = "ops"          # shell, file IO, process control (sandbox-required)
    DATA = "data"        # CVE lookup, vendor DB queries, KG reads
    META = "meta"        # planning, memory, summarization


class Capability(StrEnum):
    """Coarse policy tags. Runtime profiles allow/deny by capability."""

    NEEDS_SANDBOX = "needs_sandbox"
    NETWORK_EGRESS = "network_egress"
    FS_WRITE = "fs_write"
    LONG_RUNNING = "long_running"
    DESTRUCTIVE = "destructive"
    READS_KG = "reads_kg"
    WRITES_KG = "writes_kg"


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a single tool invocation, returned to the ReAct loop."""

    ok: bool
    output: Any
    error: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


# A handler is sync today; we'll add async support when the ReAct loop
# needs streaming long-running tools. ``ctx`` is an opaque per-run context
# the ReAct loop passes through — DB session, run id, KG service, etc.
ToolHandler = Callable[[dict[str, Any], "ToolContext"], ToolResult]


@dataclass
class ToolContext:
    """Runtime context handed to every tool invocation.

    Kept intentionally small — tools should declare what they need; the
    ReAct loop populates only those fields. ``extras`` is the escape
    hatch for one-off needs without bloating the core type.
    """

    run_id: Any  # UUID, but kept Any to avoid an import cycle into core
    session: Any = None  # SQLAlchemy session for KG/audit, may be None for pure-IO tools
    target_address: str | None = None
    workdir: str | None = None
    timeout_seconds: int = 300
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    """Static metadata + handler for a single tool."""

    name: str
    domain: ToolDomain
    description: str
    params_schema: dict[str, Any]   # JSON Schema (object) describing arguments
    capabilities: frozenset[Capability]
    handler: ToolHandler

    def to_openai_schema(self) -> dict[str, Any]:
        """Render in OpenAI tool-use format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Render in Anthropic tool-use format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.params_schema,
        }


class UnknownToolError(LookupError):
    pass


class ToolRegistry:
    """In-process map of tool name → ``ToolSpec``.

    Mutation is allowed at import time (seeders, plugins). The ReAct
    loop should treat its frozen-at-startup snapshot as read-only.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # --- mutation ---------------------------------------------------

    def register(self, spec: ToolSpec, *, replace: bool = False) -> None:
        if not replace and spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # --- lookup -----------------------------------------------------

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(name) from exc

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterable[ToolSpec]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_by_domain(self, domain: ToolDomain) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.domain == domain]

    def filter(
        self,
        *,
        domains: Iterable[ToolDomain] | None = None,
        allowed_capabilities: Iterable[Capability] | None = None,
        denied_capabilities: Iterable[Capability] | None = None,
    ) -> list[ToolSpec]:
        """Return tools the caller's policy allows it to run.

        ``allowed_capabilities``: if given, a tool's capabilities must be a
            subset of this set.
        ``denied_capabilities``: if given, a tool with ANY of these
            capabilities is excluded.
        """
        domain_set = set(domains) if domains is not None else None
        allow_set = set(allowed_capabilities) if allowed_capabilities is not None else None
        deny_set = set(denied_capabilities) if denied_capabilities is not None else set()

        out: list[ToolSpec] = []
        for spec in self._tools.values():
            if domain_set is not None and spec.domain not in domain_set:
                continue
            if allow_set is not None and not spec.capabilities.issubset(allow_set):
                continue
            if spec.capabilities & deny_set:
                continue
            out.append(spec)
        return out

    # --- schema export ---------------------------------------------

    def openai_schemas(
        self, names: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        return [self.get(n).to_openai_schema() for n in (names or self.names())]

    def anthropic_schemas(
        self, names: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        return [self.get(n).to_anthropic_schema() for n in (names or self.names())]


# The application-wide default registry. Tests can build their own.
DEFAULT_REGISTRY = ToolRegistry()
