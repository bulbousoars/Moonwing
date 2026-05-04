# Moonwing Sensor Rollout

Default operator story: managers run behind a **stable DNS name or reachable IP**, and fleets install sensors using **`deploy/sensors/install-sensor.sh`** or **`deploy/sensors/install-sensor.ps1`** with `MOONWING_MANAGER_URL` + enrollment token copied from **Sensors → Install** (or Ansible / MDM-secrets equivalents). Downloads from the UI remain available for logged-in admins.

See repository [`deploy/sensors/README.md`](../../deploy/sensors/README.md) and the [top-level `README.md`](../../README.md) deployment sections.

## Phase 1: Manager Contract

- Sensor enrollment, token hashing, heartbeat, policy fetch, task polling, task result upload, and event upload.
- Moonwing stores endpoint inventory, network identity, queued tasks, and sensor events.

## Phase 2: Linux Sensor

- **Thin rollout:** `deploy/sensors/install-sensor.sh` installs a heartbeat agent + `moonwing-sensor.service` via REST enrollment (minimal deps: `curl`, `python3`).
- Optional **Python sensor package:** Ansible role (`deploy/ansible`) can install `src/moonwing_sensor` onto disk and enroll with the bundled CLI workflow.
- Initial collectors vary by installer (thin agent → basic inventory; full agent → richer inventory when wired).

## Phase 3: Windows Sensor

- **Thin rollout:** `deploy/sensors/install-sensor.ps1` + scheduled-task heartbeat mirror the thin Linux installer.
- Optional **`deploy/windows/install-moonwing-sensor.ps1`:** installs the embedded Python sensor and registers a Windows service for development/full-agent flows.

## Phase 4: Policy and Hunt Layer

- Add policy groups, endpoint labels, fleet task dispatch, and aggregate task result views.
- Keep task execution allowlisted. Do not add arbitrary shell execution by default.

## Phase 5: macOS Sensor

- Add a `launchd` daemon and signed package after Linux and Windows are stable.
- Initial collectors: host, installed apps, Homebrew inventory, launch items, network identity, and FSEvents-backed FIM.
- Endpoint Security Framework is deferred until signing and entitlement requirements are justified.
