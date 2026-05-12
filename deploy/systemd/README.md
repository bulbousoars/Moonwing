# Moonwing automatic Compose upgrades (systemd)

This follows the same **scheduled pull + rebuild** pattern many teams use for Compose stacks on Linux: `git fetch`, fast-forward to `origin/main`, `docker compose up --build`.

Here the unit runs **`scripts/moonwing-upgrade.sh`** on a timer: **`git pull --ff-only`**, **`docker compose --profile app up -d --build`**, then waits for **`/health`**.

## Prerequisites

- Linux host with **systemd** and **Docker Compose v2**
- Moonwing **git clone** at a fixed path (default **`/opt/moonwing/Moonwing`**) with **`.env`** present
- Timer runs as **root** by default (typical for unattended deploy wrappers). Adjust `User=` only if that user can run `docker compose` against your stack.

## Install (on the host, recommended)

From the **clone root** after `git pull` so `deploy/systemd/install-on-host.sh` exists:

```bash
cd /opt/moonwing/Moonwing
sudo bash deploy/systemd/install-on-host.sh
```

This copies the unit files, rewrites the default **`/opt/moonwing/Moonwing`** paths to match the detected repo root (via Python), runs **`systemctl daemon-reload`**, and **`systemctl enable --now moonwing-auto-upgrade.timer`**.

### Frequent upgrades (see `git` changes within minutes)

Re-run install with **`MOONWING_AUTO_UPGRADE_INTERVAL_MIN`** set to an integer **1–1440** (minutes between the **end** of one upgrade run and the next scheduled trigger, plus **~90s** after boot):

```bash
cd /path/to/Moonwing
sudo env MOONWING_AUTO_UPGRADE_INTERVAL_MIN=10 bash deploy/systemd/install-on-host.sh
```

This adds **`/etc/systemd/system/moonwing-auto-upgrade.timer.d/50-frequent.conf`** with **`OnUnitActiveSec=`** and reloads the timer. The original **daily ~04:15** calendar from the stock unit **still applies** as well (extra safety net). To remove frequent pulls later, delete that drop-in and `systemctl daemon-reload && systemctl restart moonwing-auto-upgrade.timer`.

## Install (manual copy)

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

## Install from Windows (OpenBao SSH broker)

If you use a broker helper such as **`New-AgentSshSession.ps1`**, open an **admin** session when the remote user must `sudo` without a password (typical for `git` in `/opt/...` and for `install-on-host.sh`):

```powershell
$s = & "C:\path\to\New-AgentSshSession.ps1" -Agent cursor -HostName moonwing.example.org -Admin
cd C:\path\to\Moonwing   # your local clone of this repo
.\deploy\systemd\Install-MoonwingSystemdAutoUpgradeRemote.ps1 `
  -SshConfig (Join-Path $s.session_dir 'ssh_config') `
  -TargetHost moonwing.example.org `
  -GitBranch main `
  -AutoUpgradeIntervalMinutes 10
```

Replace **`moonwing.example.org`** with your SSH target, **`-GitBranch`** with the branch that host should track, and tune **`-AutoUpgradeIntervalMinutes`** (or omit it) for how often you want pull/rebuild after each successful run.

## Behaviour

| Piece | Role |
|--------|------|
| `moonwing-auto-upgrade.timer` | Runs daily (~**04:15** host local time) with **`RandomizedDelaySec=45min`** to avoid thundering herds |
| `timer.d/50-frequent.conf` (optional) | **`OnUnitActiveSec=`** — repeat pull **`MOONWING_AUTO_UPGRADE_INTERVAL_MIN`** minutes after the last service run finished, plus **`OnBootSec=90s`** |
| `moonwing-auto-upgrade.service` | One-shot: runs **`moonwing-upgrade.sh`** |
| `scripts/moonwing-upgrade.sh` | Fetch / **`git pull --ff-only`**, Compose rebuild, **`curl` health** |

Refuses a **dirty** git working tree (same as manual upgrade). For hosts with a deliberate local diff (e.g. pinned `docker-compose.yml` port), either stash, use a branch, **`git update-index --skip-worktree`** on that file (common when only the MinIO published port differs), or set **`MOONWING_SKIP_GIT=1`** in the service environment and rely on another mechanism to sync sources.

**Compose `docker-compose.override.yml`:** Docker merges override `ports` with the base file, so duplicating `minio` ports there can still leave the original host binding (e.g. `9001`) and break `docker compose up`. Prefer **`skip-worktree`** on a single-line edit in `docker-compose.yml`, or resolve the host port conflict globally.

## Sudo-only deploy (agents)

For **OpenSSH agent** access without membership in the `docker` group, use a **root-owned** wrapper and sudoers (see your internal runbook for agent SSH + sudo patterns). The timer can call that wrapper’s absolute path instead of `moonwing-upgrade.sh` if you keep Docker behind sudo.

## Manual test (no timer)

```bash
sudo systemctl start moonwing-auto-upgrade.service
journalctl -u moonwing-auto-upgrade.service -n 100 -f
```
