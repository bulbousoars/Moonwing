_STATE_TRANSITIONS = {
    "queued": ["staging", "failed", "canceled"],
    "staging": ["running", "failed", "canceled"],
    "running": ["normalizing", "failed", "needs_review", "canceled"],
    "normalizing": ["completed", "failed", "needs_review"],
    "completed": [],
    "failed": [],
    "canceled": [],
    "needs_review": [],
}


def create_run_snapshot(run) -> dict:
    return {
        "id": str(run.id),
        "job_family": run.job_family,
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "execution_mode": getattr(run, "execution_mode", "api"),
        "target_id": str(run.target_id) if run.target_id else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def transition_run_status(current: str, next_status: str) -> str:
    if current == next_status:
        return current
    allowed = _STATE_TRANSITIONS.get(current, [])
    if next_status not in allowed:
        raise ValueError(f"invalid run status transition: {current!r} -> {next_status!r}")
    return next_status
