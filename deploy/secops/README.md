# Moonwing on secops (`192.168.1.215`)

**Production today:** Docker Compose project `moonwing` on **secops**, API on **port 8000**.

| Service | Container pattern |
|---------|-------------------|
| UI / API | `moonwing-moonwing-api-1` → `http://192.168.1.215:8000` |
| Worker | `moonwing-moonwing-worker-1` |
| Postgres / Redis / MinIO | `moonwing-postgres-1`, etc. |

**Not** on realestate (`192.168.1.218`) — that host runs n8n, LunarLeague, realestate-app.

## One command from Windows (OpenBao SSH — no password)

Checkout on secops: **`/opt/moonwing/Moonwing`**. Uses `cursor-admin` via `New-AgentSshSession.ps1` (see `LunarLeague/docs/AGENT_HOMELAB_SSH.md`).

```powershell
.\scripts\Invoke-MoonwingSecopsRemote.ps1
```

Optional: `-SkipGitPull` if you only changed the overlay locally and already copied it.

## On the server (docker group, e.g. dduggan)

```bash
cd /opt/moonwing/Moonwing
bash deploy/secops/on-host.sh
```

That script:

1. Finds the Moonwing checkout (or use `/opt/moonwing/Moonwing`)
2. **Stops and removes GVM/OpenVAS containers** (`gvm*` names)
3. Rebuilds with `deploy/secops/docker-compose.secops.yml` (host AI bind mounts; MinIO console on **9011** because Portainer uses **9001**)

## GVM / OpenVAS

GVM is **not** part of Moonwing. This overlay **stops** `gvm*` containers so secops RAM goes to ELK + Moonwing. To prevent GVM coming back, edit `/mnt/storage/docker/docker-compose.yml` on secops and remove/comment the `gvm` service after you back up the file.

Optional legacy scanning notes: `Proxmox-DuggancoHomeNetwork/proxmox-gvm-scanning-optional.md` (not required for Moonwing).
