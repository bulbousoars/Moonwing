from types import SimpleNamespace
from uuid import uuid4

from moonwing.db.models import Run
from moonwing.worker.staging import StagedJob
from moonwing.worker.tasks import InMemoryObjectStore, process_run


class FakeSession:
    def __init__(self, run):
        self.run = run
        self.commits = 0
        self.rollbacks = 0
        self.added = []

    def get(self, model, item_id):
        if model is Run and item_id == self.run.id:
            return self.run
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def add(self, item):
        self.added.append(item)


def test_process_run_enters_staging_before_running_for_raw_payload(monkeypatch):
    run = SimpleNamespace(
        id=uuid4(),
        status="queued",
        execution_snapshot={},
        target_id=uuid4(),
    )
    session = FakeSession(run)
    staged_statuses = []

    def fake_stage_run(**kwargs):
        staged_statuses.append(run.status)
        return StagedJob(
            command=["scanner"],
            artifacts=[],
            execution_snapshot=dict(run.execution_snapshot, staged=True),
            target_metadata={"address": "dugganco.com"},
            target_display_name="dugganco.com",
            credential_provider="google",
            encrypted_api_key=None,
        )

    monkeypatch.setattr("moonwing.worker.tasks.stage_run", fake_stage_run)

    process_run(
        session=session,
        run_id=run.id,
        raw_payload={"findings": []},
        object_store=InMemoryObjectStore(),
    )

    stages = [event["stage"] for event in run.execution_snapshot["activity"]]
    assert staged_statuses == ["staging"]
    assert stages[:3] == ["staging", "staging", "running"]
    assert run.status == "completed"
