from moonwing.services.normalization import normalize_findings


def test_normalize_findings_preserves_rich_detail_fields():
    [finding] = normalize_findings({
        "findings": [
            {
                "title": "MinIO Console exposed",
                "severity": "high",
                "product": "MinIO Console",
                "affected_hosts": ["127.0.0.1"],
                "affected_ports": ["9003/tcp"],
                "description": "The MinIO administrative console is reachable.",
                "impact": "Unauthorized access could expose object storage.",
                "remediation": "Require authentication and restrict network access.",
                "confidence": "high",
                "references": ["https://min.io/docs/"],
                "evidence": ["9003/tcp open http", "http-title: MinIO Console"],
            }
        ]
    })

    assert finding["title"] == "MinIO Console exposed"
    assert finding["evidence_refs"] == ["9003/tcp open http", "http-title: MinIO Console"]
    assert finding["details"] == {
        "product": "MinIO Console",
        "affected_hosts": ["127.0.0.1"],
        "affected_ports": ["9003/tcp"],
        "description": "The MinIO administrative console is reachable.",
        "impact": "Unauthorized access could expose object storage.",
        "remediation": "Require authentication and restrict network access.",
        "confidence": "high",
        "references": ["https://min.io/docs/"],
    }


def test_normalize_findings_accepts_legacy_minimal_findings():
    [finding] = normalize_findings({
        "findings": [
            {
                "title": "Open SSH Port",
                "severity": "info",
                "evidence": ["22/tcp open ssh OpenSSH"],
            }
        ]
    })

    assert finding["details"] == {}
