from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.db.models import SensorEndpoint, SensorEvent, SensorTask


class SensorAuthError(ValueError):
    pass


@dataclass(frozen=True)
class SensorEnrollmentResult:
    sensor: SensorEndpoint
    token: str


@dataclass(frozen=True)
class SensorHeartbeatResult:
    sensor: SensorEndpoint
    policy: dict
    queued_tasks: int


DEFAULT_POLICIES: dict[str, dict] = {
    "linux": {
        "platform": "linux",
        "collectors": ["host", "packages", "processes", "listening_ports", "auth_logs", "docker"],
        "inventory_interval_seconds": 3600,
        "heartbeat_interval_seconds": 60,
        "fim": [
            {"path": "/etc", "mode": "hash", "interval_seconds": 900},
            {"path": "/mnt/storage/docker", "mode": "metadata", "interval_seconds": 1800},
        ],
        "exclusions": ["/mnt/storage/media", "/mnt/storage/minio", "/mnt/storage/pgdata"],
    },
    "windows": {
        "platform": "windows",
        "collectors": ["host", "installed_apps", "services", "processes", "listening_ports", "windows_event_logs"],
        "inventory_interval_seconds": 3600,
        "heartbeat_interval_seconds": 60,
        "fim": [
            {"path": "C:\\Windows\\System32\\drivers\\etc", "mode": "hash", "interval_seconds": 900},
            {"path": "C:\\ProgramData\\Moonwing", "mode": "metadata", "interval_seconds": 1800},
        ],
        "windows_event_logs": ["Security", "System", "Application", "Microsoft-Windows-Windows Defender/Operational"],
    },
    "macos": {
        "platform": "macos",
        "collectors": ["host", "installed_apps", "homebrew", "launch_items", "processes", "listening_ports", "unified_logs"],
        "inventory_interval_seconds": 3600,
        "heartbeat_interval_seconds": 60,
        "fim": [
            {"path": "/etc", "mode": "hash", "interval_seconds": 900},
            {"path": "/Library/LaunchDaemons", "mode": "metadata", "interval_seconds": 900},
            {"path": "/Library/LaunchAgents", "mode": "metadata", "interval_seconds": 900},
        ],
    },
}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_token() -> str:
    return f"mwng_sens_{secrets.token_urlsafe(32)}"


def _normalize_platform(platform: str) -> str:
    value = platform.strip().lower()
    aliases = {"darwin": "macos", "mac": "macos", "win32": "windows", "win": "windows"}
    value = aliases.get(value, value)
    if value not in DEFAULT_POLICIES:
        raise ValueError(f"Unsupported sensor platform: {platform}")
    return value


def _policy_for(platform: str, custom_policy: dict | None = None) -> dict:
    policy = dict(DEFAULT_POLICIES[_normalize_platform(platform)])
    if custom_policy:
        policy.update(custom_policy)
    return policy


def _require_enrollment_token(actual: str, expected: str) -> None:
    if not expected or not secrets.compare_digest(actual, expected):
        raise SensorAuthError("Invalid enrollment token")


def _require_sensor(session: Session, sensor_id: UUID, token: str) -> SensorEndpoint:
    sensor = session.get(SensorEndpoint, sensor_id)
    if sensor is None or not secrets.compare_digest(sensor.token_hash, _hash_token(token)):
        raise SensorAuthError("Invalid sensor token")
    return sensor


def enroll_sensor(
    session: Session,
    *,
    enrollment_token: str,
    expected_enrollment_token: str,
    hostname: str,
    platform: str,
    os_name: str,
    sensor_version: str,
    labels: list[str] | None = None,
    policy: dict | None = None,
) -> SensorEnrollmentResult:
    _require_enrollment_token(enrollment_token, expected_enrollment_token)
    normalized_platform = _normalize_platform(platform)
    token = _issue_token()
    sensor = SensorEndpoint(
        hostname=hostname.strip(),
        platform=normalized_platform,
        os_name=os_name.strip(),
        sensor_version=sensor_version.strip(),
        status="active",
        token_hash=_hash_token(token),
        labels=labels or [],
        policy=policy or {},
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(sensor)
    session.commit()
    session.refresh(sensor)
    return SensorEnrollmentResult(sensor=sensor, token=token)


def sensor_heartbeat(
    session: Session,
    *,
    sensor_id: UUID,
    token: str,
    inventory: dict | None = None,
    network: dict | None = None,
    sensor_version: str | None = None,
) -> SensorHeartbeatResult:
    sensor = _require_sensor(session, sensor_id, token)
    sensor.status = "active"
    sensor.last_seen_at = datetime.now(timezone.utc)
    if inventory is not None:
        sensor.inventory = inventory
    if network is not None:
        sensor.network = network
    if sensor_version:
        sensor.sensor_version = sensor_version
    queued_tasks = session.query(SensorTask).filter_by(sensor_id=sensor.id, status="queued").count()
    session.commit()
    session.refresh(sensor)
    return SensorHeartbeatResult(sensor=sensor, policy=_policy_for(sensor.platform, sensor.policy), queued_tasks=queued_tasks)


def create_sensor_task(session: Session, *, sensor_id: UUID, task_type: str, payload: dict | None = None) -> SensorTask:
    task = SensorTask(sensor_id=sensor_id, task_type=task_type, payload=payload or {})
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def get_next_sensor_task(session: Session, *, sensor_id: UUID, token: str) -> SensorTask | None:
    _require_sensor(session, sensor_id, token)
    task = (
        session.query(SensorTask)
        .filter_by(sensor_id=sensor_id, status="queued")
        .order_by(SensorTask.created_at.asc())
        .first()
    )
    if task is None:
        return None
    task.status = "running"
    task.leased_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(task)
    return task


def record_sensor_task_result(
    session: Session,
    *,
    sensor_id: UUID,
    token: str,
    task_id: UUID,
    status: str,
    result: dict | None = None,
) -> SensorTask:
    _require_sensor(session, sensor_id, token)
    task = session.get(SensorTask, task_id)
    if task is None or task.sensor_id != sensor_id:
        raise ValueError("Sensor task not found")
    if status not in {"completed", "failed"}:
        raise ValueError("Task status must be completed or failed")
    task.status = status
    task.result = result or {}
    task.completed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(task)
    return task


def record_sensor_event(
    session: Session,
    *,
    sensor_id: UUID,
    token: str,
    event_type: str,
    severity: str,
    payload: dict | None = None,
    observed_at: datetime | None = None,
) -> SensorEvent:
    _require_sensor(session, sensor_id, token)
    event = SensorEvent(
        sensor_id=sensor_id,
        event_type=event_type,
        severity=severity,
        payload=payload or {},
        observed_at=observed_at,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event
