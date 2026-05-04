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


def test_target_form_hints_which_job_family_to_use():
    template = (ROOT / "src/moonwing/api/templates/target_form.html").read_text(encoding="utf-8")

    assert "Websites and network hosts usually use Network Scan." in template
    assert "Repositories, binaries, and SBOMs usually use Source Hunt." in template
