"""Built-in tool registrations.

Currently registers the nmap wrapper (real, calls existing runner) plus
stubs for the Phase-1 expansion (httpx, nikto, semgrep, nuclei, …). The
stubs raise ``NotImplementedError`` deliberately — they are visible to
the schema exporter so prompt construction can preview the full surface
without us pretending a sandbox decision has been made.
"""

from __future__ import annotations

from typing import Any

from .recon_tools import RECON_HANDLERS
from .registry import (
    Capability,
    ToolContext,
    ToolDomain,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


# ---------------------------------------------------------------------------
# Real handlers
# ---------------------------------------------------------------------------

def _nmap_scan(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Wrap the existing nmap runner with a ToolResult envelope."""
    from moonwing.worker.clearwing_runner import run_nmap

    target = args.get("target") or ctx.target_address
    if not target:
        return ToolResult(ok=False, output=None, error="target is required")

    ports = args.get("ports")
    timeout = int(args.get("timeout") or ctx.timeout_seconds or 300)

    raw = run_nmap(target, ports=ports, timeout=timeout)
    return ToolResult(
        ok=True,
        output={"raw": raw, "target": target, "ports": ports},
        artifacts=[],
    )


# ---------------------------------------------------------------------------
# Phase-1 placeholders — surface metadata to the model but block execution
# until the sandbox runtime story is settled (Phase 2).
# ---------------------------------------------------------------------------

def _not_implemented(name: str):
    def handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=False,
            output=None,
            error=f"tool {name!r} is registered but not yet wired up (Phase 1)",
        )
    return handler


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

NMAP_SPEC = ToolSpec(
    name="nmap_scan",
    domain=ToolDomain.SCAN,
    description=(
        "Run an nmap service-detection scan against a host and return the "
        "raw textual output. Use this first when reconning a network target."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Hostname or IP. Defaults to the run's target if omitted.",
            },
            "ports": {
                "type": "string",
                "description": (
                    "nmap port spec (e.g. '22,80,443' or '1-1000'). "
                    "If omitted, scans the top 1000 ports."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Per-call timeout in seconds.",
                "minimum": 30,
                "maximum": 1800,
            },
        },
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS, Capability.LONG_RUNNING}),
    handler=_nmap_scan,
)


HTTPX_SPEC = ToolSpec(
    name="httpx_probe",
    domain=ToolDomain.RECON,
    description=(
        "Probe an HTTP/S endpoint and return status, title, server header, "
        "TLS details, and detected web technologies."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to probe."},
            "follow_redirects": {"type": "boolean", "default": True},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS}),
    handler=RECON_HANDLERS["httpx_probe"],
)


WHATWEB_SPEC = ToolSpec(
    name="whatweb",
    domain=ToolDomain.RECON,
    description=(
        "Fingerprint the web technologies behind a URL (server, framework, "
        "CMS, JS libraries, headers) using whatweb. Run after httpx_probe to "
        "enumerate the stack before targeted vuln scanning."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL. Defaults to the run's target."},
            "timeout": {"type": "integer", "minimum": 15, "maximum": 1800},
        },
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS}),
    handler=RECON_HANDLERS["whatweb"],
)


SSLSCAN_SPEC = ToolSpec(
    name="sslscan",
    domain=ToolDomain.RECON,
    description=(
        "Audit a host's TLS configuration: supported protocol versions, "
        "cipher suites, and certificate details. Flags weak/deprecated "
        "protocols (SSLv2/3, TLS 1.0/1.1)."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "host:port or URL. Defaults to the run's target on :443.",
            },
            "timeout": {"type": "integer", "minimum": 15, "maximum": 1800},
        },
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS}),
    handler=RECON_HANDLERS["sslscan"],
)


NIKTO_SPEC = ToolSpec(
    name="nikto",
    domain=ToolDomain.SCAN,
    description=(
        "Run the nikto web server scanner against a URL to surface dangerous "
        "files, outdated software, and common misconfigurations. Slow — use "
        "once you know a web service is present."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL. Defaults to the run's target."},
            "tuning": {
                "type": "string",
                "description": "Optional nikto -Tuning string to scope the scan (e.g. '123b').",
            },
            "timeout": {"type": "integer", "minimum": 15, "maximum": 1800},
        },
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS, Capability.LONG_RUNNING}),
    handler=RECON_HANDLERS["nikto"],
)


FFUF_SPEC = ToolSpec(
    name="ffuf",
    domain=ToolDomain.RECON,
    description=(
        "Discover hidden directories and files by fuzzing the URL path with a "
        "wordlist (ffuf). Returns paths that responded with interesting status "
        "codes. Use to map the attack surface of a web app."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Base URL. FUZZ is appended to the path."},
            "wordlist": {
                "type": "string",
                "description": "Optional wordlist path. Falls back to a host default.",
            },
            "match_codes": {
                "type": "string",
                "description": "Comma-separated HTTP status codes to keep. Defaults to common hits.",
            },
            "timeout": {"type": "integer", "minimum": 15, "maximum": 1800},
        },
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS, Capability.LONG_RUNNING}),
    handler=RECON_HANDLERS["ffuf"],
)


NUCLEI_SPEC = ToolSpec(
    name="nuclei_scan",
    domain=ToolDomain.SCAN,
    description=(
        "Run the nuclei vulnerability scanner against a URL using a "
        "specific template set."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "URL/host. Defaults to the run's target."},
            "templates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Nuclei template tags (e.g. 'cve', 'exposure', 'misconfig').",
            },
            "severity": {
                "type": "string",
                "description": "Optional severity filter, e.g. 'critical,high'.",
            },
            "timeout": {"type": "integer", "minimum": 15, "maximum": 1800},
        },
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.NETWORK_EGRESS, Capability.LONG_RUNNING}),
    handler=RECON_HANDLERS["nuclei_scan"],
)


SEMGREP_SPEC = ToolSpec(
    name="semgrep_scan",
    domain=ToolDomain.HUNT,
    description=(
        "Run semgrep over a source-tree path with the given ruleset; "
        "return matches as a JSON list."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "config": {
                "type": "string",
                "description": "Ruleset id, e.g. 'p/owasp-top-ten'.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.LONG_RUNNING, Capability.NEEDS_SANDBOX}),
    handler=_not_implemented("semgrep_scan"),
)


def _kg_query(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Look up a KG node + its outgoing neighbors."""
    if ctx.session is None:
        return ToolResult(ok=False, output=None, error="kg_query requires a DB session")
    kind = args.get("kind")
    stable_key = args.get("stable_key")
    if not kind or not stable_key:
        return ToolResult(
            ok=False, output=None, error="kind and stable_key are required"
        )

    from moonwing.services.kg import find_node, get_neighbors

    node = find_node(ctx.session, kind=kind, stable_key=stable_key)
    if node is None:
        return ToolResult(ok=True, output={"node": None, "neighbors": []})

    edge_kind = args.get("edge_kind") or None
    neighbors = []
    for edge, neighbor in get_neighbors(
        ctx.session, node_id=node.id, edge_kind=edge_kind, direction="out"
    ):
        neighbors.append(
            {
                "edge_kind": edge.kind,
                "node_kind": neighbor.kind,
                "stable_key": neighbor.stable_key,
                "attrs": dict(neighbor.attrs or {}),
            }
        )

    return ToolResult(
        ok=True,
        output={
            "node": {
                "kind": node.kind,
                "stable_key": node.stable_key,
                "attrs": dict(node.attrs or {}),
                "confidence": node.confidence,
            },
            "neighbors": neighbors,
        },
    )


def _meta_note(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Push a planning note into the run's activity log."""
    note = (args.get("note") or "").strip()
    if not note:
        return ToolResult(ok=False, output=None, error="note is required")

    callback = ctx.extras.get("on_note") if ctx.extras else None
    if callable(callback):
        try:
            callback(note)
        except Exception as exc:  # pragma: no cover — defensive
            return ToolResult(
                ok=False, output=None, error=f"note callback failed: {exc}"
            )
    return ToolResult(ok=True, output={"recorded": True, "note": note})


KG_QUERY_SPEC = ToolSpec(
    name="kg_query",
    domain=ToolDomain.DATA,
    description=(
        "Look up a knowledge-graph node by (kind, stable_key) and return "
        "its attributes plus outgoing neighbors. Use to check what the "
        "system already knows about a host/service/CVE before re-scanning."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "stable_key": {"type": "string"},
            "edge_kind": {"type": "string"},
        },
        "required": ["kind", "stable_key"],
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.READS_KG}),
    handler=_kg_query,
)


SLEEP_SPEC = ToolSpec(
    name="meta_note",
    domain=ToolDomain.META,
    description=(
        "Record a planning note in the run's activity log without taking "
        "an external action. Use to externalize reasoning between probe steps."
    ),
    params_schema={
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
        "additionalProperties": False,
    },
    capabilities=frozenset(),
    handler=_meta_note,
)


from .file_tools import FILE_READ_SPECS

_SEED_SPECS = (
    NMAP_SPEC,
    HTTPX_SPEC,
    WHATWEB_SPEC,
    SSLSCAN_SPEC,
    NIKTO_SPEC,
    FFUF_SPEC,
    NUCLEI_SPEC,
    SEMGREP_SPEC,
    KG_QUERY_SPEC,
    SLEEP_SPEC,
    *FILE_READ_SPECS,
)


def register_seed_tools(registry: ToolRegistry) -> None:
    """Idempotently register all built-in tools onto ``registry``."""
    for spec in _SEED_SPECS:
        registry.register(spec, replace=True)
