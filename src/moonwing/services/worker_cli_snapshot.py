from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from moonwing.db.models.worker_diagnostic import WorkerDiagnostic

CLI_AGENTS_COMPONENT = "cli_agents"


def upsert_cli_agent_snapshot(session: Session, snapshot: dict) -> None:
    """Persist the latest worker-side CLI / smoke snapshot for the updates dashboard."""
    row = session.get(WorkerDiagnostic, CLI_AGENTS_COMPONENT)
    snap = dict(snapshot)
    snap["persisted_at"] = datetime.now(timezone.utc).isoformat()
    if row is None:
        session.add(WorkerDiagnostic(component=CLI_AGENTS_COMPONENT, payload=snap))
    else:
        row.payload = snap


def load_cli_agent_snapshot(session: Session) -> dict | None:
    row = session.get(WorkerDiagnostic, CLI_AGENTS_COMPONENT)
    if row is None:
        return None
    return dict(row.payload or {})
