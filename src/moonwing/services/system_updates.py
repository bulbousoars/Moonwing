"""In-manager checks and apply flow for upgrading the Moonwing codebase.

Runs git, pip (editable install), alembic, and optionally systemd restart from
paths configured via Settings. Intended for admins on the manager host.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field


class ApplyStepResult(BaseModel):
    name: str
    exit_code: int
    stdout: str = ''
    stderr: str = ''


class ApplyOutcome(BaseModel):
    success: bool
    message: str
    steps: list[ApplyStepResult] = Field(default_factory=list)


@dataclass
class GitUpdateStatus:
    package_version: str
    repo_path_setting: str
    repo_path_resolved: str | None
    repo_ready: bool
    is_git_repository: bool
    git_unreachable_reason: str | None
    local_branch: str | None
    local_commit_short: str | None
    local_commit_full: str | None
    remote: str
    remote_branch_spec: str
    fetch_attempted: bool
    fetch_error: str | None
    commits_behind: int | None
    upstream_commit_short: str | None
    upstream_subject: str | None
    working_tree_dirty: bool | None
    apply_available_reason: str | None

    github_remote_commit_short: str | None = None
    github_remote_subject: str | None = None
    github_compare_error: str | None = None


def packaged_version() -> str:
    try:
        from importlib.metadata import version

        return version('moonwing')
    except Exception:
        return 'unknown'


def _resolve_repo(repo_setting: str) -> Path | None:
    stripped = repo_setting.strip()
    if not stripped:
        return None
    path = Path(stripped).expanduser()
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _git_run(repo: Path, args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['git', '-C', str(repo.resolve()), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _cmd_run(argv: list[str], *, cwd: Path | None, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def resolve_venv_python(repo: Path, configured: str) -> str:
    if configured.strip():
        return configured.strip()
    candidates = (
        repo / 'venv' / 'bin' / 'python',
        repo / 'venv' / 'Scripts' / 'python.exe',
        repo / '.venv' / 'bin' / 'python',
        repo / '.venv' / 'Scripts' / 'python.exe',
    )
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())
    return sys.executable


def _parse_systemd_units(raw: str) -> list[str]:
    return [p.strip() for p in raw.replace('\n', ',').split(',') if p.strip()]


def fetch_github_tip(repository: str, branch: str) -> tuple[str | None, str | None, str | None]:
    """Return (sha7, subject, error)."""
    spec = repository.strip()
    if not spec or '/' not in spec:
        return None, None, None
    owner, slash, rest = spec.partition('/')
    repo = rest.strip('/') if slash else ''
    owner = owner.strip()
    repo = repo.strip().replace('.git', '')
    if not owner or not repo or '/' in repo:
        return None, None, 'GITHUB_REPOSITORY must be owner/repo'

    api = os.environ.get('MOONWING_GITHUB_API_URL') or ''
    api = api.rstrip('/') if api.strip() else 'https://api.github.com'

    branch_enc = quote(branch.strip(), safe='')
    url = f'{api}/repos/{owner}/{repo}/commits/{branch_enc}'
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                url,
                headers={
                    'Accept': 'application/vnd.github+json',
                    'User-Agent': 'moonwing-update-check',
                },
            )
        if r.status_code != 200:
            return None, None, f'GitHub API HTTP {r.status_code}: {(r.text or "")[:200]}'
        data = r.json()
        sha = data.get('sha')
        msg = ''
        cm = data.get('commit')
        if isinstance(cm, dict):
            msg = str(cm.get('message', '')).split('\n')[0]
        sha7 = sha[:7] if isinstance(sha, str) else None
        return sha7, msg or None, None
    except httpx.HTTPError as exc:
        return None, None, str(exc)


def _git_stdout(repo: Path, args: list[str], *, timeout: int = 60) -> tuple[str | None, str | None]:
    proc = _git_run(repo, args, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or 'git command failed'
        return None, err
    text = proc.stdout.strip()
    return text or '', None


def build_git_update_status(settings) -> GitUpdateStatus:
    from moonwing.config import Settings

    assert isinstance(settings, Settings)

    remote = getattr(settings, 'update_git_remote', 'origin').strip() or 'origin'
    branch = getattr(settings, 'update_git_branch', 'main').strip() or 'main'
    repo_setting = getattr(settings, 'update_repo_path', '') or ''

    gh_repo = getattr(settings, 'update_github_repository', '') or ''
    gh_branch = getattr(settings, 'update_github_branch', branch) or branch

    gh_short: str | None = None
    gh_subj: str | None = None
    gh_err: str | None = None
    if gh_repo.strip():
        gh_short, gh_subj, gh_err = fetch_github_tip(gh_repo.strip(), gh_branch.strip() or branch)

    repo_path = _resolve_repo(repo_setting)
    base = GitUpdateStatus(
        package_version=packaged_version(),
        repo_path_setting=repo_setting,
        repo_path_resolved=str(repo_path) if repo_path else None,
        repo_ready=False,
        is_git_repository=False,
        git_unreachable_reason=None,
        local_branch=None,
        local_commit_short=None,
        local_commit_full=None,
        remote=remote,
        remote_branch_spec=f'{remote}/{branch}',
        fetch_attempted=False,
        fetch_error=None,
        commits_behind=None,
        upstream_commit_short=None,
        upstream_subject=None,
        working_tree_dirty=None,
        apply_available_reason=None,
        github_remote_commit_short=gh_short,
        github_remote_subject=gh_subj,
        github_compare_error=gh_err,
    )

    if repo_path is None:
        base.git_unreachable_reason = 'MOONWING_UPDATE_REPO_PATH is not configured'
        base.apply_available_reason = 'Configure MOONWING_UPDATE_REPO_PATH to enable in-app upgrades.'
        return base

    if not repo_path.is_dir():
        base.git_unreachable_reason = f'Configured path does not exist: {repo_path}'
        base.apply_available_reason = 'Repo path invalid — fix MOONWING_UPDATE_REPO_PATH.'
        return base

    git_dir = repo_path / '.git'
    if git_dir.exists():
        base.is_git_repository = git_dir.is_file() or git_dir.is_dir()
    if not base.is_git_repository:
        base.git_unreachable_reason = 'Directory is not a git checkout (missing .git). Use Ansible/git deploy or rely on GitHub tip below.'
        base.repo_ready = True
        base.apply_available_reason = 'Apply requires a git checkout — configure deployment with .git retained or pull manually.'
        return base

    branch_out, ber = _git_stdout(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD'])
    short_out, ser = _git_stdout(repo_path, ['rev-parse', '--short', 'HEAD'])
    full_out, fer = _git_stdout(repo_path, ['rev-parse', 'HEAD'])

    errors = []
    if ber:
        errors.append(f'branch: {ber}')
    if ser:
        errors.append(f'short-sha: {ser}')
    if fer:
        errors.append(f'full-sha: {fer}')
    if errors:
        base.repo_ready = True
        base.git_unreachable_reason = '; '.join(errors)
        base.apply_available_reason = base.git_unreachable_reason
        return base

    base.repo_ready = True
    base.local_branch = branch_out.strip() if branch_out else None
    base.local_commit_short = short_out.strip() if short_out else None
    base.local_commit_full = full_out.strip() if full_out else None

    dirty_proc = _git_run(repo_path, ['status', '--porcelain'], timeout=30)
    if dirty_proc.returncode == 0:
        base.working_tree_dirty = bool(dirty_proc.stdout.strip())
    else:
        base.working_tree_dirty = None

    # Fetch tracked branch tip (works even without local upstream configured)
    base.fetch_attempted = True
    fp = _git_run(repo_path, ['fetch', remote, branch], timeout=120)
    if fp.returncode != 0:
        base.fetch_error = fp.stderr.strip() or fp.stdout.strip() or f'fetch exit {fp.returncode}'
        if base.working_tree_dirty:
            base.apply_available_reason = 'Fetch failed — fix network or SSH remotes. Working tree dirty; commit or stash before apply.'
        elif base.fetch_error:
            base.apply_available_reason = f'Cannot compare upstream: {base.fetch_error[:200]}'
        return base

    behind_out, bh_err = _git_stdout(repo_path, ['rev-list', '--count', 'HEAD..FETCH_HEAD'])
    if bh_err or behind_out is None:
        base.fetch_error = base.fetch_error or bh_err or 'Could not compute commits-behind FETCH_HEAD'
    else:
        try:
            base.commits_behind = int((behind_out or '0').strip())
        except ValueError:
            base.commits_behind = None
            base.fetch_error = base.fetch_error or 'Invalid rev-list output'

    usubj, usr = _git_stdout(repo_path, ['log', '-1', '--pretty=%s', 'FETCH_HEAD'], timeout=30)
    if usr:
        base.fetch_error = base.fetch_error or usr
    elif usubj:
        base.upstream_subject = usubj.strip()

    ushort, usr2 = _git_stdout(repo_path, ['rev-parse', '--short', 'FETCH_HEAD'], timeout=30)
    if usr2:
        base.fetch_error = base.fetch_error or usr2
    elif ushort:
        base.upstream_commit_short = ushort.strip()

    if base.fetch_error:
        base.apply_available_reason = base.fetch_error[:500]
        return base
    if base.working_tree_dirty:
        base.apply_available_reason = 'Working tree is dirty — commit or stash changes before upgrading.'
        return base
    if base.commits_behind is None:
        base.apply_available_reason = 'Upstream comparison unavailable.'
        return base
    if base.commits_behind == 0:
        base.apply_available_reason = 'Already at the fetched tip.'
        return base
    base.apply_available_reason = None
    return base


def apply_system_update(settings) -> ApplyOutcome:
    from moonwing.config import Settings

    assert isinstance(settings, Settings)

    remote = getattr(settings, 'update_git_remote', 'origin').strip() or 'origin'
    branch = getattr(settings, 'update_git_branch', 'main').strip() or 'main'
    repo_path = _resolve_repo(getattr(settings, 'update_repo_path', '') or '')
    statuses: list[ApplyStepResult] = []

    if repo_path is None or not repo_path.is_dir():
        return ApplyOutcome(success=False, message='MOONWING_UPDATE_REPO_PATH is invalid', steps=[])

    git_marker = repo_path / '.git'
    if not (git_marker.is_file() or git_marker.is_dir()):
        return ApplyOutcome(success=False, message='Not a git repository — apply is disabled', steps=[])

    py = resolve_venv_python(repo_path, getattr(settings, 'update_venv_python', '') or '')
    cwd = repo_path.resolve()
    pip_timeout = getattr(settings, 'update_pip_timeout_seconds', 600)
    alembic_timeout = getattr(settings, 'update_alembic_timeout_seconds', 300)

    gs = build_git_update_status(settings)
    if gs.working_tree_dirty:
        return ApplyOutcome(success=False, message='Refusing to upgrade with a dirty working tree', steps=[])
    if gs.fetch_error:
        return ApplyOutcome(success=False, message=f'Upstream check failed: {gs.fetch_error}', steps=[])
    if gs.commits_behind is None:
        return ApplyOutcome(success=False, message='Upstream comparison unavailable — cannot safely apply.', steps=[])
    if gs.commits_behind == 0:
        return ApplyOutcome(success=False, message='Already up to date with the fetched remote tip.', steps=[])

    # pull --ff-only
    ff = ['git', '-C', str(cwd), 'pull', '--ff-only', remote, branch]
    p1 = _cmd_run(ff, cwd=None, env={**os.environ}, timeout=300)
    statuses.append(_step('git pull --ff-only', p1))
    if p1.returncode != 0:
        return ApplyOutcome(success=False, message='git pull failed', steps=statuses)

    pip_cmd = [py, '-m', 'pip', 'install', '-e', '.']
    p2 = _cmd_run(pip_cmd, cwd=cwd, env={**os.environ}, timeout=pip_timeout)
    statuses.append(_step('pip install -e .', p2))
    if p2.returncode != 0:
        return ApplyOutcome(success=False, message='pip install failed', steps=statuses)

    alembic_env = {**os.environ, 'PYTHONPATH': str((cwd / 'src').resolve())}
    alb = [py, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head']
    if not (cwd / 'alembic.ini').is_file():
        return ApplyOutcome(success=False, message='alembic.ini not found in repo root', steps=statuses)
    p3 = _cmd_run(alb, cwd=cwd, env=alembic_env, timeout=alembic_timeout)
    statuses.append(_step('alembic upgrade head', p3))
    if p3.returncode != 0:
        return ApplyOutcome(success=False, message='alembic migration failed', steps=statuses)

    units_raw = getattr(settings, 'update_systemd_units', '') or ''
    units = _parse_systemd_units(units_raw)
    sc_bin = shutil_which_systemctl()
    if units and platform.system() == 'Linux' and sc_bin:
        for unit in units:
            p4 = _cmd_run([sc_bin, 'restart', unit], cwd=None, env={**os.environ}, timeout=120)
            statuses.append(_step(f'systemctl restart {unit}', p4))
            if p4.returncode != 0:
                return ApplyOutcome(success=False, message=f'systemctl restart {unit} failed', steps=statuses)
        return ApplyOutcome(
            success=True,
            message='Upgrade completed and systemd units restarted; you may lose this UI briefly.',
            steps=statuses,
        )

    skip_reason = ''
    if not units:
        skip_reason = 'No systemd units configured (MOONWING_UPDATE_SYSTEMD_UNITS). Restart services manually.'
    elif platform.system() != 'Linux':
        skip_reason = 'Non-linux host — restart services manually.'
    else:
        skip_reason = 'systemctl binary not found — restart services manually.'

    return ApplyOutcome(success=True, message=f'Upgrade completed. {skip_reason}', steps=statuses)


def _step(name: str, proc: subprocess.CompletedProcess[str]) -> ApplyStepResult:
    return ApplyStepResult(name=name, exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def shutil_which_systemctl() -> str | None:
    from shutil import which

    return which('systemctl')
