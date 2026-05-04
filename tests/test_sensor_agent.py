from moonwing_sensor.agent import build_heartbeat_payload, normalize_server_url
from moonwing_sensor.collectors import collect_host_inventory, packages_from_dpkg_status


def test_normalize_server_url_removes_trailing_slash():
    assert normalize_server_url("https://moonwing.example.com/") == "https://moonwing.example.com"


def test_host_inventory_reports_platform_and_hostname():
    inventory = collect_host_inventory()

    assert inventory["hostname"]
    assert inventory["platform"] in {"linux", "windows", "macos"}
    assert "os_name" in inventory


def test_dpkg_status_parser_extracts_package_names_and_versions():
    status = """Package: bash
Status: install ok installed
Version: 5.2.21-2

Package: curl
Status: install ok installed
Version: 8.5.0-2
"""

    assert packages_from_dpkg_status(status) == [
        {"name": "bash", "version": "5.2.21-2"},
        {"name": "curl", "version": "8.5.0-2"},
    ]


def test_heartbeat_payload_respects_policy_collectors():
    payload = build_heartbeat_payload({"collectors": ["host", "packages"]})

    assert "host" in payload["inventory"]
    assert "packages" in payload["inventory"]
    assert "network" in payload
