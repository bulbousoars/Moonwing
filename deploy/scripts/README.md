# Moonwing deploy scripts

Two ways to ship a Moonwing checkout to VM 215 (`secops`, `192.168.1.215`).

## 1. Ansible (preferred)

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --limit secops --tags manager
```

The `moonwing_manager` role:

1. rsyncs the local checkout to `/mnt/storage/moonwing/app`
2. ensures `/mnt/storage/moonwing/app/venv` exists
3. runs `pip install -e .` in the venv (picks up new deps from `pyproject.toml`)
4. runs `alembic upgrade head`
5. installs `/etc/systemd/system/moonwing-{api,worker}.service` from templates
6. restarts both services

The `moonwing_sensor` role (separate, see `--tags sensor`) deploys the
endpoint agent to hosts in the `moonwing_sensors` group.

## 2. PowerShell quick-deploy (Windows, no Ansible)

```powershell
.\deploy\scripts\deploy-manager.ps1
.\deploy\scripts\deploy-manager.ps1 -SkipMigrations
.\deploy\scripts\deploy-manager.ps1 -SkipRestart
```

Same five-step pipeline as the Ansible role, executed from a Windows
checkout via WSL + sshpass. Reuses the OpenBao offline escrow pattern
established in `C:\Users\danie\homelab_ssh.ps1`.

## What this replaces

Prior workflow (drift-prone):

- Edit files under `C:\Users\danie\moonwing-ui-work` (not a git repo)
- `scp` individual files into `/mnt/storage/moonwing/app/src/...`
- *Also* `cp` files into `/mnt/storage/moonwing/app/venv/lib/.../site-packages/moonwing/` because the live service was importing from the installed package
- `systemctl restart moonwing-api`

The new workflow guarantees:

- Production code only ever comes from a checkout of this repo
- `pip install -e .` keeps the installed package linked to the same source tree, so there is no second copy under `site-packages`
- Migrations are always applied as part of deploy
- Both services restart together
