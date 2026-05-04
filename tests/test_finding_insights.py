from moonwing.services.finding_insights import build_finding_insights


def test_build_finding_insights_derives_risk_docs_and_validation_steps():
    insights = build_finding_insights(
        title="MinIO Console exposed without authentication",
        severity="high",
        finding_details={
            "product_affected": "MinIO Console",
            "host_affected": "192.0.2.15",
            "affected_ports": "9003/tcp",
            "scan_type": "Network Scan",
            "description": "The MinIO administrative console is reachable.",
            "impact": "Unauthorized access could expose object storage.",
            "remediation": "Restrict access and require authentication.",
            "confidence": "high",
            "references": ["https://min.io/docs/"],
        },
        stored_details={"product": "MinIO Console", "cve": "CVE-2024-1234"},
        evidence_refs=["192.0.2.15 9003/tcp open http MinIO Console"],
        target_metadata={"address": "192.0.2.15", "network_exposure": "internal"},
    )

    assert "MinIO Console" in insights["executive_summary"]
    assert "high severity" in insights["risk_reasoning"]
    assert "Direct network access" in insights["exploitability"]
    assert "Restrict access and require authentication." in insights["remediation_plan"]
    assert "Re-run the scan" in insights["validation_steps"]
    assert "https://min.io/docs/" in insights["documentation_links"]
    assert "https://nvd.nist.gov/vuln/detail/CVE-2024-1234" in insights["vulnerability_references"]
    assert insights["affected_asset"]["Host"] == "192.0.2.15"
    assert insights["affected_asset"]["Ports"] == "9003/tcp"


def test_build_finding_insights_handles_sparse_legacy_findings():
    insights = build_finding_insights(
        title="OpenSSH service detected",
        severity="info",
        finding_details={
            "product_affected": "OpenSSH",
            "host_affected": "Unknown",
            "affected_ports": "Unknown",
            "scan_type": "Unknown",
            "description": "",
            "impact": "",
            "remediation": "",
            "confidence": "",
            "references": [],
        },
        stored_details={},
        evidence_refs=["22/tcp open ssh OpenSSH"],
        target_metadata={},
    )

    assert insights["technical_explanation"]
    assert insights["risk_reasoning"]
    assert insights["documentation_links"]
