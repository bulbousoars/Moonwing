from __future__ import annotations

import json
import subprocess

import httpx
import pytest

import moonwing.core.tools.recon_tools as rt
from moonwing.core.tools import ToolContext


def _ctx(**kw) -> ToolContext:
    kw.setdefault("run_id", None)
    return ToolContext(**kw)


def _completed(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=code, stdout=stdout, stderr=stderr)


@pytest.fixture()
def installed(monkeypatch):
    """Pretend every external binary is on PATH."""
    monkeypatch.setattr(rt.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture()
def absent(monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)


# --------------------------- httpx_probe -----------------------------------


def _patch_httpx(monkeypatch, handler):
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        passthrough = {
            k: v for k, v in kwargs.items() if k in {"follow_redirects", "timeout", "headers"}
        }
        return real_client(transport=httpx.MockTransport(handler), **passthrough)

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_httpx_probe_parses_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"server": "nginx/1.25", "content-type": "text/html", "x-powered-by": "PHP/8.2"},
            text="<html><head><title>Login</title></head><body>hi</body></html>",
        )

    _patch_httpx(monkeypatch, handler)
    result = rt._httpx_probe({"url": "http://target.test"}, _ctx())
    assert result.ok
    assert result.output["status_code"] == 200
    assert result.output["title"] == "Login"
    assert result.output["server"] == "nginx/1.25"
    assert result.output["tech_hints"]["x-powered-by"] == "PHP/8.2"


def test_httpx_probe_uses_target_address(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(204, text="")

    _patch_httpx(monkeypatch, handler)
    result = rt._httpx_probe({}, _ctx(target_address="example.test"))
    assert result.ok
    assert seen["url"].startswith("http://example.test")


def test_httpx_probe_requires_url():
    result = rt._httpx_probe({}, _ctx())
    assert not result.ok
    assert "url is required" in result.error


def test_httpx_probe_handles_connection_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _patch_httpx(monkeypatch, handler)
    result = rt._httpx_probe({"url": "http://down.test"}, _ctx())
    assert not result.ok
    assert "httpx request failed" in result.error


# --------------------------- whatweb ---------------------------------------


def test_whatweb_not_installed(absent):
    result = rt._whatweb({"url": "http://t.test"}, _ctx())
    assert not result.ok
    assert "not installed" in result.error


def test_whatweb_parses_plugins(installed, monkeypatch):
    line = json.dumps({"target": "http://t.test", "plugins": {"nginx": {"version": ["1.25"]}}})
    monkeypatch.setattr(rt, "_run_cli", lambda *a, **k: _completed(stdout=line + "\n"))
    result = rt._whatweb({"url": "http://t.test"}, _ctx())
    assert result.ok
    assert result.output["plugins"][0]["plugin"] == "nginx"


def test_whatweb_timeout(installed, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="whatweb", timeout=1)

    monkeypatch.setattr(rt, "_run_cli", boom)
    result = rt._whatweb({"url": "http://t.test"}, _ctx())
    assert not result.ok
    assert "timed out" in result.error


# --------------------------- sslscan ---------------------------------------


def test_sslscan_flags_weak_protocols(installed, monkeypatch):
    text = "SSLv3   enabled\nTLSv1.0   enabled\nTLSv1.2   enabled\n"
    monkeypatch.setattr(rt, "_run_cli", lambda *a, **k: _completed(stdout=text))
    result = rt._sslscan({"target": "https://t.test"}, _ctx())
    assert result.ok
    assert result.output["target"] == "t.test:443"
    assert "SSLv3" in result.output["weak_protocols"]
    assert "TLSv1.0" in result.output["weak_protocols"]


def test_sslscan_not_installed(absent):
    result = rt._sslscan({"target": "t.test"}, _ctx())
    assert not result.ok


# --------------------------- nikto -----------------------------------------


def test_nikto_extracts_items(installed, monkeypatch):
    text = "- Nikto v2.5\n+ Server: nginx\n+ /admin/: Admin login page found.\n"
    monkeypatch.setattr(rt, "_run_cli", lambda *a, **k: _completed(stdout=text))
    result = rt._nikto({"url": "http://t.test"}, _ctx())
    assert result.ok
    assert any("Admin login page" in item for item in result.output["items"])


# --------------------------- ffuf ------------------------------------------


def test_ffuf_no_wordlist(installed, monkeypatch):
    monkeypatch.delenv("MOONWING_FFUF_WORDLIST", raising=False)
    monkeypatch.setattr(rt, "_resolve_wordlist", lambda explicit: None)
    result = rt._ffuf({"url": "http://t.test"}, _ctx())
    assert not result.ok
    assert "wordlist" in result.error


def test_ffuf_parses_hits(installed, tmp_path, monkeypatch):
    wordlist = tmp_path / "words.txt"
    wordlist.write_text("admin\nlogin\n")
    monkeypatch.setattr(rt, "_resolve_wordlist", lambda explicit: str(wordlist))

    def fake_run(cmd, *, timeout, input_text=None):
        out_path = cmd[cmd.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"results": [{"url": "http://t.test/admin", "status": 200, "length": 10, "words": 2}]}, fh)
        return _completed()

    monkeypatch.setattr(rt, "_run_cli", fake_run)
    result = rt._ffuf({"url": "http://t.test"}, _ctx())
    assert result.ok
    assert result.output["hit_count"] == 1
    assert result.output["hits"][0]["url"].endswith("/admin")


# --------------------------- nuclei ----------------------------------------


def test_nuclei_parses_jsonl(installed, monkeypatch):
    lines = "\n".join(
        json.dumps(d)
        for d in [
            {"template-id": "tech-detect", "info": {"name": "Nginx", "severity": "info"}, "matched-at": "t.test"},
            {"template-id": "CVE-2021-1234", "info": {"name": "RCE", "severity": "critical"}, "matched-at": "t.test/x"},
        ]
    )
    monkeypatch.setattr(rt, "_run_cli", lambda *a, **k: _completed(stdout=lines + "\n"))
    result = rt._nuclei({"target": "http://t.test"}, _ctx())
    assert result.ok
    assert result.output["match_count"] == 2
    sevs = {m["severity"] for m in result.output["matches"]}
    assert "critical" in sevs


def test_nuclei_not_installed(absent):
    result = rt._nuclei({"target": "t.test"}, _ctx())
    assert not result.ok
    assert "not installed" in result.error


# --------------------------- registry wiring -------------------------------


def test_recon_tools_registered_real():
    from moonwing.core.tools.registry import ToolRegistry
    from moonwing.core.tools.seed import register_seed_tools

    reg = ToolRegistry()
    register_seed_tools(reg)
    for name in ("httpx_probe", "whatweb", "sslscan", "nikto", "ffuf", "nuclei_scan"):
        assert name in reg, name
    # nuclei must no longer carry the sandbox tag (full fan-out decision)
    from moonwing.core.tools.registry import Capability

    assert Capability.NEEDS_SANDBOX not in reg.get("nuclei_scan").capabilities
