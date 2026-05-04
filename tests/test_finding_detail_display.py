from moonwing.services.finding_detail_display import build_finding_details, format_evidence_refs


def test_build_finding_details_prefers_persisted_details_over_derived_values():
    details = build_finding_details(
        stored_details={
            "product": "MinIO Console",
            "affected_hosts": ["127.0.0.1"],
            "affected_ports": ["9003/tcp"],
            "description": "The MinIO administrative console is reachable.",
            "impact": "Unauthorized access could expose object storage.",
            "remediation": "Require authentication and restrict network access.",
            "confidence": "high",
            "references": ["https://min.io/docs/"],
        },
        enrichment={
            "product_affected": "MinIO API",
            "host_affected": "lab network",
            "scan_type": "Network Scan",
        },
    )

    assert details["product_affected"] == "MinIO Console"
    assert details["host_affected"] == "127.0.0.1"
    assert details["affected_ports"] == "9003/tcp"
    assert details["scan_type"] == "Network Scan"
    assert details["description"] == "The MinIO administrative console is reachable."
    assert details["impact"] == "Unauthorized access could expose object storage."
    assert details["remediation"] == "Require authentication and restrict network access."
    assert details["confidence"] == "high"
    assert details["references"] == ["https://min.io/docs/"]


def test_build_finding_details_falls_back_to_enrichment_for_legacy_findings():
    details = build_finding_details(
        stored_details={},
        enrichment={
            "product_affected": "OpenSSH",
            "host_affected": "192.0.2.15",
            "scan_type": "Network Scan",
        },
    )

    assert details["product_affected"] == "OpenSSH"
    assert details["host_affected"] == "192.0.2.15"
    assert details["affected_ports"] == "Unknown"
    assert details["description"] == ""
    assert details["references"] == []


def test_format_evidence_refs_extracts_host_port_service_and_raw_text():
    formatted = format_evidence_refs([
        "192.0.2.15:9003/tcp open http MinIO Console",
        "http-title: MinIO Console",
    ])

    assert formatted[0] == {
        "raw": "192.0.2.15:9003/tcp open http MinIO Console",
        "host": "192.0.2.15",
        "port": "9003/tcp",
        "service": "http MinIO Console",
    }
    assert formatted[1]["raw"] == "http-title: MinIO Console"
    assert formatted[1]["host"] == ""
