"""Run state transitions and execution snapshots.

The platform foundation design treats run state as a small, explicit state
machine and persists a resolved execution snapshot at submit time so that
later changes to defaults or policy do not retroactively rewrite history.
"""

from __future__ import annotations

import copy
from typing import Any


_STATE_TRANSITIONS: dict[str, list[str]] = {
    "queued": ["staging", "failed", "canceled"],
    "staging": ["running", "failed", "canceled"],
    "running": ["normalizing", "failed", "needs_review", "canceled"],
    "normalizing": ["completed", "failed", "needs_review"],
    "completed": [],
    "failed": [],
    "canceled": [],
    "needs_review": [],
}


class RunStateError(ValueError):
    """Raised when a run is asked to take an invalid status transition."""


def transition_run_status(current: str, next_status: str) -> str:
    """Validate a run-status transition and return ``next_status``.

    Raises ``RunStateError`` (a ``ValueError`` subclass for backward
    compatibility) when the transition is not in the allowed set.
    """
    if current == next_status:
        return current
    allowed = _STATE_TRANSITIONS.get(current, [])
    if next_status not in allowed:
        raise RunStateError(
            f"invalid run status transition: {current!r} -> {next_status!r}"
        )
    return next_status


def create_run_snapshot(
    run: Any = None,
    /,
    *,
    provider: str | None = None,
    model: str | None = None,
    credential_id: str | None = None,
    runtime_profile_name: str | None = None,
    policy_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the resolved execution snapshot persisted on a run.

    Two call shapes are supported:

    - ``create_run_snapshot(run)`` — extract a serialization-friendly dict
      from a Run-like object (production form, used by ``api/routes/runs``
      and ``services/run_activity``).
    - ``create_run_snapshot(provider=..., model=..., credential_id=...,
      runtime_profile_name=..., policy_flags=...)`` — build a snapshot
      from the resolved selection at submit time (foundation form). The
      snapshot is deep-copied so later mutation of nested policy flags by
      the caller never bleeds into stored state.
    """
    if run is not None:
        return {
            "id": str(run.id),
            "job_family": run.job_family,
            "status": run.status,
            "provider": run.provider,
            "model": run.model,
            "execution_mode": getattr(run, "execution_mode", "api"),
            "target_id": str(run.target_id) if getattr(run, "target_id", None) else None,
            "created_at": run.created_at.isoformat() if getattr(run, "created_at", None) else None,
        }

    return {
        "provider": provider,
        "model": model,
        "credential_id": credential_id,
        "runtime_profile_name": runtime_profile_name,
        "policy_flags": copy.deepcopy(policy_flags) if policy_flags is not None else {},
    }
