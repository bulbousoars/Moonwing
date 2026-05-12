from moonwing_sensor.agent import build_heartbeat_payload, normalize_server_url
from moonwing_sensor.collectors import (
    collect_host_inventory,
    packages_from_dpkg_status,
    packages_from_npm_ls_tree,
    packages_from_package_json,
)


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


def test_heartbeat_payload_includes_npm_packages_when_collector_enabled():
    payload = build_heartbeat_payload({"collectors": ["host", "npm_packages"]})

    assert "host" in payload["inventory"]
    assert "npm_packages" in payload["inventory"]
    assert isinstance(payload["inventory"]["npm_packages"], list)


def test_packages_from_npm_ls_tree_skips_missing_entries():
    tree = {
        "dependencies": {
            "npm": {"version": "10.0.0"},
            "ghost": {"missing": True},
            "bad": "not-a-dict",
        }
    }
    out = packages_from_npm_ls_tree(tree, origin_label="global")
    assert out == [{"name": "npm", "version": "10.0.0", "origin": "global"}]


def test_packages_from_package_json_merges_dependency_sections(tmp_path):
    pj = tmp_path / "package.json"
    pj.write_text('{"dependencies":{"a":"1.0.0"},"devDependencies":{"b":"^2.0.0"}}', encoding="utf-8")
    out = packages_from_package_json(pj)
    by_name = {row["name"]: row for row in out}
    assert by_name["a"]["version"] == "1.0.0"
    assert by_name["b"]["version"] == "^2.0.0"
    assert by_name["a"]["origin"].startswith("project:")
