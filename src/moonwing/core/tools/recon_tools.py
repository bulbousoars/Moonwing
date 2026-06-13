"""Real network reconnaissance tool handlers.

These wire the registry's RECON/SCAN stubs to actual implementations so the
network ReAct agent can fan out across a Clearwing-style toolchain instead of
relying on nmap alone.

Design rules (mirrors ``clearwing_runner.run_nmap``):
  * Every external-binary tool checks ``shutil.which`` first and returns a
    graceful ``ok=False`` result ("not installed") instead of raising, so a
    missing tool on the scanner host degrades the loop rather than killing it.
  * Subprocess calls always carry a timeout; a ``TimeoutExpired`` becomes a
    graceful failure, not an exception that unwinds the agent.
  * Handlers return structured ``output`` where parsing is cheap and reliable,
    otherwise ``{"raw": <text>}`` so the model still sees the evidence.

``httpx_probe`` is intentionally pure-Python (uses the ``httpx`` library that
is already a Moonwing dependency) so it needs zero extra host packages.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlsplit

from .registry import ToolContext, ToolResult

logger = logging.getLogger("moonwing.core.tools.recon")

# Cap captured tool output so a chatty scanner can't blow the model context.
_MAX_OUTPUT_CHARS = 24_000


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _missing(binary: str) -> ToolResult:
    return ToolResult(
        ok=False,
        output=None,
        error=f"({binary} not installed on scanner host)",
    )


def _run_cli(
    cmd: list[str],
    *,
    timeout: int,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess capturing text stdout/stderr. Raises TimeoutExpired."""
    logger.info("recon tool: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
    )


def _timeout_seconds(args: dict[str, Any], ctx: ToolContext, default: int = 300) -> int:
    raw = args.get("timeout") or ctx.timeout_seconds or default
    return max(15, min(int(raw), 1800))


def _host_port(value: str, default_port: int = 443) -> str:
    """Normalize a URL or host into ``host:port`` for host-oriented scanners."""
    candidate = value.strip()
    if "://" in candidate:
        parts = urlsplit(candidate)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"{host}:{port}"
    if ":" in candidate and not candidate.startswith("["):
        return candidate
    return f"{candidate}:{default_port}"


def _normalize_url(value: str) -> str:
    candidate = value.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    return candidate


# ---------------------------------------------------------------------------
# httpx_probe — pure Python, no external binary
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Cheap response-header → technology hints. Not exhaustive; the agent can dig
# deeper with whatweb/nuclei when a hint is interesting.
_TECH_HEADER_HINTS = ("server", "x-powered-by", "x-aspnet-version", "x-generator", "via")


def _httpx_probe(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    import httpx

    url = _normalize_url(args.get("url") or ctx.target_address or "")
    if not url or url == "http://":
        return ToolResult(ok=False, output=None, error="url is required")

    follow_redirects = bool(args.get("follow_redirects", True))
    timeout = _timeout_seconds(args, ctx, default=30)

    try:
        with httpx.Client(
            follow_redirects=follow_redirects,
            timeout=min(timeout, 60),
            verify=False,  # scanning targets often have self-signed/expired certs
            headers={"User-Agent": "moonwing-recon/1.0"},
        ) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, output=None, error=f"httpx request failed: {exc}")

    body = resp.text or ""
    title_match = _TITLE_RE.search(body)
    title = title_match.group(1).strip() if title_match else None
    tech = {
        key: resp.headers[key]
        for key in _TECH_HEADER_HINTS
        if key in resp.headers
    }
    redirect_chain = [str(r.url) for r in resp.history] + [str(resp.url)]

    output = {
        "url": url,
        "final_url": str(resp.url),
        "status_code": resp.status_code,
        "reason": resp.reason_phrase,
        "title": _truncate(title, 300) if title else None,
        "server": resp.headers.get("server"),
        "content_type": resp.headers.get("content-type"),
        "content_length": resp.headers.get("content-length") or len(resp.content),
        "scheme": urlsplit(str(resp.url)).scheme,
        "tech_hints": tech,
        "redirect_chain": redirect_chain if len(redirect_chain) > 1 else [],
        "set_cookie": "set-cookie" in resp.headers,
    }
    return ToolResult(ok=True, output=output)


# ---------------------------------------------------------------------------
# whatweb — web technology fingerprinting (JSON)
# ---------------------------------------------------------------------------

