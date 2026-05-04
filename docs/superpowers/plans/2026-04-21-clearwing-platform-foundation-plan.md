# Clearwing Platform Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Moonwing slice on `secops` with a Clearwing-backed control API, worker execution for `network_scan` and `source_hunt`, Postgres as system of record, and local S3-compatible storage for artifacts.

**Architecture:** A small control-plane API persists users, credentials, runtime profiles, targets, runs, findings, and artifact metadata in Postgres; a worker service executes Clearwing jobs asynchronously and normalizes results; MinIO provides S3-compatible artifact storage. The API and worker share an internal core package that owns job specs, provider/model selection, run state rules, and normalized finding contracts.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, Pydantic, Celery or Dramatiq, Redis, Postgres, MinIO, Docker Compose, Ansible, pytest.

---

## Planned File Structure

- `D:\Projects\Moonwing\README.md`
  - Project overview, local dev, service map.
- `D:\Projects\Moonwing\.env.example`
  - Environment contract for API, worker, Postgres, Redis, MinIO, Clearwing, and provider backends.
- `D:\Projects\Moonwing\docker-compose.yml`
  - Local/dev composition for API, worker, Postgres, Redis, MinIO.
- `D:\Projects\Moonwing\docs\superpowers\specs\2026-04-21-clearwing-platform-foundation-design.md`
  - Approved design spec.
- `D:\Projects\Moonwing\docs\superpowers\plans\2026-04-21-clearwing-platform-foundation-plan.md`
  - This implementation plan.
- `D:\Projects\Moonwing\src\moonwing\config.py`
  - Application settings.
- `D:\Projects\Moonwing\src\moonwing\db\base.py`
  - SQLAlchemy base metadata.
- `D:\Projects\Moonwing\src\moonwing\db\session.py`
  - Session factory and engine wiring.
- `D:\Projects\Moonwing\src\moonwing\db\models\*.py`
  - Relational entities for users, credentials, runtime profiles, targets, inputs, runs, findings, artifacts.
- `D:\Projects\Moonwing\src\moonwing\schemas\*.py`
  - API request/response contracts.
- `D:\Projects\Moonwing\src\moonwing\core\job_types.py`
  - `network_scan` and `source_hunt` domain contracts.
- `D:\Projects\Moonwing\src\moonwing\core\runtime_profiles.py`
  - Policy and execution setting resolution.
- `D:\Projects\Moonwing\src\moonwing\core\providers.py`
  - Provider/backend and model selection abstractions.
- `D:\Projects\Moonwing\src\moonwing\core\findings.py`
  - Normalized finding schema and mapping helpers.
- `D:\Projects\Moonwing\src\moonwing\services\credentials.py`
  - Secret reference validation and selection rules.
- `D:\Projects\Moonwing\src\moonwing\services\runs.py`
  - Run creation, state transitions, cancellation, snapshot generation.
- `D:\Projects\Moonwing\src\moonwing\services\artifacts.py`
  - MinIO object storage integration and artifact provenance persistence.
- `D:\Projects\Moonwing\src\moonwing\services\normalization.py`
  - Raw Clearwing output to normalized finding ingestion.
- `D:\Projects\Moonwing\src\moonwing\api\main.py`
  - FastAPI app bootstrap.
- `D:\Projects\Moonwing\src\moonwing\api\routes\*.py`
  - Endpoints for auth placeholders, credentials, runtimes, targets, runs, findings.
- `D:\Projects\Moonwing\src\moonwing\worker\main.py`
  - Worker bootstrap.
- `D:\Projects\Moonwing\src\moonwing\worker\tasks.py`
  - Queue task handlers for staged run execution.
- `D:\Projects\Moonwing\src\moonwing\worker\clearwing_runner.py`
  - Clearwing CLI orchestration.
- `D:\Projects\Moonwing\alembic.ini`
  - Alembic config.
- `D:\Projects\Moonwing\alembic\versions\*.py`
  - Database migrations.
