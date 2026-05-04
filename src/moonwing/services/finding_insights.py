from __future__ import annotations

import re


DOC_LINKS = {
    "minio": "https://min.io/docs/",
    "openssh": "https://www.openssh.com/manual.html",
    "portainer": "https://docs.portainer.io/",
    "authentik": "https://docs.goauthentik.io/",
    "nginx": "https://nginx.org/en/docs/",
    "apache": "https://httpd.apache.org/docs/",
    "postgres": "https://www.postgresql.org/docs/",
    "redis": "https://redis.io/docs/latest/",
    "docker": "https://docs.docker.com/",
}


def build_finding_insights(
    *,
    title: str,
    severity: str,
    finding_details: dict,
    stored_details: dict | None,
    evidence_refs: list,
    target_metadata: dict | None,
) -> dict:
    stored_details = stored_details or {}
    target_metadata = target_metadata or {}
    product = finding_details.get("product_affected") or "the affected product"
    host = finding_details.get("host_affected") or target_metadata.get("address") or "Unknown"
    ports = finding_details.get("affected_ports") or "Unknown"
    scan_type = finding_details.get("scan_type") or "Unknown"
    description = finding_details.get("description") or f"Moonwing observed evidence matching this finding during a {scan_type.lower()}."
    impact = finding_details.get("impact") or "The practical impact depends on exposure, authentication requirements, and the affected service configuration."
    remediation = finding_details.get("remediation") or "Review the affected service, restrict unnecessary exposure, apply vendor guidance, and re-run the scan."
    confidence = finding_details.get("confidence") or "not recorded"
    references = list(finding_details.get("references") or [])

    docs = _documentation_links(product, title, references)
    vuln_refs = _vulnerability_references(stored_details, references, evidence_refs, title)

    return {
        "executive_summary": f"{product} on {host} has a {severity} severity finding: {title}.",
        "technical_explanation": f"{description} Evidence indicates {product} on {host} at {ports}.",
        "affected_asset": {
            "Product": product,
            "Host": host,
            "Ports": ports,
            "Scan Type": scan_type,
            "Target Address": str(target_metadata.get("address") or ""),
            "Target Type": str(target_metadata.get("target_type") or ""),
        },
        "risk_reasoning": f"This is marked {severity} severity with {confidence} confidence. {impact}",
        "exploitability": _exploitability(severity=severity, ports=ports, evidence_refs=evidence_refs),
        "remediation_plan": remediation,
        "validation_steps": [
            "Apply the remediation or compensating control.",
            "Re-run the scan",
            "Confirm the affected service, port, or code path no longer appears in new evidence.",
        ],
        "documentation_links": docs,
        "vulnerability_references": vuln_refs,
        "detection_logic": [
            f"Finding title: {title}",
            f"Stored details keys: {', '.join(sorted(stored_details.keys())) if stored_details else 'none'}",
            f"Evidence references reviewed: {len(evidence_refs or [])}",
        ],
        "raw_details": stored_details,
    }


def _documentation_links(product: str, title: str, references: list[str]) -> list[str]:
    blob = f"{product} {title}".lower()
    links = [url for key, url in DOC_LINKS.items() if key in blob]
    links.extend(ref for ref in references if ref.startswith(("http://", "https://")) and ref not in links)
    return links or ["https://owasp.org/www-project-top-ten/"]


def _vulnerability_references(stored_details: dict, references: list[str], evidence_refs: list, title: str) -> list[str]:
    values = [title, *references, *(str(ref) for ref in evidence_refs or [])]
    for key in ("cve", "cves", "vulnerability_id"):
        value = stored_details.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    cves = sorted(set(re.findall(r"CVE-\d{4}-\d{4,7}", " ".join(values), flags=re.IGNORECASE)))
    return [f"https://nvd.nist.gov/vuln/detail/{cve.upper()}" for cve in cves]


def _exploitability(*, severity: str, ports: str, evidence_refs: list) -> str:
    blob = " ".join([ports, *(str(ref) for ref in evidence_refs or [])]).lower()
    if any(token in blob for token in ("open", "/tcp", "/udp", "http", "ssh")):
        return "Direct network access appears possible from the scanner's vantage point. Validate whether authentication, firewall rules, or network segmentation reduce practical exploitability."
    if severity.lower() in {"critical", "high"}:
        return "High-impact finding. Confirm prerequisites such as credentials, network adjacency, and service exposure."
    return "Exploitability is not directly proven by the stored evidence. Treat this as a condition to verify and monitor."
