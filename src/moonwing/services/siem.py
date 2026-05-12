"""Optional HTTP shipping of security-relevant events to a SIEM (e.g. Logstash HTTP input)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from moonwing.config import Settings

logger = logging.getLogger("moonwing.siem")


def _settings() -> Settings:
    return Settings()


def emit_siem_record(payload: dict[str, Any], *, settings: Settings | None = None) -> None:
    """POST a single JSON object to ``MOONWING_SIEM_HTTP_URL`` when SIEM is enabled."""
    settings = settings or _settings()
    if not settings.siem_enabled:
        return
    url = (settings.siem_http_url or "").strip()
    if not url:
        return

    envelope = {
        "@timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "service": "moonwing",
        **payload,
    }
    headers = {"Content-Type": "application/json"}
    raw = (settings.siem_http_headers_json or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    headers[str(k)] = str(v)
        except json.JSONDecodeError:
            logger.warning("MOONWING_SIEM_HTTP_HEADERS_JSON is not valid JSON; ignoring")

    try:
        resp = httpx.post(url, json=envelope, headers=headers, timeout=settings.siem_http_timeout_seconds)
        if resp.status_code >= 400:
            logger.warning("siem POST %s returned %s", url, resp.status_code)
    except Exception as exc:
        logger.warning("siem ship failed: %s", exc)


def emit_audit_event(
    *,
    action: str,
    resource_type: str,
    actor_user_id: UUID | None,
    resource_id: str | None,
    outcome: str,
    metadata: dict[str, Any] | None,
    settings: Settings | None = None,
) -> None:
    emit_siem_record(
        {
            "log_type": "moonwing_audit",
            "action": action,
            "resource_type": resource_type,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "resource_id": resource_id,
            "outcome": outcome,
            "metadata": metadata or {},
        },
        settings=settings,
    )


def emit_run_terminal(
    *,
    run_id: UUID,
    status: str,
    job_family: str,
    finding_count: int | None = None,
    settings: Settings | None = None,
) -> None:
    emit_siem_record(
        {
            "log_type": "moonwing_run",
            "run_id": str(run_id),
            "status": status,
            "job_family": job_family,
            "finding_count": finding_count,
        },
        settings=settings,
    )
