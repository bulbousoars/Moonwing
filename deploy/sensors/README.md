# Endpoint sensor rollout (Compose + stable manager URL)

## Easiest path — use the Moonwing website (good for beginners)

Sign in → **Sensors** → **Install sensor**.

| PC type | What to do |
|---------|------------|
| **Windows** | Click **Download Windows installer (ZIP)**. Unzip every file into one folder, then double-click **`Run Moonwing Sensor Setup.bat`**. Approve the Windows security prompt. |
| **Linux** | Click **Download** on the Linux card and run that script once as **root** (or use `deploy/sensors/install-sensor.sh` from Git with URL + token from the page). |

Below: **automated / terminal** rollout from a Git checkout.

---

This matches the **DNS/IP + UI token** model:

1. **Manager** runs where endpoints can reach it—typically **`docker compose --profile app`** behind a stable **DNS name or IP** (`https://moonwing.internal.example.com` or `http://10.x.x.x:8000` with TLS ideally provided by your reverse proxy).
2. Administrator sets **`MOONWING_SENSOR_ENROLLMENT_TOKEN`** on the manager, restarts API, opens **Management → Sensors → Install**, and copies the **enrollment token** (treat like a bootstrap secret).
3. On each endpoint, run the **same-version** installers in this folder. They accept the manager URL and token via **CLI flags or environment variables** (no multicast discovery—you standardize hostname/IP/DNS administratively).

**Environment variables**

| Variable | Meaning |
|---------|---------|
| `MOONWING_MANAGER_URL` | Required. Base URL users use in the browser, e.g. `https://moonwing.internal.example.com` (no trailing path). |
| `MOONWING_ENROLLMENT_TOKEN` | Required. Same value as `MOONWING_SENSOR_ENROLLMENT_TOKEN` on the manager. |

## Linux

```bash
chmod +x install-sensor.sh
sudo MOONWING_MANAGER_URL=https://moonwing.example.com \
  MOONWING_ENROLLMENT_TOKEN='<paste-from-ui>' \
  ./install-sensor.sh

# or
sudo ./install-sensor.sh --manager-url https://moonwing.example.com --enrollment-token '<paste-from-ui>'
```

## Windows (Administrator PowerShell)

```powershell
cd deploy\sensors
$env:MOONWING_MANAGER_URL = 'https://moonwing.example.com'
$env:MOONWING_ENROLLMENT_TOKEN = '<paste-from-ui>'
.\install-sensor.ps1

# or
.\install-sensor.ps1 -ManagerUrl https://moonwing.example.com -EnrollmentToken '<paste-from-ui>'
```

## Relationship to UI-generated installers

**Management → Sensors → Install** offers **pre-filled** downloads: Linux `.sh`, Windows `.ps1`, and a **Windows ZIP** (double-click `.bat`) for non-technical users. The scripts in this folder are the same idea with URL + token supplied by you—useful for Ansible, imaging, Intune, or air-gapped copies of the repo.

Operational behavior must stay aligned with `src/moonwing/services/sensor_installer.py`; update both if the enrollment heartbeat contract changes.

## Full Python sensor (optional)

For development or when you already have the repo plus Python on disk, see `deploy/windows/install-moonwing-sensor.ps1`, which installs the `moonwing_sensor` package from `src/` and registers a Windows service.
