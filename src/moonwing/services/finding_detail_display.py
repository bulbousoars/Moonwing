from __future__ import annotations

import re
from collections.abc import Iterable


def build_finding_details(*, stored_details: dict | None, enrichment: dict[str, str]) -> dict:
    stored_details = stored_details or {}

    return {
        "product_affected": _display_value(stored_details.get("product")) or enrichment.get("product_affected") or "Unknown",
        "host_affected": _display_value(stored_details.get("affected_hosts")) or enrichment.get("host_affected") or "Unknown",
        "affected_ports": _display_value(stored_details.get("affected_ports")) or "Unknown",
        "scan_type": stored_details.get("scan_type") or enrichment.get("scan_type") or "Unknown",
        "description": str(stored_details.get("description") or ""),
        "impact": str(stored_details.get("impact") or ""),
        "remediation": str(stored_details.get("remediation") or ""),
        "confidence": str(stored_details.get("confidence") or ""),
        "references": _as_list(stored_details.get("references")),
    }


def format_evidence_refs(evidence_refs: Iterable[object]) -> list[dict[str, str]]:
    formatted = []
    for ref in evidence_refs or []:
        raw = str(ref)
        formatted.append({
            "raw": raw,
            "host": _first_match(raw, r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            "port": _first_match(raw, r"\b\d{1,5}/(?:tcp|udp)\b"),
            "service": _extract_service(raw),
        })
    return formatted


def _display_value(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _as_list(value: object) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _extract_service(text: str) -> str:
    port_match = re.search(r"\b\d{1,5}/(?:tcp|udp)\b", text, flags=re.IGNORECASE)
    if not port_match:
        return ""

    remainder = text[port_match.end():].strip()
    remainder = re.sub(r"^(?:open|closed|filtered)\s+", "", remainder, flags=re.IGNORECASE)
    return remainder.strip(" -:")[:160]
