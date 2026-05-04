# Moonwing

Moonwing is a Clearwing-backed control plane for multi-user vulnerability
scanning, source hunting, and endpoint sensor management that you typically
deploy on hardware or a VM you operate.

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
alembic/                     Schema migrations (see alembic/versions)
tests/                       Foundation tests (unit/integration) + feature tests
deploy/ansible/              moonwing_manager + moonwing_sensor roles, site.yml
deploy/scripts/              PowerShell quick-deploy (no Ansible required)
deploy/windows/              Windows sensor installer
docs/                        Design specs, plans, sensor rollout notes
```

## Local development

```powershell
# Bring up backing services
docker compose up -d postgres redis minio

# Install Moonwing into a local venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Apply migrations against a local Postgres (matches compose defaults below)
$env:MOONWING_DATABASE_URL = "postgresql+psycopg://moonwing:moonwing@localhost:5432/moonwing"
alembic upgrade head

# Run the API
$env:PYTHONPATH = "src"
python -m uvicorn moonwing.api.main:app --reload

# Run tests
pytest -v
```

The database URL shown here matches the docker-compose Postgres credentials for
developer convenience — change passwords and URLs for anything beyond local use.

## Deployment

Supported patterns for deploying the manager (API + worker) use a Git checkout
(or rsync-equivalent sync) plus `pip install -e .`, migrations, and service
restart. **Docker Compose does not ship endpoint sensors** — use the Ansible
sensor role or `deploy/windows/install-moonwing-sensor.ps1`.

Keep **inventory**, **install directories**, SSH users, and hostnames inside
private Ansible vars or vault — do not paste them into docs you publish.

### Ansible (preferred)

See `deploy/ansible/inventory/` and adjust `hosts.yml` for your hosts and SSH
accounts (do not commit real infrastructure details to a public clone).

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --limit <manager-host> --tags manager
```

### PowerShell quick-deploy (Windows checkout)

From a workstation with SSH/rsync tooling configured for your manager host:

```powershell
.\deploy\scripts\deploy-manager.ps1 -HostIp YOUR_MANAGER_HOST
```

Typical role / script responsibilities:

1. Sync repository tree to an install directory on the manager host
2. `pip install -e .` in the service Python environment
3. `alembic upgrade head`
4. `systemctl restart moonwing-api moonwing-worker` (or equivalents)
5. Health check plus `alembic current`

See [`deploy/scripts/README.md`](deploy/scripts/README.md) for scripting
behavior and knobs.

### Docker Compose (optional)

[`docker-compose.yml`](docker-compose.yml) supports:

1. **Infrastructure only** — `postgres`, `redis`, `minio` (same hybrid dev flow
   as [Local development](#local-development)).
2. **Full manager in containers (`app` profile)** — `moonwing-api` and
   `moonwing-worker` images from the repo [`Dockerfile`](Dockerfile):

   ```bash
   docker compose --profile app up -d --build
   ```

   Good for demos, CI smoke checks, or an all-container environment. Split
   topologies are common too (DB/object store in Compose, processes on the host,
   or vice versa).

### Sensor rollout

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --tags sensor --limit moonwing_sensors
```

Populate `moonwing_sensors` hosts in inventory for Linux targets; Windows hosts
typically use `deploy\windows\install-moonwing-sensor.ps1`.

## Service reference (conceptual)

| Component | Typical role |
|-----------|----------------|
| `moonwing-api` | HTTP API + UI (`uvicorn`; often port 8000) |
| `moonwing-worker` | Run execution worker |
| Reverse proxy | TLS termination / SSO in front of the API (optional) |
| `postgres` | Primary database |
| `redis` | Queue / caching (currently placeholder) |
| `minio` | S3-compatible artifact storage |

Bind addresses, TLS, DNS, and port publishing are deployment-specific — set
them in your proxy, systemd units, Compose file, or cloud load balancer rather
than in this README.

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
- OIDC (`services/oidc.py`, `MOONWING_OIDC_*` env).
- LDAP directory sync (`services/ldap_sync.py`, configurable via Management UI).
- IAM/PAM with audit events and privileged-access grants.
- Service-account / sensor bearer tokens (only hashes stored in DB).

## Notifications

In-app + email (SMTP). Configured at `/management/notifications`. Event types
include: `run_started`, `run_completed`, `critical_finding`, `user_created`,
`user_deleted`, `user_status_changed`, `user_role_changed`.

## Encryption

Provider API keys are Fernet-encrypted at rest (`services/crypto.py`). Generate
a key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `MOONWING_ENCRYPTION_KEY` via your secrets manager or environment —
never commit real keys.

## Verification after deploy

Use your deployed base URL (or LAN IP and port configured on the host):

```bash
curl https://your-moonwing-host/health           # typically {"ok":true}
sudo systemctl is-active moonwing-api moonwing-worker
sudo -u <service-user> <venv>/bin/python -m alembic -c <install-path>/alembic.ini current
```

Adjust paths for your Ansible role defaults or container layout.

## Design references

See `docs/superpowers/specs/2026-04-21-clearwing-platform-foundation-design.md`
and `docs/superpowers/plans/` for the original foundation design and
implementation plans.

Development history is in Git; keep production-specific incident notes out of a
public default branch if they contain infra identifiers.
