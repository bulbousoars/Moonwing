# Moonwing

Moonwing is a **web-based security operations hub** your team installs on a server **you** control (a VM, bare metal, or cloud instance). Through the browser people can coordinate **network vulnerability scans**, **source-code hunts**, manage **credentials and integrations**, browse **findings**, and enroll **endpoint sensors** (lightweight agents on Linux or Windows PCs/servers).

Behind the scenes a database stores users, scans, artifacts, policies, and sensor data—this is meant for **multi-user**, **auditable** work, not ad-hoc one-off scripting.

---

## Prerequisites

Install on the machine that will host Moonwing:

- **Docker Engine** and the **Compose v2** plugin (`docker compose …`)
- **Git** (`git`)

You open **one TCP port** to users (normally **8000** for the Compose layout below, often **443** in production after you put TLS in front). Endpoint sensors reach the **same public address** over HTTPS—plan a DNS name early so scripts and SSO stay simple.

---

## Recommended install (system administrator): clone the Git repository + Docker Compose

Use a **`git clone`**, not GitHub “Download ZIP”, when you intend to operate this as an installation you will **update over time**:

| Prefer Git checkout | Prefer ZIP less often |
|---|---|
| **`git pull`** reapplies upstream fixes and features in one familiar step | Replacing folders by hand errors easily |
| You can **pin releases** (`git checkout v…`) reproducibly | No clean **remote** metadata for tooling |
| Optional **in-app upgrades** and scripts assume a checkout with origins | Sensors and automation often bundle **installer paths** keyed to repo layout |

ZIP is acceptable only where Git is forbidden; expect a heavier manual bump process.

### Install steps (first boot)

Pick a stable directory owned by whoever runs Docker (often `/opt` on Linux):

```bash
sudo mkdir -p /opt/moonwing
sudo chown "$USER:$USER" /opt/moonwing
cd /opt/moonwing

git clone https://github.com/bulbousoars/Moonwing.git
cd Moonwing

# Optional: deploy a numbered release tag when you publish them
# git checkout v0.2.0

cp .env.example .env
```

Edit `.env`. At minimum for a serious deployment you should assign:

- **`MOONWING_SESSION_SECRET`** — long random secret for cookie sessions  
- **`MOONWING_ENCRYPTION_KEY`** — Fernet key (see [.env.example](.env.example) comment); needed before storing integration API keys  
- **`MOONWING_SENSOR_ENROLLMENT_TOKEN`** — long random string; anyone with it may register sensors—rotate deliberately  
- Change **Postgres / MinIO** passwords inside **both** Compose services and **`MOONWING_DATABASE_URL` / `.env`** if you expose Postgres or MinIO past localhost  

Start the stack and create database tables:

```bash
docker compose --profile app up -d --build
docker compose exec moonwing-api python -m alembic upgrade head
```

Check health from the Docker host:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Browse to `http://YOUR_SERVER:8000`, or your public HTTPS URL once a reverse proxy terminates TLS. Complete any first-run prompts to create your administrator identity.

Expose **HTTPS** publicly for production (`reverse proxy → moonwing-api:8000`; put OIDC and browser flows on that canonical URL).

### Updating after `git pull`

Run from the clone root:

```bash
git pull origin main
# or checkout a newer tag explicitly

docker compose --profile app up -d --build
docker compose exec moonwing-api python -m alembic upgrade head
```

---

## Sensors (fleet endpoints)

Agents **call into** Moonwing—they do not run inside the Compose file on arbitrary LAN machines.

Requirements:

1. **Reachable HTTPS (or LAN HTTP only if policy allows)** on a **hostname or stable IP everyone agrees on** (`https://moonwing.company.internal`, etc.).
2. **Enrollment token**: set `MOONWING_SENSOR_ENROLLMENT_TOKEN` in `.env`; copy the plaintext value once from **Sensors → Install** in the web UI during rollout waves.

Fleet-friendly scripts checked into this repo (same Git clone admins already use):

- [`deploy/sensors/install-sensor.sh`](deploy/sensors/install-sensor.sh) — Linux (root), `curl` + `python3`  
- [`deploy/sensors/install-sensor.ps1`](deploy/sensors/install-sensor.ps1) — Windows (elevated PowerShell)  

Supply:

