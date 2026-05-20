from pathlib import Path

from moonwing.config import Settings
from moonwing.services.host_cli_discovery import (
    HOST_AI_TOOL_SPECS,
    parse_host_cli_scan_dirs,
    resolve_host_cli_scan_dirs,
    scan_host_ai_tools,
)


def test_parse_host_cli_scan_dirs():
    assert parse_host_cli_scan_dirs("") == []
    assert parse_host_cli_scan_dirs("/a,/b") == ["/a", "/b"]


def test_scan_finds_tool_in_directory(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_text("#!/bin/sh\necho claude\n", encoding="utf-8")
    fake.chmod(0o755)

    settings = Settings(host_cli_scan_dirs=str(bindir))
    report = scan_host_ai_tools(settings)
    claude = next(t for t in report["tools"] if t["id"] == "claude")
    assert claude["installed"] is True
    assert claude["resolved_path"] == str(fake.resolve())


def test_scan_reports_all_catalog_tools(tmp_path):
    settings = Settings(host_cli_scan_dirs=str(tmp_path))
    report = scan_host_ai_tools(settings)
    assert report["total_tools"] == len(HOST_AI_TOOL_SPECS)
    assert report["installed_count"] == 0


def test_resolve_uses_existing_compose_defaults_when_unconfigured(monkeypatch, tmp_path):
    probe = tmp_path / "host-probe" / "usr" / "local" / "bin"
    probe.mkdir(parents=True)
    tool = probe / "codex"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)

    monkeypatch.setattr(
        "moonwing.services.host_cli_discovery.COMPOSE_DEFAULT_HOST_DIRS",
        (str(probe),),
    )
    dirs = resolve_host_cli_scan_dirs(Settings(host_cli_scan_dirs=""))
    assert str(probe) in dirs
