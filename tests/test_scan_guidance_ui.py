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
    assert 'class="credential-display"' in template
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


def test_system_host_terminal_template_wires_xterm_when_enabled():
    template = (ROOT / "src/moonwing/api/templates/system_host_terminal.html").read_text(encoding="utf-8")
    assert "/ws/system/terminal" in template
    assert "_cli_tools_probe.html" in template
    assert "FitAddon.FitAddon" in template
    assert "live:" in template
    assert "/system/host-terminal/web-shell" in template
