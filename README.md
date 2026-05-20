# Moonwing

Moonwing is a **web-based security operations hub** your team installs on a server **you** control (a VM, bare metal, or cloud instance). Through the browser people can coordinate **network vulnerability scans**, **source-code hunts**, manage **credentials and integrations**, browse **findings**, and enroll **endpoint sensors** (lightweight agents on Linux or Windows PCs/servers).

Behind the scenes a database stores users, scans, artifacts, policies, and sensor data—this is meant for **multi-user**, **auditable** work, not ad-hoc one-off scripting.

---

## Super simple (follow in order)

These are the **short** instructions. Use the rest of the README when you need detail.

### A) Start Moonwing on a Linux server with Docker

You need: a terminal open on that server, Docker, Git, and about 10 minutes.

**Step 1 — go to a folder and download Moonwing**

Paste this whole block, press **Enter**, wait until it finishes:

```bash
sudo mkdir -p /opt/moonwing && sudo chown "$USER:$USER" /opt/moonwing && cd /opt/moonwing && git clone https://github.com/bulbousoars/Moonwing.git && cd Moonwing
```

(Optional: `git checkout v0.2.0` first if you deploy from version tags.)

**Step 2 — configure secrets**

Paste this, press **Enter**. A text editor opens:

```bash
cp .env.example .env && ${EDITOR:-nano} .env
```

Fill in at least **`MOONWING_SESSION_SECRET`**, **`MOONWING_ENCRYPTION_KEY`**, and **`MOONWING_SENSOR_ENROLLMENT_TOKEN`** (the file explains how). Save and close the editor.

**Step 3 — start everything**

```bash
./scripts/moonwing-up.sh
```

**Step 4 — open Moonwing in a browser**

From your own PC, visit **`http://THE_SERVER_IP:8000`** (replace with your server’s IP or hostname). Finish the setup screens.

Production: put **HTTPS** in front with your usual reverse proxy.

### B) Put a sensor on a Windows PC (easy)

Someone with **Sensors** permission in Moonwing signs in → **Sensors** → **Install sensor**.

**Step 1** — Click **Download Windows installer (ZIP)**.

**Step 2** — Open the ZIP file, drag **all** files into one folder (for example Desktop → `Moonwing-sensor`).

**Step 3** — Double-click **`Run Moonwing Sensor Setup.bat`**. When Windows asks for permission, choose **Yes**.

**Step 4** — Wait until the window says the install finished. The PC must be able to reach your Moonwing address over the network.

We ship a **ZIP + double-click setup** instead of one big **.exe** so you’re not blocked on code signing yet; an optional signed installer may come later.

For Linux sensors, stay on **Sensors → Install** and use the **Linux** download, or follow [`deploy/sensors/README.md`](deploy/sensors/README.md).

---

## Prerequisites

Install on the machine that will host Moonwing:

- **Docker Engine** and the **Compose v2** plugin (`docker compose …`), **v2.23 or newer** — the stack uses Compose’s **“init job”** semantics so database migrations run automatically before the API starts.
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

```bash
sudo mkdir -p /opt/moonwing && sudo chown "$USER:$USER" /opt/moonwing && cd /opt/moonwing

git clone https://github.com/bulbousoars/Moonwing.git && cd Moonwing
# Optional: git checkout v0.2.0

cp .env.example .env
${EDITOR:-nano} .env
```

Set at least **`MOONWING_SESSION_SECRET`**, **`MOONWING_ENCRYPTION_KEY`**, and **`MOONWING_SENSOR_ENROLLMENT_TOKEN`**, and fix database / MinIO passwords if you changed them anywhere (details in [.env.example](.env.example)).

Start everything (infra health checks, **`alembic upgrade head`** once via `moonwing-migrate`, then API + worker):

```bash
./scripts/moonwing-up.sh
```

(`docker compose --profile app up -d --build` is equivalent if you omit the helper.) Requires **Compose v2.23+** for `service_completed_successfully`.

Smoke test:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Browse to `http://YOUR_SERVER:8000`, or HTTPS behind your reverse proxy, and finish first-run administrator setup.

### Updating after `git pull`

**One command on the server** (git pull with safety checks, rebuild Compose stack, wait for `/health`):

```bash
cd /opt/moonwing/Moonwing   # your clone path
./scripts/moonwing-upgrade.sh
```

Equivalent manual steps:

```bash
git pull origin main && ./scripts/moonwing-up.sh
```

`moonwing-upgrade.sh` refuses a dirty working tree unless you set **`MOONWING_SKIP_GIT=1`** (for rsync-only deploys). Optional: **`MOONWING_GIT_BRANCH`** when there is no upstream tracking branch, **`MOONWING_HEALTH_URL`** if the API is only reachable on another host/port from the server shell.

**From a Windows workstation** (OpenSSH `ssh` on PATH), run the same upgrade on a remote Linux path:

```powershell
cd <your-local-clone>   # any path containing this repo
.\scripts\Invoke-MoonwingUpgradeRemote.ps1 -HostName YOUR_SERVER -RemoteGitRoot /opt/moonwing/Moonwing
```

