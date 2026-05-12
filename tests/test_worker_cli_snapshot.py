from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moonwing.db.models.worker_diagnostic import WorkerDiagnostic
from moonwing.services.worker_cli_snapshot import load_cli_agent_snapshot, upsert_cli_agent_snapshot


def test_cli_agent_snapshot_upsert_roundtrip():
    eng = create_engine("sqlite:///:memory:")
    WorkerDiagnostic.__table__.create(eng)
    factory = sessionmaker(bind=eng)
    s = factory()
    upsert_cli_agent_snapshot(s, {"tools": [], "ready_for_cli_scan_count": 1, "total_cli_slots": 3})
    s.commit()
    out = load_cli_agent_snapshot(s)
    assert out["ready_for_cli_scan_count"] == 1
    assert "persisted_at" in out

    upsert_cli_agent_snapshot(s, {"tools": [], "ready_for_cli_scan_count": 2, "total_cli_slots": 3})
    s.commit()
    assert load_cli_agent_snapshot(s)["ready_for_cli_scan_count"] == 2
    s.close()
