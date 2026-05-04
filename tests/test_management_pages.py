from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_management_directory_routes_render_full_directory_page():
    route = (ROOT / "src/moonwing/api/routes/dashboard.py").read_text(encoding="utf-8")

    assert "@router.get('/management/directory'" in route
    assert "management_directory.html" in route
    assert "@router.post('/management/directory/config')" in route
    assert "@router.post('/management/directory/test')" in route
    assert "@router.post('/management/directory/preview')" in route
    assert "@router.post('/management/directory/sync')" in route


def test_management_notifications_routes_are_present():
    route = (ROOT / "src/moonwing/api/routes/dashboard.py").read_text(encoding="utf-8")

    assert "@router.get('/management/notifications'" in route
    assert "management_notifications.html" in route
    assert "@router.post('/management/notifications/smtp')" in route
    assert "@router.post('/management/notifications/smtp/test')" in route
    assert "@router.post('/management/notifications/preferences')" in route


def test_management_templates_exist_for_nav_links():
    templates = ROOT / "src/moonwing/api/templates"

    assert (templates / "management_directory.html").exists()
    assert (templates / "management_notifications.html").exists()
