from __future__ import annotations

import re
from collections.abc import Iterable

PRODUCT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("minio console", "MinIO Console"),
    ("minio api", "MinIO API"),
    ("minio", "MinIO API"),
    ("portainer-agent", "Portainer Agent"),
    ("portainer agent", "Portainer Agent"),
    ("elasticsearch rest api", "Elasticsearch REST API"),
    ("elasticsearch", "Elasticsearch"),
    ("moonwing", "Moonwing Dashboard"),
    ("uvicorn", "Moonwing Dashboard"),
    ("openssh", "OpenSSH"),
    ("open ssh", "OpenSSH"),
    ("qbittorrent", "qBittorrent"),
    ("sabnzbd", "SABnzbd"),
    ("whisparr", "Whisparr"),
    ("nextcloud", "Nextcloud"),
    ("unifi", "UniFi Network"),
    ("ubiquiti", "Ubiquiti Management"),
    ("authentik", "authentik"),
    ("hashicorp vault", "HashiCorp Vault"),
    ("vault", "HashiCorp Vault"),
    ("n8n", "n8n"),
    ("android debug bridge", "Android Debug Bridge"),
    ("adb", "Android Debug Bridge"),
    ("pptp", "PPTP VPN"),
    ("werkzeug", "Werkzeug Dev Server"),
    ("next.js", "Next.js Dev Server"),
    ("node.js express", "Node.js Express API"),
    ("express", "Node.js Express API"),
    ("smb", "SMB"),
    ("proxmox", "Proxmox VE API"),
    ("traefik", "Traefik"),
)


def enrich_finding_display(
    *,
    title: str,
    evidence_refs: Iterable[object],
    target_display_name: str | None,
    target_metadata: dict | None,
    job_family: str,
) -> dict[str, str]:
    evidence = [str(ref) for ref in evidence_refs or []]
    blob = " ".join([title, *evidence]).lower()
    target_metadata = target_metadata or {}

    return {
        "product_affected": _derive_product(title=title, evidence=evidence, blob=blob),
        "host_affected": _derive_hosts(evidence=evidence, target_display_name=target_display_name, target_metadata=target_metadata),
        "scan_type": _format_scan_type(job_family),
    }


def _derive_product(*, title: str, evidence: list[str], blob: str) -> str:
    for needle, product in PRODUCT_PATTERNS:
        if needle in blob:
            return product

    service_match = re.search(r"service:\s*([^().,]+)", " ".join(evidence), flags=re.IGNORECASE)
    if service_match:
        return service_match.group(1).strip()

    port_title_match = re.match(r"(.+?)\s+(?:exposed|accessible|detected|reachable|service|port)\b", title, flags=re.IGNORECASE)
    if port_title_match:
        return port_title_match.group(1).strip()

    return title.strip() or "Unknown"


def _derive_hosts(*, evidence: list[str], target_display_name: str | None, target_metadata: dict) -> str:
    host_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    hosts: list[str] = []
    for ref in evidence:
        for host in host_pattern.findall(ref):
            if host not in hosts:
                hosts.append(host)

    if hosts:
        return ", ".join(hosts)

    address = str(target_metadata.get("address") or "").strip()
    if address and "/" not in address:
        return address

    if target_display_name:
        return target_display_name

    return "Unknown"


def _format_scan_type(job_family: str) -> str:
    labels = {
        "network_scan": "Network Scan",
        "source_hunt": "Source Hunt",
    }
    return labels.get(job_family, job_family.replace("_", " ").title() if job_family else "Unknown")