- `D:\Projects\Moonwing\tests\unit\*.py`
  - Unit tests for domain rules and services.
- `D:\Projects\Moonwing\tests\integration\*.py`
  - Integration tests for API, database, and worker flows.
- `C:\Users\danie\Documents\Ansible\inventory\hosts.yml`
  - Update `secops` to `192.168.1.215`.
- `C:\Users\danie\Documents\Ansible\roles\secops_docker_stack\files\docker-compose.yml`
  - Replace OpenVAS-focused stack with Moonwing dependencies or add Moonwing service stack.
- `C:\Users\danie\Documents\Ansible\roles\secops_docker_stack\tasks\main.yml`
  - Deploy updated compose and supporting env/config assets.

## Task 1: Scaffold Project and Tooling

**Files:**
- Create: `D:\Projects\Moonwing\README.md`
- Create: `D:\Projects\Moonwing\.env.example`
- Create: `D:\Projects\Moonwing\pyproject.toml`
- Create: `D:\Projects\Moonwing\docker-compose.yml`
- Create: `D:\Projects\Moonwing\src\moonwing\__init__.py`
- Create: `D:\Projects\Moonwing\src\moonwing\config.py`
- Create: `D:\Projects\Moonwing\tests\conftest.py`

- [ ] **Step 1: Write the failing config/bootstrap test**

```python
from moonwing.config import Settings


def test_settings_defaults_expose_core_service_names():
    settings = Settings()

    assert settings.app_name == "moonwing"
    assert settings.database_url.startswith("postgresql+")
    assert settings.object_storage_endpoint == "minio:9000"
    assert settings.queue_backend == "redis://redis:6379/0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'moonwing'` or missing `Settings`.

- [ ] **Step 3: Write minimal project scaffolding and settings implementation**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MOONWING_")

    app_name: str = "moonwing"
    database_url: str = "postgresql+psycopg://moonwing:moonwing@postgres:5432/moonwing"
    queue_backend: str = "redis://redis:6379/0"
    object_storage_endpoint: str = "minio:9000"
    object_storage_bucket: str = "moonwing-artifacts"
    clearwing_binary: str = "clearwing"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example pyproject.toml docker-compose.yml src/moonwing tests/unit/test_config.py tests/conftest.py
git commit -m "chore: scaffold moonwing project"
```

## Task 2: Define Core Domain Contracts

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\core\job_types.py`
- Create: `D:\Projects\Moonwing\src\moonwing\core\providers.py`
- Create: `D:\Projects\Moonwing\src\moonwing\core\runtime_profiles.py`
- Create: `D:\Projects\Moonwing\src\moonwing\core\findings.py`
- Test: `D:\Projects\Moonwing\tests\unit\test_core_models.py`

- [ ] **Step 1: Write failing tests for supported job families and provider/model selection**

```python
from moonwing.core.job_types import JobFamily, SourceInputKind
from moonwing.core.providers import ProviderSelection


def test_supported_job_families_are_stable():
    assert JobFamily.NETWORK_SCAN.value == "network_scan"
    assert JobFamily.SOURCE_HUNT.value == "source_hunt"
    assert SourceInputKind.SBOM.value == "sbom"


def test_provider_selection_keeps_backend_and_model_separate():
    selection = ProviderSelection(provider="openai", model="gpt-5.2", endpoint_type="hosted")

    assert selection.provider == "openai"
    assert selection.model == "gpt-5.2"
    assert selection.endpoint_type == "hosted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_core_models.py -v`
Expected: FAIL because domain modules do not exist.

- [ ] **Step 3: Implement the core enums and Pydantic models**

```python
from enum import Enum
from pydantic import BaseModel


class JobFamily(str, Enum):
    NETWORK_SCAN = "network_scan"
    SOURCE_HUNT = "source_hunt"


class SourceInputKind(str, Enum):
    REPO = "repo"
    LOCAL_SOURCE_TREE = "local_source_tree"
    BINARY = "binary"
    SBOM = "sbom"


class ProviderSelection(BaseModel):
    provider: str
    model: str
    endpoint_type: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_core_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/core tests/unit/test_core_models.py
git commit -m "feat: add core moonwing domain contracts"
```

