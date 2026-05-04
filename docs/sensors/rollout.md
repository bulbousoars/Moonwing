# Moonwing Sensor Rollout

## Phase 1: Manager Contract

- Sensor enrollment, token hashing, heartbeat, policy fetch, task polling, task result upload, and event upload.
- Moonwing stores endpoint inventory, network identity, queued tasks, and sensor events.

## Phase 2: Linux Sensor

- Python sensor package runs under `systemd`.
- Ansible role installs the package, enrolls once, and starts `moonwing-sensor.service`.
- Initial collectors: host, packages, process snapshot, network identity.

## Phase 3: Windows Sensor

- PowerShell installer copies the sensor package, enrolls once, and registers a Windows Service.
- Initial collectors: host, installed apps from registry, network identity.

## Phase 4: Policy and Hunt Layer

- Add policy groups, endpoint labels, fleet task dispatch, and aggregate task result views.
- Keep task execution allowlisted. Do not add arbitrary shell execution by default.

## Phase 5: macOS Sensor

- Add a `launchd` daemon and signed package after Linux and Windows are stable.
- Initial collectors: host, installed apps, Homebrew inventory, launch items, network identity, and FSEvents-backed FIM.
- Endpoint Security Framework is deferred until signing and entitlement requirements are justified.
