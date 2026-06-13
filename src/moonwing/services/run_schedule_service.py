"""Cron-based run schedules: validation and worker materialization."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from croniter import croniter
from sqlalchemy import select
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from moonwing.db.models import Run, RunSchedule
from moonwing.worker.clearwing_runner import DEFAULT_AI_INSTRUCTION_MAX_CHARS

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def validate_cron_expression(expr: str) -> str:
    expr = (expr or "").strip()
    if not expr or len(expr) > 128:
        raise ValueError("cron expression must be 1–128 non-empty characters")
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron must have exactly 5 fields: minute hour day month weekday")
    try:
        croniter(expr, datetime.now(timezone.utc))
    except Exception as exc:
        raise ValueError(f"invalid cron expression: {exc}") from exc
    return expr


def validate_timezone(tz_name: str) -> str:
    name = (tz_name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc
    return name


def compute_next_run_utc(
    *,
    cron_expression: str,
    timezone_name: str,
    anchor_utc: datetime | None = None,
) -> datetime:
    """Return the first cron occurrence strictly after ``anchor_utc`` (default: now), in UTC."""
    anchor_utc = anchor_utc or datetime.now(timezone.utc)
    if anchor_utc.tzinfo is None:
        anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(timezone_name)
    local_anchor = anchor_utc.astimezone(tz)
    itr = croniter(cron_expression.strip(), local_anchor)
    nxt = itr.get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=tz)
    return nxt.astimezone(timezone.utc)


def materialize_due_schedules(session: "Session", *, now: datetime | None = None) -> int:
    """Create queued runs for enabled schedules with ``next_run_at`` in the past.

    Advances each schedule's ``next_run_at`` to the following cron tick. At most
    **one** queued run is created per schedule per call to avoid queue floods when
    the worker was offline.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    stmt = (
        select(RunSchedule)
        .where(RunSchedule.enabled.is_(True))
        .where(RunSchedule.next_run_at <= now)
        .order_by(RunSchedule.next_run_at)
        .limit(50)
    )
    schedules = list(session.scalars(stmt).all())
    created = 0
    for s in schedules:
        exec_snap: dict = {}
        if s.ai_instruction and str(s.ai_instruction).strip():
            exec_snap["ai_instruction"] = str(s.ai_instruction).strip()[:DEFAULT_AI_INSTRUCTION_MAX_CHARS]

        run = Run(
            job_family=s.job_family,
            status="queued",
            user_id=s.user_id,
            credential_id=s.credential_id,
            runtime_profile_id=s.runtime_profile_id,
            target_id=s.target_id,
            provider=s.provider,
            model=s.model,
            execution_mode=s.execution_mode or "api",
            execution_snapshot=exec_snap,
        )
        session.add(run)

        fired_at = s.next_run_at
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=timezone.utc)

        s.last_materialized_at = now
        s.next_run_at = compute_next_run_utc(
            cron_expression=s.cron_expression,
            timezone_name=s.timezone,
            anchor_utc=fired_at,
        )
        created += 1

    if created:
        logger.info("materialized %d scheduled run(s)", created)
    return created