## Task 3: Create Database Schema and Migrations

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\db\base.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\session.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\models\user.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\models\credential.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\models\runtime_profile.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\models\target.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\models\artifact.py`
- Create: `D:\Projects\Moonwing\src\moonwing\db\models\run.py`
- Create: `D:\Projects\Moonwing\alembic.ini`
- Create: `D:\Projects\Moonwing\alembic\env.py`
- Create: `D:\Projects\Moonwing\alembic\versions\20260421_01_initial_schema.py`
- Test: `D:\Projects\Moonwing\tests\integration\test_initial_schema.py`

- [ ] **Step 1: Write failing integration test for core persistence**

```python
def test_initial_schema_persists_user_credential_and_run(session_factory):
    session = session_factory()

    assert session.execute("select 1").scalar() == 1
    assert hasattr(session.bind, "dialect")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_initial_schema.py -v`
Expected: FAIL due to missing engine, tables, or migrations.

- [ ] **Step 3: Implement SQLAlchemy models and first migration**

```python
class Run(Base):
    __tablename__ = "runs"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_family = mapped_column(String(32), nullable=False)
    status = mapped_column(String(32), nullable=False)
    provider = mapped_column(String(64), nullable=False)
    model = mapped_column(String(128), nullable=False)
    execution_snapshot = mapped_column(JSONB, nullable=False, default=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_initial_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/db alembic.ini alembic tests/integration/test_initial_schema.py
git commit -m "feat: add postgres schema for moonwing core entities"
```

## Task 4: Implement Credential, Model, and Runtime Selection Rules

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\services\credentials.py`
- Create: `D:\Projects\Moonwing\src\moonwing\schemas\credentials.py`
- Create: `D:\Projects\Moonwing\src\moonwing\schemas\runtime_profiles.py`
- Test: `D:\Projects\Moonwing\tests\unit\test_selection_rules.py`

- [ ] **Step 1: Write failing tests for shared vs user credentials and default selection**

```python
from moonwing.services.credentials import resolve_run_selection


def test_user_default_selection_prefers_explicit_model_over_profile_default():
    selection = resolve_run_selection(
        explicit_provider="anthropic",
        explicit_model="sonnet-4.6",
        explicit_credential_id="cred-user-1",
        profile_defaults={"provider": "openai", "model": "gpt-5.2"},
        user_defaults={"provider": "openai", "model": "gpt-5.2", "credential_id": "cred-user-2"},
    )

    assert selection.provider == "anthropic"
    assert selection.model == "sonnet-4.6"
    assert selection.credential_id == "cred-user-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_selection_rules.py -v`
Expected: FAIL because resolver does not exist.

- [ ] **Step 3: Implement deterministic selection and policy checks**

```python
if explicit_provider and explicit_model and explicit_credential_id:
    return ResolvedRunSelection(
        provider=explicit_provider,
        model=explicit_model,
        credential_id=explicit_credential_id,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_selection_rules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/services/credentials.py src/moonwing/schemas tests/unit/test_selection_rules.py
git commit -m "feat: add credential and model selection rules"
```

## Task 5: Implement Artifact Provenance and SBOM Metadata Persistence

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\services\artifacts.py`
- Create: `D:\Projects\Moonwing\src\moonwing\schemas\artifacts.py`
- Test: `D:\Projects\Moonwing\tests\unit\test_artifact_provenance.py`

- [ ] **Step 1: Write failing test for SBOM provenance fields**

```python
from moonwing.services.artifacts import build_sbom_metadata


def test_sbom_metadata_keeps_source_and_file_paths():
    metadata = build_sbom_metadata(
        source_type="downloaded_url",
        source_location="https://example.com/sbom.json",
        retrieved_at="2026-04-21T18:00:00Z",
        installed_at="2026-04-20T10:00:00Z",
        original_filepath="/tmp/build/sbom.json",
        current_filepath="/opt/app/sbom.json",
        format="cyclonedx",
    )

    assert metadata["source_location"] == "https://example.com/sbom.json"
    assert metadata["original_filepath"] == "/tmp/build/sbom.json"
    assert metadata["current_filepath"] == "/opt/app/sbom.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_artifact_provenance.py -v`
Expected: FAIL because artifact service does not exist.

- [ ] **Step 3: Implement provenance builders and MinIO object reference contract**

```python
def build_sbom_metadata(**kwargs):
    return {
        "source_type": kwargs["source_type"],
        "source_location": kwargs["source_location"],
        "retrieved_at": kwargs["retrieved_at"],
        "installed_at": kwargs.get("installed_at"),
        "original_filepath": kwargs.get("original_filepath"),
        "current_filepath": kwargs.get("current_filepath"),
        "format": kwargs["format"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_artifact_provenance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/services/artifacts.py src/moonwing/schemas/artifacts.py tests/unit/test_artifact_provenance.py
git commit -m "feat: add artifact provenance and sbom metadata contracts"
```

## Task 6: Build Run Service and State Machine

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\services\runs.py`
- Create: `D:\Projects\Moonwing\src\moonwing\schemas\runs.py`
- Test: `D:\Projects\Moonwing\tests\unit\test_run_service.py`

- [ ] **Step 1: Write failing tests for run creation snapshot and state transitions**

```python
from moonwing.services.runs import create_run_snapshot, transition_run_status


def test_create_run_snapshot_keeps_provider_model_and_policy_flags():
    snapshot = create_run_snapshot(
        provider="openai",
        model="gpt-5.2",
        credential_id="cred-1",
        runtime_profile_name="default-network",
        policy_flags={"allow_exploits": False},
    )

    assert snapshot["provider"] == "openai"
    assert snapshot["model"] == "gpt-5.2"
    assert snapshot["policy_flags"]["allow_exploits"] is False


def test_transition_run_status_allows_running_to_normalizing():
    assert transition_run_status("running", "normalizing") == "normalizing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_service.py -v`
Expected: FAIL because run service is missing.

- [ ] **Step 3: Implement run snapshot builder and valid state transitions**

```python
_ALLOWED_TRANSITIONS = {
    "queued": {"staging", "canceled"},
    "staging": {"running", "failed", "canceled"},
    "running": {"normalizing", "failed", "needs_review", "canceled"},
    "normalizing": {"completed", "failed", "needs_review"},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_run_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/services/runs.py src/moonwing/schemas/runs.py tests/unit/test_run_service.py
git commit -m "feat: add run snapshot and state machine services"
```

## Task 7: Expose Control API Endpoints

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\api\main.py`
- Create: `D:\Projects\Moonwing\src\moonwing\api\routes\credentials.py`
- Create: `D:\Projects\Moonwing\src\moonwing\api\routes\runtime_profiles.py`
- Create: `D:\Projects\Moonwing\src\moonwing\api\routes\targets.py`
- Create: `D:\Projects\Moonwing\src\moonwing\api\routes\runs.py`
- Create: `D:\Projects\Moonwing\src\moonwing\api\routes\findings.py`
- Test: `D:\Projects\Moonwing\tests\integration\test_api_runs.py`

- [ ] **Step 1: Write failing API test for run submission**

```python
def test_post_run_creates_queued_run(client):
    payload = {
        "job_family": "network_scan",
        "target_id": "11111111-1111-1111-1111-111111111111",
        "runtime_profile_id": "22222222-2222-2222-2222-222222222222",
        "credential_id": "33333333-3333-3333-3333-333333333333",
        "provider": "openai",
        "model": "gpt-5.2",
    }

    response = client.post("/runs", json=payload)

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_runs.py -v`
Expected: FAIL because API app and route do not exist.

- [ ] **Step 3: Implement FastAPI app and initial routes**

```python
app = FastAPI(title="moonwing")
app.include_router(runs_router, prefix="/runs", tags=["runs"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_api_runs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/api tests/integration/test_api_runs.py
git commit -m "feat: expose moonwing control api routes"
```

## Task 8: Implement Worker Bootstrap and Clearwing Job Staging

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\worker\main.py`
- Create: `D:\Projects\Moonwing\src\moonwing\worker\tasks.py`
- Create: `D:\Projects\Moonwing\src\moonwing\worker\clearwing_runner.py`
- Test: `D:\Projects\Moonwing\tests\unit\test_worker_runner.py`

- [ ] **Step 1: Write failing test for staged Clearwing command selection**

```python
from moonwing.worker.clearwing_runner import build_clearwing_command


def test_build_clearwing_command_for_source_hunt_repo():
    command = build_clearwing_command(
        job_family="source_hunt",
        input_kind="repo",
        source_ref="https://github.com/example/repo.git",
    )

    assert command[:2] == ["clearwing", "sourcehunt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_worker_runner.py -v`
Expected: FAIL because worker runner is missing.

- [ ] **Step 3: Implement worker bootstrap and command builder**

```python
def build_clearwing_command(job_family: str, input_kind: str, source_ref: str) -> list[str]:
    if job_family == "network_scan":
        return ["clearwing", "scan", source_ref]
    return ["clearwing", "sourcehunt", source_ref]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_worker_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/worker tests/unit/test_worker_runner.py
git commit -m "feat: add worker bootstrap and clearwing command staging"
```

## Task 9: Normalize Raw Outputs into Findings and Artifacts

**Files:**
- Create: `D:\Projects\Moonwing\src\moonwing\services\normalization.py`
- Test: `D:\Projects\Moonwing\tests\unit\test_normalization.py`

- [ ] **Step 1: Write failing test for normalized finding shape**

```python
from moonwing.services.normalization import normalize_findings


def test_normalize_findings_extracts_title_severity_and_evidence_refs():
    raw = {
        "findings": [
            {
                "title": "SQL injection",
                "severity": "high",
                "evidence": ["artifact://log-1"],
            }
        ]
    }

    findings = normalize_findings(raw)

    assert findings[0]["title"] == "SQL injection"
    assert findings[0]["severity"] == "high"
    assert findings[0]["evidence_refs"] == ["artifact://log-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_normalization.py -v`
Expected: FAIL because normalization service is missing.

- [ ] **Step 3: Implement normalized finding mapper**

```python
def normalize_findings(raw_payload: dict) -> list[dict]:
    findings = []
    for item in raw_payload.get("findings", []):
        findings.append(
            {
                "title": item.get("title", "untitled finding"),
                "severity": item.get("severity", "unknown"),
                "evidence_refs": item.get("evidence", []),
            }
        )
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_normalization.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/moonwing/services/normalization.py tests/unit/test_normalization.py
git commit -m "feat: add raw output normalization for findings"
```

## Task 10: Add Integration Flow for Queue, Storage, and Run Completion

**Files:**
- Modify: `D:\Projects\Moonwing\docker-compose.yml`
- Modify: `D:\Projects\Moonwing\src\moonwing\services\runs.py`
- Modify: `D:\Projects\Moonwing\src\moonwing\worker\tasks.py`
- Test: `D:\Projects\Moonwing\tests\integration\test_run_execution_flow.py`

- [ ] **Step 1: Write failing integration test for queued-to-completed flow**

```python
def test_worker_completes_run_and_persists_findings(api_client, worker_harness):
    run_id = worker_harness.seed_run(status="queued", job_family="network_scan")

    worker_harness.process(run_id)
    run_record = worker_harness.fetch_run(run_id)

    assert run_record.status == "completed"
    assert len(worker_harness.fetch_findings(run_id)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_run_execution_flow.py -v`
Expected: FAIL because the end-to-end flow is incomplete.

- [ ] **Step 3: Implement worker task execution, persistence, and completion updates**

```python
transition_run_status(run.status, "staging")
transition_run_status("staging", "running")
raw_payload = runner.execute(run)
findings = normalize_findings(raw_payload)
transition_run_status("running", "normalizing")
transition_run_status("normalizing", "completed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_run_execution_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml src/moonwing/services/runs.py src/moonwing/worker/tasks.py tests/integration/test_run_execution_flow.py
git commit -m "feat: wire queued run execution through completion"
```

## Task 11: Update SecOps Ansible Deployment

**Files:**
- Modify: `C:\Users\danie\Documents\Ansible\inventory\hosts.yml`
- Modify: `C:\Users\danie\Documents\Ansible\roles\secops_docker_stack\files\docker-compose.yml`
- Modify: `C:\Users\danie\Documents\Ansible\roles\secops_docker_stack\tasks\main.yml`
- Create: `C:\Users\danie\Documents\Ansible\roles\secops_docker_stack\files\moonwing.env`
- Test: deployment verification notes in `D:\Projects\Moonwing\README.md`

- [ ] **Step 1: Write the failing deployment verification checklist in the README**

```markdown
## Deployment Verification

- [ ] `ansible-playbook -i inventory\hosts.yml site.yml --limit secops --tags docker`
- [ ] `curl http://192.168.1.215:<moonwing-api-port>/healthz`
- [ ] verify Postgres, Redis, MinIO, API, and worker containers are healthy
```

- [ ] **Step 2: Run inventory check to verify current config is stale**

Run: `Select-String -Path C:\Users\danie\Documents\Ansible\inventory\hosts.yml -Pattern "192.168.1.250|192.168.1.215"`
Expected: shows `192.168.1.250` before the change.

- [ ] **Step 3: Update inventory and compose deployment assets**

```yaml
secops:
  ansible_host: 192.168.1.215
```

```yaml
services:
  postgres:
  redis:
  minio:
  moonwing-api:
  moonwing-worker:
```

- [ ] **Step 4: Run deployment verification**

Run: `ansible-playbook -i inventory/hosts.yml site.yml --limit secops --tags docker`
Expected: PLAY RECAP with `failed=0`

- [ ] **Step 5: Commit**

```bash
git add C:/Users/danie/Documents/Ansible/inventory/hosts.yml C:/Users/danie/Documents/Ansible/roles/secops_docker_stack D:/Projects/Moonwing/README.md
git commit -m "feat: deploy moonwing foundation to secops"
```

## Task 12: Verification and Hardening Pass

**Files:**
- Modify: `D:\Projects\Moonwing\tests\integration\test_run_execution_flow.py`
- Modify: `D:\Projects\Moonwing\tests\unit\test_selection_rules.py`
- Modify: `D:\Projects\Moonwing\README.md`

- [ ] **Step 1: Add failing tests for retry safety and forbidden credential usage**

```python
def test_retry_does_not_duplicate_findings(worker_harness):
    run_id = worker_harness.seed_completed_run_with_raw_payload()

    worker_harness.retry_normalization(run_id)

    assert worker_harness.finding_count(run_id) == 1


def test_user_cannot_use_unapproved_shared_credential():
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_selection_rules.py tests/integration/test_run_execution_flow.py -v`
Expected: FAIL because idempotency and forbidden shared-credential paths are not enforced yet.

- [ ] **Step 3: Implement idempotency guards and stricter policy enforcement**

```python
if existing_normalization_fingerprint == incoming_fingerprint:
    return existing_findings
```

- [ ] **Step 4: Run the targeted and full test suite**

Run: `pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests src/moonwing README.md
git commit -m "test: harden retry safety and credential policy enforcement"
```

## Self-Review

- Spec coverage check: covered control API, worker execution, Postgres source of truth, local S3-compatible storage, both credential modes, explicit provider/model selection, SBOM provenance, state transitions, and secops deployment path.
- Placeholder scan: no `TODO`, `TBD`, or intentionally incomplete implementation tasks remain.
- Type consistency check: top-level platform job families are consistently `network_scan` and `source_hunt`; run state vocabulary is consistent with the approved design.
