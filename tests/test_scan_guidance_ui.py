from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_launch_form_explains_network_scan_and_source_hunt():
    template = (ROOT / "src/moonwing/api/templates/run_form.html").read_text(encoding="utf-8")

    assert "Network Scan" in template
    assert "What is exposed and reachable" in template
    assert "Source Hunt" in template
    assert "What is wrong in the code" in template
    assert 'value="network_scan"' in template
    assert 'value="source_hunt"' in template
    # Merged run form (prod model-registry version) selects credentials via a
    # dropdown with provider/model discovery rather than main's read-only
    # credential-display status element.
    assert 'id="credential_id"' in template
    assert "_cli_tools_probe.html" not in template


def test_schedule_form_excludes_cli_tools_probe():
    template = (ROOT / "src/moonwing/api/templates/schedule_form.html").read_text(encoding="utf-8")
    assert "_cli_tools_probe.html" not in template


def test_cli_tools_probe_offers_copy_install_and_docs():
    template = (ROOT / "src/moonwing/api/templates/_cli_tools_probe.html").read_text(encoding="utf-8")

    assert "data-copy-cmd" in template
    assert "cli-copy-btn" in template
    assert "Install help" in template
    assert "Vendor docs" in template


def test_target_form_hints_which_job_family_to_use():
    template = (ROOT / "src/moonwing/api/templates/target_form.html").read_text(encoding="utf-8")

    assert "Websites and network hosts usually use Network Scan." in template
    assert "Repositories, binaries, and SBOMs usually use Source Hunt." in template


def test_system_cli_tools_page_scans_docker_host():
    template = (ROOT / "src/moonwing/api/templates/system_cli_tools.html").read_text(encoding="utf-8")
    assert "Docker host" in template
    assert "cursor" in template.lower()
    assert "kimi" in template.lower()
    assert "/ws/system/terminal" not in template
    assert "_cli_tools_probe.html" not in template
    assert "host-ai-scan-btn" in template
    assert "host-ai-tools/scan" in template
    assert "formatLocalDateTime" in template
    assert "host-scan-scanned-at" in template
    assert "host-ai-scan-table-wrap" in template