def _whatweb(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if shutil.which("whatweb") is None:
        return _missing("whatweb")
    url = _normalize_url(args.get("url") or ctx.target_address or "")
    if not url or url == "http://":
        return ToolResult(ok=False, output=None, error="url is required")
    timeout = _timeout_seconds(args, ctx, default=120)

    cmd = ["whatweb", "--quiet", "--no-errors", "--log-json=-", url]
    try:
        proc = _run_cli(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output=None, error=f"(whatweb timed out after {timeout}s)")

    plugins: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        for name, detail in (entry.get("plugins") or {}).items():
            plugins.append({"plugin": name, "detail": detail})

    if not plugins:
        return ToolResult(ok=True, output={"url": url, "raw": _truncate(proc.stdout or proc.stderr)})
    return ToolResult(ok=True, output={"url": url, "plugins": plugins})


# ---------------------------------------------------------------------------
# sslscan — TLS / cipher audit
# ---------------------------------------------------------------------------

def _sslscan(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if shutil.which("sslscan") is None:
        return _missing("sslscan")
    target = args.get("target") or ctx.target_address
    if not target:
        return ToolResult(ok=False, output=None, error="target is required")
    host_port = _host_port(target, default_port=443)
    timeout = _timeout_seconds(args, ctx, default=120)

    cmd = ["sslscan", "--no-colour", host_port]
    try:
        proc = _run_cli(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output=None, error=f"(sslscan timed out after {timeout}s)")

    text = proc.stdout or proc.stderr or ""
    # Surface the cheap red flags without parsing the whole report.
    weak = sorted(
        {
            proto
            for proto in ("SSLv2", "SSLv3", "TLSv1.0", "TLSv1.1")
            if re.search(rf"{re.escape(proto)}\s+enabled", text)
        }
    )
    return ToolResult(
        ok=True,
        output={"target": host_port, "weak_protocols": weak, "raw": _truncate(text)},
    )


# ---------------------------------------------------------------------------
# nikto — web server vulnerability scan
# ---------------------------------------------------------------------------

def _nikto(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if shutil.which("nikto") is None:
        return _missing("nikto")
    url = _normalize_url(args.get("url") or ctx.target_address or "")
    if not url or url == "http://":
        return ToolResult(ok=False, output=None, error="url is required")
    timeout = _timeout_seconds(args, ctx, default=600)

    cmd = ["nikto", "-h", url, "-nointeractive", "-ask", "no"]
    tuning = args.get("tuning")
    if tuning:
        cmd += ["-Tuning", str(tuning)]
    try:
        proc = _run_cli(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output=None, error=f"(nikto timed out after {timeout}s)")

    text = proc.stdout or proc.stderr or ""
    # nikto prefixes findings with "+ "; pull them out as a quick item list.
    items = [ln.strip()[2:].strip() for ln in text.splitlines() if ln.strip().startswith("+ ")]
    return ToolResult(
        ok=True,
        output={"url": url, "items": items, "raw": _truncate(text)},
    )


# ---------------------------------------------------------------------------
# ffuf — content discovery (directory/file fuzzing)
# ---------------------------------------------------------------------------

_DEFAULT_WORDLISTS = (
    "/usr/share/seclists/Discovery/Web-Content/common.txt",
    "/usr/share/wordlists/dirb/common.txt",
    "/usr/share/wordlists/dirbuster/directory-list-2.3-small.txt",
)


def _resolve_wordlist(explicit: str | None) -> str | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("MOONWING_FFUF_WORDLIST")
    if env:
        candidates.append(env)
    candidates.extend(_DEFAULT_WORDLISTS)
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _ffuf(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if shutil.which("ffuf") is None:
        return _missing("ffuf")
    base = _normalize_url(args.get("url") or ctx.target_address or "")
    if not base or base == "http://":
        return ToolResult(ok=False, output=None, error="url is required")

    wordlist = _resolve_wordlist(args.get("wordlist"))
    if wordlist is None:
        return ToolResult(
            ok=False,
            output=None,
            error="(no ffuf wordlist found; set MOONWING_FFUF_WORDLIST or pass wordlist)",
        )

    fuzz_url = base.rstrip("/") + "/FUZZ"
    match_codes = args.get("match_codes") or "200,204,301,302,307,401,403"
    timeout = _timeout_seconds(args, ctx, default=600)

    out_fd, out_path = tempfile.mkstemp(prefix="moonwing-ffuf-", suffix=".json")
    os.close(out_fd)
    try:
        cmd = [
            "ffuf",
            "-u", fuzz_url,
            "-w", wordlist,
            "-mc", str(match_codes),
            "-of", "json",
            "-o", out_path,
            "-s",
        ]
        try:
            _run_cli(cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=None, error=f"(ffuf timed out after {timeout}s)")

        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = {}
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass

    results = [
        {
            "url": r.get("url"),
            "status": r.get("status"),
            "length": r.get("length"),
            "words": r.get("words"),
        }
        for r in (data.get("results") or [])
    ]
    return ToolResult(
        ok=True,
        output={"base_url": base, "wordlist": wordlist, "hits": results, "hit_count": len(results)},
    )


# ---------------------------------------------------------------------------
# nuclei — templated vulnerability scan (JSONL)
# ---------------------------------------------------------------------------

def _nuclei(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if shutil.which("nuclei") is None:
        return _missing("nuclei")
    target = _normalize_url(args.get("target") or ctx.target_address or "")
    if not target or target == "http://":
        return ToolResult(ok=False, output=None, error="target is required")
    timeout = _timeout_seconds(args, ctx, default=900)

    cmd = ["nuclei", "-u", target, "-jsonl", "-silent", "-duc", "-nc"]
    templates = args.get("templates")
    if templates:
        if isinstance(templates, str):
            templates = [templates]
        for tag in templates:
            cmd += ["-tags", str(tag)]
    severity = args.get("severity")
    if severity:
        cmd += ["-severity", str(severity)]

    try:
        proc = _run_cli(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output=None, error=f"(nuclei timed out after {timeout}s)")

    matches: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        info = entry.get("info") or {}
        matches.append(
            {
                "template_id": entry.get("template-id") or entry.get("templateID"),
                "name": info.get("name"),
                "severity": info.get("severity"),
                "matched_at": entry.get("matched-at") or entry.get("host"),
                "type": entry.get("type"),
            }
        )
    return ToolResult(
        ok=True,
        output={"target": target, "match_count": len(matches), "matches": matches},
    )


# Exported map so seed.py can wire handlers without importing each by name.
RECON_HANDLERS = {
    "httpx_probe": _httpx_probe,
    "whatweb": _whatweb,
    "sslscan": _sslscan,
    "nikto": _nikto,
    "ffuf": _ffuf,
    "nuclei_scan": _nuclei,
}
