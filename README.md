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
- **Endpoint sensors (`moonwing_sensor`)** — Enroll Linux or Windows hosts against
  a manager reachable at a **stable DNS name or IP**; see [Sensor rollout](#sensor-rollout).

## Repository layout

```
src/moonwing/                FastAPI app, worker, services, schemas, models
src/moonwing_sensor/         Endpoint agent package
alembic/                     Schema migrations (see alembic/versions)
tests/                       Foundation tests (unit/integration) + feature tests
deploy/ansible/              Optional moonwing_manager + moonwing_sensor roles, site.yml
deploy/scripts/              Optional PowerShell host sync deploy (venv + systemd)
deploy/sensors/              In-repo Linux / Windows sensor installers (Compose-friendly)
deploy/windows/              Alternate Windows installer (bundles repo Python sensor)
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

**Recommended operators path:** clone or pull tagged releases from this repo, run
the **Docker Compose stack** for the manager, put a **stable DNS name or routable IP**
(and TLS terminating reverse-proxy in production) in front of the API service,
configure `.env`, then onboard endpoints using the **[sensor rollout](#sensor-rollout)** scripts.

Compose does **not** run endpoint agents on other hosts—they connect **inbound** to
your published manager URL (`https://moonwing.<your-domain>` or `http://<ip>:8000`, etc.).
Keep passwords, tokens, inventory, SSH users, and hostnames outside public branches.

### Docker Compose (recommended)

[`docker-compose.yml`](docker-compose.yml) supports:

1. **Infrastructure only** — `postgres`, `redis`, `minio` (same hybrid dev flow
   as [Local development](#local-development)).
2. **Full manager (`app` profile)** — builds `moonwing-api` + `moonwing-worker`
   from [`Dockerfile`](Dockerfile):

   ```bash
   cp .env.example .env          # edit secrets, MOONWING_SENSOR_ENROLLMENT_TOKEN, etc.
   docker compose --profile app up -d --build
   ```

   Configure OIDC (**`MOONWING_OIDC_*`**) if used, and TLS at your ingress so browsers
   and enrollment scripts share one **canonical HTTPS base URL**.

Split topologies remain valid (Compose for DB/store, API on host systemd, Ansible,
Kubernetes, etc.).

### Ansible (alternative)

Fleet managers or mixed Linux estates can use [`deploy/ansible/`](deploy/ansible/).
See `inventory/` placeholders and `--tags manager` / `--tags sensor`.

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --limit <manager-host> --tags manager
```

### PowerShell quick-deploy for Linux manager (optional)

From a workstation with SSH/rsync to the manager VM:

```powershell
.\deploy\scripts\deploy-manager.ps1 -HostIp YOUR_MANAGER_HOST
```

Uses a venv + systemd on the **remote Linux host**. See [`deploy/scripts/README.md`](deploy/scripts/README.md).

### Sensor rollout

Sensors enroll against the HTTP API—you must designate a single **canonical manager
origin** (FQDN preferred; static IP acceptable if HTTPS or network policy permits).

**A + UI token flow**

1. Set `MOONWING_SENSOR_ENROLLMENT_TOKEN` on the manager, restart API, browse
   **Sensors → Install** to reveal/copy the bootstrap token once for your rollout wave.
2. On each endpoint, run the thin installers shipped in this repo (same tag as manager
   or `main`):

   [`deploy/sensors/README.md`](deploy/sensors/README.md)

   Summary:

   ```bash
   chmod +x deploy/sensors/install-sensor.sh
   sudo MOONWING_MANAGER_URL=https://moonwing.example.com \
        MOONWING_ENROLLMENT_TOKEN='<paste from UI>' \
        ./deploy/sensors/install-sensor.sh
   ```

   ```powershell
   cd deploy\sensors
   .\install-sensor.ps1 -ManagerUrl https://moonwing.example.com -EnrollmentToken '<paste from UI>'
   ```

Alternatively download installers pre-filled from **Sensors → Install**, wrap the
above in Ansible/`Invoke-WebRequest` from blob storage, or use the developer-oriented
bundled agent at `deploy/windows/install-moonwing-sensor.ps1` which expects the repo +
Python locally.

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
