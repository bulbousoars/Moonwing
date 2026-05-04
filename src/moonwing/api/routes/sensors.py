from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from moonwing.api.deps import _get_settings, get_db
from moonwing.services.sensors import (
    SensorAuthError,
    enroll_sensor,
    get_next_sensor_task,
    record_sensor_event,
    record_sensor_task_result,
    sensor_heartbeat,
)

router = APIRouter()


class SensorEnrollRequest(BaseModel):
    enrollment_token: str
    hostname: str
    platform: str
    os_name: str = ""
    sensor_version: str = ""
    labels: list[str] = Field(default_factory=list)


class SensorEnrollResponse(BaseModel):
    sensor_id: UUID
    token: str
    policy: dict


class SensorHeartbeatRequest(BaseModel):
    inventory: dict = Field(default_factory=dict)
    network: dict = Field(default_factory=dict)
    sensor_version: str = ""


class SensorHeartbeatResponse(BaseModel):
    status: str
    policy: dict
    queued_tasks: int


class SensorTaskResponse(BaseModel):
    id: UUID
    task_type: str
    payload: dict


class SensorTaskResultRequest(BaseModel):
    status: str
    result: dict = Field(default_factory=dict)


class SensorEventRequest(BaseModel):
    event_type: str
    severity: str = "info"
    payload: dict = Field(default_factory=dict)


def _bearer_token(authorization: str = Header("")) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sensor bearer token required")
    return authorization.split(" ", 1)[1].strip()


def _auth_error(exc: SensorAuthError) -> HTTPException:
    return HTTPException(status_code=401, detail=str(exc))


@router.post("/enroll")
def enroll(payload: SensorEnrollRequest, db: Session = Depends(get_db)) -> SensorEnrollResponse:
    settings = _get_settings()
    if not settings.sensor_enrollment_token:
        raise HTTPException(status_code=503, detail="Sensor enrollment is not configured")
    try:
        result = enroll_sensor(
            db,
            enrollment_token=payload.enrollment_token,
            expected_enrollment_token=settings.sensor_enrollment_token,
            hostname=payload.hostname,
            platform=payload.platform,
            os_name=payload.os_name,
            sensor_version=payload.sensor_version,
            labels=payload.labels,
        )
        heartbeat = sensor_heartbeat(db, sensor_id=result.sensor.id, token=result.token)
    except SensorAuthError as exc:
        raise _auth_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SensorEnrollResponse(sensor_id=result.sensor.id, token=result.token, policy=heartbeat.policy)


@router.post("/{sensor_id}/heartbeat")
def heartbeat(
    sensor_id: UUID,
    payload: SensorHeartbeatRequest,
    token: str = Depends(_bearer_token),
    db: Session = Depends(get_db),
) -> SensorHeartbeatResponse:
    try:
        result = sensor_heartbeat(
            db,
            sensor_id=sensor_id,
            token=token,
            inventory=payload.inventory,
            network=payload.network,
            sensor_version=payload.sensor_version,
        )
    except SensorAuthError as exc:
        raise _auth_error(exc) from exc
    return SensorHeartbeatResponse(status=result.sensor.status, policy=result.policy, queued_tasks=result.queued_tasks)


@router.get("/{sensor_id}/policy")
def policy(sensor_id: UUID, token: str = Depends(_bearer_token), db: Session = Depends(get_db)) -> dict:
    try:
        result = sensor_heartbeat(db, sensor_id=sensor_id, token=token)
    except SensorAuthError as exc:
        raise _auth_error(exc) from exc
    return result.policy


@router.get("/{sensor_id}/tasks/next")
def next_task(
    sensor_id: UUID,
    token: str = Depends(_bearer_token),
    db: Session = Depends(get_db),
) -> SensorTaskResponse | None:
    try:
        task = get_next_sensor_task(db, sensor_id=sensor_id, token=token)
    except SensorAuthError as exc:
        raise _auth_error(exc) from exc
    if task is None:
        return None
    return SensorTaskResponse(id=task.id, task_type=task.task_type, payload=task.payload)


@router.post("/{sensor_id}/tasks/{task_id}/result")
def task_result(
    sensor_id: UUID,
    task_id: UUID,
    payload: SensorTaskResultRequest,
    token: str = Depends(_bearer_token),
    db: Session = Depends(get_db),
) -> dict:
    try:
        task = record_sensor_task_result(
            db,
            sensor_id=sensor_id,
            token=token,
            task_id=task_id,
            status=payload.status,
            result=payload.result,
        )
    except SensorAuthError as exc:
        raise _auth_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": task.status}


@router.post("/{sensor_id}/events")
def event(
    sensor_id: UUID,
    payload: SensorEventRequest,
    token: str = Depends(_bearer_token),
    db: Session = Depends(get_db),
) -> dict:
    try:
        saved = record_sensor_event(
            db,
            sensor_id=sensor_id,
            token=token,
            event_type=payload.event_type,
            severity=payload.severity,
            payload=payload.payload,
        )
    except SensorAuthError as exc:
        raise _auth_error(exc) from exc
    return {"id": str(saved.id)}
