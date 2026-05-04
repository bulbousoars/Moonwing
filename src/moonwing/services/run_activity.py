from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


ACTIVE_RUN_STATUSES = {"queued", "staging", "running", "normalizing"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "canceled", "needs_review"}
SENSITIVE_FAILURE_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|token|secret|password)(\s*[:=]\s*)([^\s,;]+)"),
)


def _timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_run_activity(
    run: Any,
    *,
    stage: str,
    message: str,
    level: str = "info",
    now: datetime | None = None,
    limit: int = 200,
) -> None:
    snapshot = dict(run.execution_snapshot or {})
    activity = list(snapshot.get("activity") or [])
    activity.append(
        {
            "timestamp": _timestamp(now),
            "stage": stage,
            "message": message,
            "level": level,
        }
    )
    snapshot["activity"] = activity[-limit:]
    run.execution_snapshot = snapshot


def sanitize_failure_message(message: str, *, limit: int = 500) -> str:
    text = " ".join(str(message).split())
    for pattern in SENSITIVE_FAILURE_PATTERNS:
        text = pattern.sub(r"\1\2[redacted]", text)
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text or "Unknown worker exception"


def record_run_failure(run: Any, exc: BaseException, *, now: datetime | None = None) -> None:
    message = sanitize_failure_message(str(exc))
    snapshot = dict(run.execution_snapshot or {})
    snapshot["failure"] = {
        "type": exc.__class__.__name__,
        "message": message,
        "timestamp": _timestamp(now),
    }
    run.execution_snapshot = snapshot
    append_run_activity(
        run,
        stage="failed",
        message=f"Run failed: {message}",
        level="error",
        now=now,
    )


def serialize_run_activity(run: Any) -> dict[str, Any]:
    snapshot = run.execution_snapshot or {}
    status = run.status
    return {
        "run_id": str(run.id),
        "status": status,
        "active": status in ACTIVE_RUN_STATUSES,
        "terminal": status in TERMINAL_RUN_STATUSES,
        "activity": list(snapshot.get("activity") or []),
        "failure": snapshot.get("failure"),
    }
