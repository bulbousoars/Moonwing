# Endpoint sensor rollout (Compose + stable manager URL)

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

**Management → Sensors → Install** can still download installers with the URL and token **pre-embedded**. The scripts here are identical in behavior—useful for Ansible, imaging, Intune scripts, or “curl this file from Git” workflows without logging into the UI on every host.

Operational behavior must stay aligned with `src/moonwing/services/sensor_installer.py`; update both if the enrollment heartbeat contract changes.

## Full Python sensor (optional)

For development or when you already have the repo plus Python on disk, see `deploy/windows/install-moonwing-sensor.ps1`, which installs the `moonwing_sensor` package from `src/` and registers a Windows service.
