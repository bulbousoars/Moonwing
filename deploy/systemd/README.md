# Moonwing automatic Compose upgrades (systemd)

This matches the **scheduled pull + rebuild** idea used on the **realestate** VM for the Lunar League / Crescent-style stack (`lunarleague-agent-deploy`: `git fetch`, fast-forward to `origin/main`, `docker compose up --build`).

Here the unit runs **`scripts/moonwing-upgrade.sh`** on a timer: **`git pull --ff-only`**, **`docker compose --profile app up -d --build`**, then waits for **`/health`**.

## Prerequisites

- Linux host with **systemd** and **Docker Compose v2**
- Moonwing **git clone** at a fixed path (default **`/opt/moonwing/Moonwing`**) with **`.env`** present
- Timer runs as **root** by default (same as the homelab `moonwing-agent-deploy` / `lunarleague-agent-deploy` wrappers). Adjust `User=` only if that user can run `docker compose` against your stack.

## Install

1. Copy unit files (edit paths first if your clone is not under `/opt/moonwing/Moonwing`):

   ```bash
   sudo cp deploy/systemd/moonwing-auto-upgrade.service deploy/systemd/moonwing-auto-upgrade.timer /etc/systemd/system/
   ```

   If the clone lives elsewhere, either edit **`ExecStart=`** and **`Environment=MOONWING_UPGRADE_REPO=`** in the service file before copying, or add a drop-in after install:

   ```bash
   sudo systemctl edit moonwing-auto-upgrade
   ```

   Example override:

   ```ini
   [Service]
   Environment=MOONWING_UPGRADE_REPO=/data/moonwing/Moonwing
   Environment=MOONWING_GIT_BRANCH=main
   ExecStart=/data/moonwing/Moonwing/scripts/moonwing-upgrade.sh
   ```

2. Reload and enable the timer:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now moonwing-auto-upgrade.timer
   ```

3. Check schedule and last run:

   ```bash
   systemctl list-timers moonwing-auto-upgrade.timer
   journalctl -u moonwing-auto-upgrade.service -n 50 --no-pager
   ```

## Behaviour

| Piece | Role |
|--------|------|
| `moonwing-auto-upgrade.timer` | Runs daily (~**04:15** host local time) with **`RandomizedDelaySec=45min`** to avoid thundering herds |
| `moonwing-auto-upgrade.service` | One-shot: runs **`moonwing-upgrade.sh`** |
| `scripts/moonwing-upgrade.sh` | Fetch / **`git pull --ff-only`**, Compose rebuild, **`curl` health** |

Refuses a **dirty** git working tree (same as manual upgrade). For hosts with a deliberate local diff (e.g. pinned `docker-compose.yml` port), either stash, use a branch, or set **`MOONWING_SKIP_GIT=1`** in the service environment and rely on another mechanism to sync sources.

## Sudo-only deploy (agents)

For **OpenSSH agent** access without membership in the `docker` group, use a **root-owned** wrapper and sudoers, same pattern as **`lunarleague-agent-deploy`** / **`moonwing-agent-deploy`** in `OPENBAO-AGENT-AUTH.md`. The timer can call that wrapper’s absolute path instead of `moonwing-upgrade.sh` if you keep Docker behind sudo.

## Manual test (no timer)

```bash
sudo systemctl start moonwing-auto-upgrade.service
journalctl -u moonwing-auto-upgrade.service -n 100 -f
```
