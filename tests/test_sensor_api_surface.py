from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sensor_router_is_mounted_and_bypasses_dashboard_auth():
    main = (ROOT / "src/moonwing/api/main.py").read_text(encoding="utf-8")

    assert "sensor_router" in main
    assert "app.include_router(sensor_router" in main
    assert 'path.startswith("/api/sensors")' in main


def test_sensor_api_routes_cover_mvp_contract():
    route = (ROOT / "src/moonwing/api/routes/sensors.py").read_text(encoding="utf-8")

    assert '@router.post("/enroll")' in route
    assert '@router.post("/{sensor_id}/heartbeat")' in route
    assert '@router.get("/{sensor_id}/policy")' in route
    assert '@router.get("/{sensor_id}/tasks/next")' in route
    assert '@router.post("/{sensor_id}/tasks/{task_id}/result")' in route
    assert '@router.post("/{sensor_id}/events")' in route


def test_sensor_management_page_is_routed_and_linked():
    route = (ROOT / "src/moonwing/api/routes/dashboard.py").read_text(encoding="utf-8")
    base = (ROOT / "src/moonwing/api/templates/base.html").read_text(encoding="utf-8")
    template = ROOT / "src/moonwing/api/templates/sensors.html"

    assert "@router.get('/sensors'" in route
    assert "SensorEndpoint" in route
    assert "sensors.html" in route
    assert 'href="/sensors"' in base
    assert template.exists()


def test_sensor_enrollment_token_is_configured_from_settings():
    config = (ROOT / "src/moonwing/config.py").read_text(encoding="utf-8")

    assert "sensor_enrollment_token" in config


def test_sensor_models_are_in_alembic_metadata_and_migration_exists():
    env = (ROOT / "alembic/env.py").read_text(encoding="utf-8")
    migration = (ROOT / "alembic/versions/20260504_01_add_sensor_endpoints.py").read_text(encoding="utf-8")

    assert "sensor" in env
    assert "sensor_endpoints" in migration
    assert "sensor_tasks" in migration
    assert "sensor_events" in migration


def test_sensor_deployment_scaffolding_exists_for_linux_windows_and_macos_timeline():
    role = (ROOT / "deploy/ansible/roles/moonwing_sensor/tasks/main.yml").read_text(encoding="utf-8")
    unit = (ROOT / "deploy/ansible/roles/moonwing_sensor/templates/moonwing-sensor.service.j2").read_text(encoding="utf-8")
    windows = (ROOT / "deploy/windows/install-moonwing-sensor.ps1").read_text(encoding="utf-8")
    timeline = (ROOT / "docs/sensors/rollout.md").read_text(encoding="utf-8")

    assert "ansible.builtin.copy" in role
    assert "ansible.builtin.systemd_service" in role
    assert "ExecStart=" in unit
    assert "New-Service" in windows
    assert "Phase 5: macOS Sensor" in timeline
