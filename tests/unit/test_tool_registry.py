from __future__ import annotations

import pytest

from moonwing.core.tools import (
    DEFAULT_REGISTRY,
    Capability,
    ToolDomain,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    UnknownToolError,
)
from moonwing.core.tools.registry import ToolContext
from moonwing.core.tools.seed import (
    HTTPX_SPEC,
    NMAP_SPEC,
    SEMGREP_SPEC,
    register_seed_tools,
)


# ----------------------- registration / lookup --------------------------


def test_default_registry_has_seed_tools():
    names = DEFAULT_REGISTRY.names()
    assert "nmap_scan" in names
    assert "httpx_probe" in names
    assert "semgrep_scan" in names


def test_register_then_get():
    reg = ToolRegistry()
    reg.register(NMAP_SPEC)
    assert "nmap_scan" in reg
    assert reg.get("nmap_scan") is NMAP_SPEC


def test_duplicate_registration_raises_without_replace():
    reg = ToolRegistry()
    reg.register(NMAP_SPEC)
    with pytest.raises(ValueError):
        reg.register(NMAP_SPEC)


def test_replace_overwrites_silently():
    reg = ToolRegistry()
    reg.register(NMAP_SPEC)
    other = ToolSpec(
        name="nmap_scan",
        domain=ToolDomain.SCAN,
        description="x",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
        capabilities=frozenset(),
        handler=lambda a, c: ToolResult(ok=True, output=None),
    )
    reg.register(other, replace=True)
    assert reg.get("nmap_scan") is other


def test_unknown_tool_raises():
    reg = ToolRegistry()
    with pytest.raises(UnknownToolError):
        reg.get("does_not_exist")


def test_unregister_is_idempotent():
    reg = ToolRegistry()
    reg.register(NMAP_SPEC)
    reg.unregister("nmap_scan")
    reg.unregister("nmap_scan")  # second call must not raise
    assert "nmap_scan" not in reg


# ----------------------- domain + capability filters --------------------


def test_list_by_domain():
    reg = ToolRegistry()
    register_seed_tools(reg)
    scans = reg.list_by_domain(ToolDomain.SCAN)
    assert any(t.name == "nmap_scan" for t in scans)
    hunts = reg.list_by_domain(ToolDomain.HUNT)
    assert any(t.name == "semgrep_scan" for t in hunts)


def test_filter_by_allowed_capabilities():
    reg = ToolRegistry()
    register_seed_tools(reg)
    # Only allow non-sandboxed network probes — semgrep should drop out.
    out = reg.filter(allowed_capabilities={Capability.NETWORK_EGRESS, Capability.LONG_RUNNING})
    out_names = {t.name for t in out}
    assert "nmap_scan" in out_names
    assert "nuclei_scan" in out_names        # network_egress + long_running, no sandbox
    assert "semgrep_scan" not in out_names   # needs_sandbox


def test_filter_by_denied_capabilities():
    reg = ToolRegistry()
    register_seed_tools(reg)
    out = reg.filter(denied_capabilities={Capability.NEEDS_SANDBOX})
    out_names = {t.name for t in out}
    assert "semgrep_scan" not in out_names
    assert "nuclei_scan" in out_names  # no longer sandbox-gated
    assert "nmap_scan" in out_names


def test_filter_by_domain_set():
    reg = ToolRegistry()
    register_seed_tools(reg)
    out = reg.filter(domains={ToolDomain.RECON, ToolDomain.DATA})
    domains = {t.domain for t in out}
    assert domains <= {ToolDomain.RECON, ToolDomain.DATA}


# ----------------------- schema export ---------------------------------


def test_openai_schema_shape():
    schema = NMAP_SPEC.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "nmap_scan"
    assert schema["function"]["parameters"]["type"] == "object"


def test_anthropic_schema_shape():
    schema = HTTPX_SPEC.to_anthropic_schema()
    assert schema["name"] == "httpx_probe"
    assert "input_schema" in schema
    assert schema["input_schema"]["required"] == ["url"]


def test_registry_exports_all_schemas():
    reg = ToolRegistry()
    register_seed_tools(reg)
    openai = reg.openai_schemas()
    anthropic = reg.anthropic_schemas()
    assert len(openai) == len(reg) == len(anthropic)
    assert {s["function"]["name"] for s in openai} == set(reg.names())


def test_subset_export_by_name():
    reg = ToolRegistry()
    register_seed_tools(reg)
    out = reg.openai_schemas(["nmap_scan"])
    assert len(out) == 1
    assert out[0]["function"]["name"] == "nmap_scan"


# ----------------------- handler invocation ----------------------------


def test_nmap_handler_requires_target():
    ctx = ToolContext(run_id=None, target_address=None)
    result = NMAP_SPEC.handler({}, ctx)
    assert not result.ok
    assert "target is required" in (result.error or "")


def test_stub_handler_reports_not_yet_wired():
    # semgrep remains a Phase-2 stub (needs a sandbox runtime decision).
    ctx = ToolContext(run_id=None)
    result = SEMGREP_SPEC.handler({"path": "/tmp/x"}, ctx)
    assert not result.ok
    assert "not yet wired up" in (result.error or "")
