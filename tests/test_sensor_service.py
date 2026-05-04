from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moonwing.db.base import Base
from moonwing.services.sensors import (
    SensorAuthError,
    create_sensor_task,
    enroll_sensor,
    get_next_sensor_task,
    record_sensor_event,
    record_sensor_task_result,
    sensor_heartbeat,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_sensor_enrollment_issues_token_and_stores_hash_only():
    session = _session()

    result = enroll_sensor(
        session,
        enrollment_token="enroll-secret",
        expected_enrollment_token="enroll-secret",
        hostname="docker-infra",
        platform="linux",
        os_name="Ubuntu 24.04",
        sensor_version="0.1.0",
        labels=["docker", "linux"],
    )

    assert result.sensor.hostname == "docker-infra"
    assert result.token.startswith("mwng_sens_")
    assert result.token not in result.sensor.token_hash
    assert result.sensor.status == "active"


def test_enrollment_rejects_invalid_enrollment_token():
    session = _session()

    try:
        enroll_sensor(
            session,
            enrollment_token="wrong",
            expected_enrollment_token="enroll-secret",
            hostname="host",
            platform="linux",
            os_name="Ubuntu",
            sensor_version="0.1.0",
        )
    except SensorAuthError as exc:
        assert "Invalid enrollment token" in str(exc)
    else:
        raise AssertionError("expected invalid enrollment token to be rejected")


def test_heartbeat_updates_inventory_and_returns_platform_policy():
    session = _session()
    enrolled = enroll_sensor(
        session,
        enrollment_token="enroll-secret",
        expected_enrollment_token="enroll-secret",
        hostname="danspc",
        platform="windows",
        os_name="Windows 11",
        sensor_version="0.1.0",
    )

    response = sensor_heartbeat(
        session,
        sensor_id=enrolled.sensor.id,
        token=enrolled.token,
        inventory={"installed_apps": ["PowerShell"]},
        network={"ips": ["192.168.1.7"]},
        sensor_version="0.1.1",
    )

    assert response.sensor.sensor_version == "0.1.1"
    assert response.sensor.inventory["installed_apps"] == ["PowerShell"]
    assert response.policy["platform"] == "windows"
    assert "windows_event_logs" in response.policy["collectors"]


def test_sensor_task_lifecycle_requires_sensor_token():
    session = _session()
    enrolled = enroll_sensor(
        session,
        enrollment_token="enroll-secret",
        expected_enrollment_token="enroll-secret",
        hostname="secops",
        platform="linux",
        os_name="Ubuntu",
        sensor_version="0.1.0",
    )
    task = create_sensor_task(session, sensor_id=enrolled.sensor.id, task_type="inventory.refresh", payload={"scope": "quick"})

    leased = get_next_sensor_task(session, sensor_id=enrolled.sensor.id, token=enrolled.token)
    assert leased is not None
    assert leased.id == task.id
    assert leased.status == "running"

    completed = record_sensor_task_result(
        session,
        sensor_id=enrolled.sensor.id,
        token=enrolled.token,
        task_id=task.id,
        status="completed",
        result={"packages": 42},
    )
    assert completed.status == "completed"
    assert completed.result["packages"] == 42

    try:
        get_next_sensor_task(session, sensor_id=enrolled.sensor.id, token="bad-token")
    except SensorAuthError:
        pass
    else:
        raise AssertionError("expected bad sensor token to be rejected")


def test_sensor_events_are_stored_with_platform_sensor_identity():
    session = _session()
    enrolled = enroll_sensor(
        session,
        enrollment_token="enroll-secret",
        expected_enrollment_token="enroll-secret",
        hostname="macbook",
        platform="macos",
        os_name="macOS 15",
        sensor_version="0.1.0",
    )

    event = record_sensor_event(
        session,
        sensor_id=enrolled.sensor.id,
        token=enrolled.token,
        event_type="fim.changed",
        severity="medium",
        payload={"path": "/etc/hosts"},
    )

    assert event.sensor_id == enrolled.sensor.id
    assert event.payload["path"] == "/etc/hosts"
