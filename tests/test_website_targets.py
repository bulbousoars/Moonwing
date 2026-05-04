from unittest.mock import Mock, patch

from moonwing.services.targets import normalize_target_metadata
from moonwing.worker.clearwing_runner import run_nmap


def test_website_target_normalizes_https_url_for_network_scan():
    metadata = normalize_target_metadata(
        target_type="website",
        display_name="Moonwing",
        source_metadata={"url": "https://moonwing.example.com/management/notifications"},
    )

    assert metadata["url"] == "https://moonwing.example.com/management/notifications"
    assert metadata["address"] == "moonwing.example.com"
    assert metadata["scheme"] == "https"
    assert metadata["path"] == "/management/notifications"
    assert metadata["scan_ports"] == "443"
    assert metadata["input_kind"] == "url"
    assert metadata["website"] is True


def test_website_target_uses_explicit_url_port():
    metadata = normalize_target_metadata(
        target_type="website",
        display_name="Lab App",
        source_metadata={"url": "http://192.0.2.15:8000/health"},
    )

    assert metadata["address"] == "192.0.2.15"
    assert metadata["scan_ports"] == "8000"


def test_website_target_accepts_display_name_url_when_metadata_is_empty():
    metadata = normalize_target_metadata(
        target_type="website",
        display_name="https://example.com",
        source_metadata={},
    )

    assert metadata["url"] == "https://example.com"
    assert metadata["address"] == "example.com"
    assert metadata["scan_ports"] == "443"


def test_website_target_rejects_non_http_urls():
    try:
        normalize_target_metadata(
            target_type="website",
            display_name="FTP",
            source_metadata={"url": "ftp://example.com"},
        )
    except ValueError as exc:
        assert "Website targets must use http or https" in str(exc)
    else:
        raise AssertionError("expected ftp URL to be rejected")


def test_run_nmap_uses_explicit_ports_when_provided():
    proc = Mock(returncode=0, stdout="nmap output")
    with patch("subprocess.run", return_value=proc) as run:
        assert run_nmap("example.com", ports="443") == "nmap output"

    command = run.call_args.args[0]
    assert command[:4] == ["nmap", "-sV", "-sC", "-p"]
    assert command[4] == "443"
    assert command[-1] == "example.com"


def test_target_form_exposes_website_target_type():
    template = open("src/moonwing/api/templates/target_form.html", encoding="utf-8").read()

    assert 'value="website"' in template
    assert "Website / URL" in template


def test_dashboard_routes_normalize_target_metadata_on_save():
    route = open("src/moonwing/api/routes/dashboard.py", encoding="utf-8").read()

    assert "normalize_target_metadata" in route
    assert "parsed_metadata = normalize_target_metadata" in route


def test_worker_passes_website_scan_ports_to_nmap():
    worker = open("src/moonwing/worker/tasks.py", encoding="utf-8").read()

    assert "scan_ports" in worker
    assert "run_nmap(source_ref, ports=scan_ports)" in worker