**Automatic upgrades on Linux:** install the **systemd timer** (see [`deploy/systemd/README.md`](deploy/systemd/README.md)). From Windows, you can drive the same flow over SSH using [`Install-MoonwingSystemdAutoUpgradeRemote.ps1`](deploy/systemd/Install-MoonwingSystemdAutoUpgradeRemote.ps1) with an SSH config produced by your own broker or agent tooling.

Rebuilding runs the migration container again during `up`; **`alembic upgrade head`** is idempotent.

---

## Sensors (fleet endpoints)

Agents **call into** Moonwing—they do not run inside the Compose file.

**Easiest:** sign in → **Sensors → Install sensor** → use the **Windows ZIP** or **Linux script** from that page (same idea as [Super simple](#super-simple-follow-in-order), section **B**).

**Automation / fleets:** [`deploy/sensors/README.md`](deploy/sensors/README.md) — scripts take `MOONWING_MANAGER_URL` + `MOONWING_ENROLLMENT_TOKEN` from the UI.

---

## What Compose runs ([`docker-compose.yml`](docker-compose.yml))

Without `--profile app` you get Postgres, Redis, and MinIO only (for developers coupling a local Python process to those services).

With **`--profile app`**, Compose also builds **`moonwing-api`**, **`moonwing-worker`**, and a **one-shot `moonwing-migrate`** container from this repository’s Dockerfile. **`moonwing-migrate`** waits for Postgres to become healthy, runs **`alembic upgrade head`**, exits successfully, and only then API/worker start (`service_completed_successfully`). Persisted Docker volumes retain database and MinIO data across restarts.

**`moonwing-api`** / **`moonwing-worker`** additionally wait until Redis and MinIO pass their **healthcheck** hooks. **`moonwing-api`** exposes its own Compose health probe for **`GET /health`** once the HTTP server listens.

| Service | Rough purpose |
|---------|----------------|
| moonwing-api | Browser UI & HTTP APIs |
| moonwing-worker | Background jobs for queued scans/workflows |
| postgres | Permanent storage |
| redis | Queue placeholder / future buffering |
| minio | Stored scan artifacts blob storage |

**AI scans** use the provider **HTTP API** and credentials you store in Moonwing (the default worker image does not run local CLIs). **System → Host AI tools** performs a **read-only** check of Claude, Codex, Gemini, Cursor, and Kimi on the **Docker host** via bind-mounted `bin` directories (configured in `docker-compose.yml`). It does not install tools or run them from the browser.

Compose publishes **5432 / 6379 / 9000 / 9001 / 8000** on the loopback/host—**tighten firewalls** in production and prefer private Docker networks plus a reverse-proxy surface.

---

## Other deployment modes (advanced)

Administrators integrating with existing fleets may instead:

| Path | Brief |
|------|--------|
| [`deploy/ansible/`](deploy/ansible/) | Install manager or sensors onto raw Linux hosts with Ansible inventories |
| [`deploy/scripts/deploy-manager.ps1`](deploy/scripts/deploy-manager.ps1) | Sync this repo over SSH into venv/systemd installs from a PowerShell workstation |
| [`deploy/systemd/README.md`](deploy/systemd/README.md) | **systemd timer** — daily `git pull` + Compose rebuild; includes `install-on-host.sh` |
| [`deploy/systemd/Install-MoonwingSystemdAutoUpgradeRemote.ps1`](deploy/systemd/Install-MoonwingSystemdAutoUpgradeRemote.ps1) | **Windows + SSH**: pull + install timer on a remote host using a generated `ssh_config` |
| [`scripts/Invoke-MoonwingUpgradeRemote.ps1`](scripts/Invoke-MoonwingUpgradeRemote.ps1) | From Windows, SSH to the Linux host and run `moonwing-upgrade.sh` in a given repo path |
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
scripts/              Optional helpers (`moonwing-up.sh` → Compose app profile)
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

## Scheduled runs

- UI: **Schedules** in the sidebar (`/schedules`). Cron uses **five fields** (minute hour day-of-month month day-of-week) interpreted in the **timezone** you set (IANA name, e.g. `America/New_York`).
- The **worker** (`moonwing-worker`) evaluates due schedules each poll loop, creates **`queued` runs** with the same fields as **Launch Scan**, then processes them like any other run. Keep a **single worker** if you want at-most-once materialization per tick.
- Dependency: **`croniter`** (declared in `pyproject.toml`).

## SIEM and structured logs

- **HTTP shipping** (optional): set `MOONWING_SIEM_ENABLED=true` and `MOONWING_SIEM_HTTP_URL` to a Logstash **http** input, generic webhook, or other JSON POST endpoint. Optional `MOONWING_SIEM_HTTP_HEADERS_JSON` is a JSON object merged into request headers (e.g. `{"Authorization":"Bearer …"}`).
- Events: **`log_type=moonwing_audit`** (same fields as in-app audit actions) and **`log_type=moonwing_run`** when a run finishes **completed** or **failed** (includes `run_id`, `job_family`, `finding_count` when known).
- **JSON stdout** (optional): set `MOONWING_LOG_JSON_TO_STDOUT=true` on **API** and/or **worker** so each log line is one JSON object—easy to collect with **Filebeat**, **Fluent Bit**, or Docker logging drivers and forward to your SIEM.

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

