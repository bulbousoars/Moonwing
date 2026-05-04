from moonwing.services.finding_enrichment import enrich_finding_display


def test_enriches_product_from_title_and_evidence_port():
    result = enrich_finding_display(
        title="MinIO API exposed on 9002/tcp",
        evidence_refs=[
            "9002/tcp open http Golang net/http server",
            "http-server-header: MinIO",
        ],
        target_display_name="secops localhost",
        target_metadata={"address": "127.0.0.1"},
        job_family="network_scan",
    )

    assert result["product_affected"] == "MinIO API"
    assert result["host_affected"] == "127.0.0.1"
    assert result["scan_type"] == "Network Scan"


def test_enriches_host_from_evidence_for_cidr_scan():
    result = enrich_finding_display(
        title="Portainer Agent API Reachable on Multiple Hosts",
        evidence_refs=[
            "192.168.1.111:9001 - Portainer-Agent 2.33.1 (SSL)",
            "192.168.1.169:9001 - Portainer-Agent 2.33.1 (SSL)",
        ],
        target_display_name="192.168.1.0/24",
        target_metadata={"address": "192.168.1.0/24"},
        job_family="network_scan",
    )

    assert result["product_affected"] == "Portainer Agent"
    assert result["host_affected"] == "192.168.1.111, 192.168.1.169"
    assert result["scan_type"] == "Network Scan"


def test_uses_specific_product_before_generic_open_ssh():
    result = enrich_finding_display(
        title="Open SSH Port",
        evidence_refs=["Service: OpenSSH 10.0p2 Debian 7 (protocol 2.0)."],
        target_display_name="secops localhost",
        target_metadata={"address": "127.0.0.1"},
        job_family="network_scan",
    )

    assert result["product_affected"] == "OpenSSH"
    assert result["host_affected"] == "127.0.0.1"
