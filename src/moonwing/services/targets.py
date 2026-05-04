from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def normalize_target_metadata(*, target_type: str, display_name: str, source_metadata: dict) -> dict:
    metadata = dict(source_metadata or {})
    if target_type != "website":
        return metadata

    raw_url = str(metadata.get("url") or display_name or "").strip()
    if "://" not in raw_url:
        raw_url = f"https://{raw_url}"
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Website targets must use http or https URLs.")
    if not parsed.hostname:
        raise ValueError("Website target URL must include a hostname.")

    port = parsed.port
    scan_port = str(port or (443 if parsed.scheme == "https" else 80))
    normalized_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path or "",
        parsed.params or "",
        parsed.query or "",
        "",
    ))
    metadata.update(
        {
            "url": normalized_url,
            "address": parsed.hostname,
            "scheme": parsed.scheme,
            "path": parsed.path or "/",
            "scan_ports": scan_port,
            "input_kind": "url",
            "website": True,
        }
    )
    return metadata
