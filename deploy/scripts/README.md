# Moonwing deploy scripts

Two ways to ship a Moonwing checkout onto a Linux **manager host** — pick the
workflow that fits your toolchain. Prefer keeping **IPs, SSH users, vault
secrets, and hostnames** only in inventory, CI secrets, or a private fork —
not checked into a repository you expose publicly unchanged.

## 1. Ansible (preferred)

```bash
cd deploy/ansible
ansible-playbook -i inventory/hosts.yml site.yml --limit <manager-host> --tags manager
```

The `moonwing_manager` role:

1. Synchronizes the local checkout tree to your configured install directory
2. Ensures the target virtualenv exists under that directory
3. Runs `pip install -e .` in the venv (picks up new deps from `pyproject.toml`)
4. Runs `alembic upgrade head`
5. Installs systemd units for API and worker from templates
6. Restarts both services

The `moonwing_sensor` role (separate, see `--tags sensor`) deploys the
endpoint agent to hosts in the `moonwing_sensors` group — edit
`inventory/hosts.yml` for your sensor fleet.

Defaults such as paths and OS users live in role `defaults/main.yml` — override
per environment with group/host vars rather than committing private topology to
generic docs.

## 2. PowerShell quick-deploy (Windows, no Ansible)

```powershell
.\deploy\scripts\deploy-manager.ps1
.\deploy\scripts\deploy-manager.ps1 -SkipMigrations
.\deploy\scripts\deploy-manager.ps1 -SkipRestart
```

Same conceptual pipeline as the Ansible role (rsync/sync, editable install,
migrations, restarts). The script wraps SSH/rsync from Windows (often via WSL);
configure connection details with script parameters / environment consistent
with your security policy rather than documenting them verbatim in Markdown.

## What this replaces

Older ad-hoc patterns that cause drift:

- Copying fragments into `/venv/.../site-packages` while the repo lives elsewhere.
- Divergent trees on workstations vs the install path on the manager.

The intended workflow guarantees:

- Runtime code aligns with an explicit checkout or sync artifact.
- `pip install -e .` binds the editable package to that tree instead of silently
  forking imports under site-packages alone.
- Migrations run whenever you deploy meaningful schema changes.
