from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_directory_page_uses_management_surface_components():
    template = (ROOT / "src/moonwing/api/templates/management_directory.html").read_text(encoding="utf-8")

    assert 'class="ops-page-shell"' in template
    assert 'class="ops-status-strip"' in template
    assert "ops-panel-primary" in template
    assert "ops-action-bar" in template
    assert 'class="ops-section-title"' in template


def test_notifications_page_uses_management_surface_components():
    template = (ROOT / "src/moonwing/api/templates/management_notifications.html").read_text(encoding="utf-8")

    assert 'class="ops-page-shell"' in template
    assert 'class="ops-status-strip"' in template
    assert "ops-panel-primary" in template
    assert "notif-field-pair" in template
    assert 'class="notif-help-text"' in template
    assert 'class="notif-preference-recipient-label"' in template
    assert 'class="notif-preference-list"' in template
    assert 'class="notif-preference-row"' in template


def test_privileged_access_page_is_routed_and_linked():
    route = (ROOT / "src/moonwing/api/routes/dashboard.py").read_text(encoding="utf-8")
    management = (ROOT / "src/moonwing/api/templates/management_iam.html").read_text(encoding="utf-8")
    template_path = ROOT / "src/moonwing/api/templates/management_privileged_access.html"

    assert "@router.get('/management/privileged-access'" in route
    assert "management_privileged_access.html" in route
    assert 'href="/management/privileged-access"' in management
    assert template_path.exists()


def test_management_surface_css_exists():
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    assert ".ops-page-shell" in css
    assert ".ops-status-strip" in css
    assert ".ops-panel" in css
    assert ".ops-action-bar" in css
    assert ".notif-field-pair" in css
    assert ".notif-help-text" in css
    assert ".notif-preference-recipient-label" in css
    assert ".notif-preference-list" in css
    assert ".privileged-flow" in css
