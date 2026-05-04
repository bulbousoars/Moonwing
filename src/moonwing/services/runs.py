from copy import deepcopy

from moonwing.schemas.runs import RunSnapshot

_ALLOWED_TRANSITIONS = {
    'queued': {'staging', 'failed', 'canceled'},
    'staging': {'running', 'failed', 'canceled'},
    'running': {'normalizing', 'failed', 'needs_review', 'canceled'},
    'normalizing': {'completed', 'failed', 'needs_review'},
    'completed': set(),
    'failed': set(),
    'canceled': set(),
    'needs_review': set(),
}


class RunStateError(ValueError):
    pass


def create_run_snapshot(*, provider: str, model: str, credential_id: str, runtime_profile_name: str, policy_flags: dict) -> dict:
    snapshot = RunSnapshot(
        provider=provider,
        model=model,
        credential_id=credential_id,
        runtime_profile_name=runtime_profile_name,
        policy_flags=deepcopy(policy_flags),
    )
    return snapshot.model_dump()


def transition_run_status(current_status: str, next_status: str) -> str:
    allowed_next = _ALLOWED_TRANSITIONS.get(current_status)
    if allowed_next is None:
        raise RunStateError(f'unknown current status: {current_status!r}')
    if next_status not in allowed_next:
        raise RunStateError(f'invalid transition from {current_status!r} to {next_status!r}')
    return next_status