```bash
export MOONWING_MANAGER_URL=https://moonwing.company.internal
export MOONWING_ENROLLMENT_TOKEN='<paste-from-UI>'
sudo ./deploy/sensors/install-sensor.sh
```

Details: [`deploy/sensors/README.md`](deploy/sensors/README.md).

---

## What Compose runs ([`docker-compose.yml`](docker-compose.yml))

Without `--profile app` you get Postgres, Redis, and MinIO only (for developers coupling a local Python process to those services).

With **`--profile app`**, Compose also builds **`moonwing-api`** (website + REST API on **8000**) and **`moonwing-worker`** from this repository’s Dockerfile. Persisted Docker volumes retain database and MinIO data across restarts.

Those two services use **`depends_on: service_healthy`**: Docker will not start **`moonwing-api`** or **`moonwing-worker`** until Postgres (**`pg_isready`**), Redis (**`redis-cli ping`**), and MinIO (**`GET /minio/health/live`**) pass their **healthcheck** hooks. That avoids most race conditions where the app boots before the database socket or object store is ready.

**`moonwing-api`** also has a Compose health probe that hits **`GET /health`** (process is up and responding). It does **not** verify the SQL schema—run **`alembic upgrade head`** right after the first **`up`**; until migrations exist the API may exit and restart until the schema is in place.
| Service | Rough purpose |
|---------|----------------|
| moonwing-api | Browser UI & HTTP APIs |
| moonwing-worker | Background jobs for queued scans/workflows |
| postgres | Permanent storage |
| redis | Queue placeholder / future buffering |
| minio | Stored scan artifacts blob storage |

Compose publishes **5432 / 6379 / 9000 / 9001 / 8000** on the loopback/host—**tighten firewalls** in production and prefer private Docker networks plus a reverse-proxy surface.

---

## Other deployment modes (advanced)

Administrators integrating with existing fleets may instead:

| Path | Brief |
|------|--------|
| [`deploy/ansible/`](deploy/ansible/) | Install manager or sensors onto raw Linux hosts with Ansible inventories |
| [`deploy/scripts/deploy-manager.ps1`](deploy/scripts/deploy-manager.ps1) | Sync this repo over SSH into venv/systemd installs from a PowerShell workstation |
| [Local development](#local-development-developers-only) below | Postgres/Redis/MinIO in Compose, Python API on laptop |

Compose remains default for “single logical install Git-tracked beside automation.”

---

## Local development (developers only)

```powershell
docker compose up -d postgres redis minio

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

$env:MOONWING_DATABASE_URL = "postgresql+psycopg://moonwing:moonwing@localhost:5432/moonwing"
alembic upgrade head

$env:PYTHONPATH = "src"
python -m uvicorn moonwing.api.main:app --reload

pytest -v
```

---

## Folder map (technical)

```
src/moonwing/         Application packages (API routes, worker, services)
src/moonwing_sensor/  Endpoint agent library (alternate Windows install path uses it out of-repo)
deploy/sensors/       Thin curl/PowerShell enroll scripts for fleets
deploy/ansible/       IaC supplemental roles when Compose is insufficient
deploy/windows/       Python-based Windows installer (developer-style)
docs/                 Long-form notes and rollout phase planning
```

---

## Authentication highlights

Moonwing supports conventional **login email/username + password**, **OIDC SSO** (`MOONWING_OIDC_*`), **LDAP-derived directory synchronization**, granular **IAM / PAM** with audit hooks, dedicated **sensor** bearer identities, plus optional **SMTP** notifications surfaced in `/management/notifications`.

---

## Run/job lifecycle

```
queued → staging → running → normalizing → completed
  ·        ·         ·           ·
failed … canceled … needs_review (where applicable)
```

---

## Operational encryption note

Synthetic provider keys reside encrypted at-rest only after **`MOONWING_ENCRYPTION_KEY`** is populated (generate per `.env.example`).

---

## After-deploy checks (Compose-oriented)

```bash
curl -fsS https://your-public-hostname/health
docker compose ps
docker compose logs -f moonwing-api --tail 50
docker compose exec moonwing-api python -m alembic current
```

---

## Background reading

Historical platform notes live under [`docs/superpowers/specs/`](docs/superpowers/specs/).
Those documents sometimes refer to external scanner or CLI tooling by **product name for historical context** — **Moonwing is an independent project** and **does not** imply affiliation, endorsement, or support from any third-party tool named there.

