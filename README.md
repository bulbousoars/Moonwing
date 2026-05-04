# Moonwing

Moonwing is a Clearwing-backed control plane for multi-user vulnerability
scanning, source hunting, and endpoint sensor management running on the
homelab `secops` VM (`192.168.1.215`).

## Architecture

- **Control API (`moonwing-api`)** — FastAPI app: runs, targets, credentials,
  runtime profiles, findings, IAM/PAM, OIDC sign-in, sensor enrollment, LDAP
  directory sync, email notifications, management UI.
- **Worker (`moonwing-worker`)** — polls Postgres for queued runs, stages
  inputs, executes Clearwing/Claude/Codex CLI or direct provider APIs,
  normalizes findings, writes artifacts to MinIO.
- **Postgres** — system of record for users, credentials, runtime profiles,
  targets, runs, findings, artifacts, sensors, audit events, privileged-access
  grants, SMTP / LDAP / OIDC config, etc.
- **Redis** — placeholder queue backend (current dispatch is in-process).
- **MinIO** — S3-compatible artifact store with provenance tracking.
- **Endpoint sensors (`moonwing_sensor`)** — Python agent installed on Linux
  via Ansible role and on Windows via PowerShell installer. Manager-side
  policies live in Postgres; agents pull tasks and post events.

## Repository layout

```
src/moonwing/                FastAPI app, worker, services, schemas, models
src/moonwing_sensor/         Endpoint agent package
alembic/                     Schema migrations (head: 20260504_01)
tests/                       Foundation tests (unit/integration) + feature tests
deploy/ansible/              moonwing_manager + moonwing_sensor roles, site.yml
deploy/scripts/              PowerShell quick-deploy (no Ansible required)
deploy/windows/              Windows sensor installer
docs/                        Design specs, plans, sensor rollout, navigation
```

## Local development

```powershell
# Bring up backing services
docker compose up -d postgres redis minio

# Install Moonwing into a local venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Apply migrations against a local Postgres
$env:MOONWING_DATABASE_URL = "postgresql+psycopg://moonwing:moonwing@localhost:5432/moonwing"
alembic upgrade head

# Run the API
$env:PYTHONPATH = "src"
python -m uvicorn moonwing.api.main:app --reload

# Run tests
pytest -v
```

## Deployment

Two supported ways. Both replace the prior practice of `scp`-ing into the
production venv's `site-packages`.

### Ansible (preferred)

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --limit secops --tags manager
```

### PowerShell quick-deploy (Windows checkout)

```powershell
.\deploy\scripts\deploy-manager.ps1
```

Both run the same five steps:

1. rsync repo → `/mnt/storage/moonwing/app` on VM 215
2. `pip install -e .` in the production venv
3. `alembic upgrade head`
4. `systemctl restart moonwing-api moonwing-worker`
5. health check + `alembic current`

See [`deploy/scripts/README.md`](deploy/scripts/README.md) for the full
contract and migration story from the legacy `scp` workflow.

### Sensor rollout

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --tags sensor --limit moonwing_sensors
```

Windows hosts use `deploy\windows\install-moonwing-sensor.ps1`.

## Service map (production)

| Service | Host:Port | Description |
|---------|-----------|-------------|
| `moonwing-api` (systemd) | `192.168.1.215:8000` | FastAPI control plane |
| `moonwing-worker` (systemd) | — | Async worker for runs |
| `moonwing.dugganco.com` | Caddy/Traefik → 8000 | Public Authentik-fronted UI |
| `postgres` (docker) | `192.168.1.215:5432` | Primary DB |
| `redis` (docker) | `192.168.1.215:6379` | Reserved for queue dispatch |
| `minio` (docker) | `192.168.1.215:9000/9001` | Artifact store |

## Run state machine

```
queued → staging → running → normalizing → completed
  ↓        ↓         ↓           ↓
failed   failed    failed      failed
  ↑        ↑         ↑
canceled canceled  canceled
                  needs_review
```

## Authentication

- Local username/password (PBKDF2-HMAC-SHA256 via `services/auth.py`).
- OIDC sign-in via Authentik (`services/oidc.py`, `MOONWING_OIDC_*` env).
- LDAP directory sync (`services/ldap_sync.py`, configured via the
  Management UI; values stored in Postgres).
- IAM/PAM with audit events and privileged-access grants.
- Service-account tokens for sensors (bearer; only hashes stored in DB).

## Notifications

In-app + email (SMTP). Configured at `/management/notifications`. Seven event
types: `run_started`, `run_completed`, `critical_finding`, `user_created`,
`user_deleted`, `user_status_changed`, `user_role_changed`.

## Encryption

API keys for upstream providers (OpenAI, Anthropic, OpenRouter, Ollama) are
Fernet-encrypted at rest (`services/crypto.py`). Generate the key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

and place it in `MOONWING_ENCRYPTION_KEY` (escrowed in OpenBao at
`secret/homelab/moonwing` on this homelab).

## Verification after deploy

```bash
curl http://192.168.1.215:8000/health             # {"ok":true}
ssh secops 'systemctl is-active moonwing-api moonwing-worker'
ssh secops '/mnt/storage/moonwing/app/venv/bin/alembic -c /mnt/storage/moonwing/app/alembic.ini current'
```

## Status

Foundation work (tasks 1–10 of the original platform plan) is complete and
shipped. Subsequent feature work — credentials encryption, dual API/CLI
execution modes, IAM/PAM, OIDC, LDAP sync, email notifications, settings CRUD,
website target type, scan guidance UI, run activity timeline, finding
enrichment/insights/status, theme switcher, endpoint sensors — has been
reconciled into this repo from VM 215 and `moonwing-ui-work`.

See `docs/superpowers/specs/2026-04-21-clearwing-platform-foundation-design.md`
for the original platform design and `docs/superpowers/plans/` for the
implementation plan.
