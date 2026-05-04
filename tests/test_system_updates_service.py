"""Unit tests for in-manager Moonwing upgrade checker."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from moonwing.config import Settings
from moonwing.services.system_updates import (
    _parse_systemd_units,
    build_git_update_status,
    fetch_github_tip,
    packaged_version,
    resolve_venv_python,
)


def test_packaged_version_is_non_empty():
    assert isinstance(packaged_version(), str)
    assert len(packaged_version()) > 0


def test_parse_systemd_units():
    assert _parse_systemd_units('a, b') == ['a', 'b']
    assert _parse_systemd_units('') == []


def test_resolve_venv_python_prefers_configured():
    repo = Path('/tmp')
    assert resolve_venv_python(repo, '/usr/bin/python3') == '/usr/bin/python3'


def test_fetch_github_tip_invalid_spec():
    sha, msg, err = fetch_github_tip('', 'main')
    assert sha is None and err is None

    _, _, err_b = fetch_github_tip('too/many/parts/repo', 'main')
    assert err_b is not None


def test_build_status_no_repo_path_configured():
    s = Settings(database_url='sqlite:///:memory:', update_repo_path='')
    gs = build_git_update_status(s)
    assert gs.git_unreachable_reason
    assert 'not configured' in gs.git_unreachable_reason.lower()


def test_build_status_behind_upstream(monkeypatch, tmp_path):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    (repo / '.git').mkdir()

    def fake_git(repo_path: Path, args: list[str], *, timeout: int = 120):
        assert Path(repo_path).resolve() == repo.resolve()
        seq = tuple(args)
        if seq == ('rev-parse', '--abbrev-ref', 'HEAD'):
            return subprocess.CompletedProcess(args, 0, 'main\n', '')
        if seq == ('rev-parse', '--short', 'HEAD'):
            return subprocess.CompletedProcess(args, 0, 'aa1\n', '')
        if seq == ('rev-parse', 'HEAD'):
            return subprocess.CompletedProcess(args, 0, 'deadbeef' * 5 + '\n', '')
        if seq == ('status', '--porcelain'):
            return subprocess.CompletedProcess(args, 0, '', '')
        if seq[:2] == ('fetch', 'origin'):
            return subprocess.CompletedProcess(args, 0, '', '')
        if seq == ('rev-list', '--count', 'HEAD..FETCH_HEAD'):
            return subprocess.CompletedProcess(args, 0, '2\n', '')
        if seq == ('log', '-1', '--pretty=%s', 'FETCH_HEAD'):
            return subprocess.CompletedProcess(args, 0, 'Do the thing\n', '')
        if seq == ('rev-parse', '--short', 'FETCH_HEAD'):
            return subprocess.CompletedProcess(args, 0, 'bb2\n', '')
        raise AssertionError(f'unexpected git args: {args!r}')

    monkeypatch.setattr('moonwing.services.system_updates._git_run', fake_git)
    st = Settings(database_url='sqlite:///:memory:', update_repo_path=str(repo))
    gs = build_git_update_status(st)
    assert gs.commits_behind == 2
    assert gs.apply_available_reason is None
    assert gs.upstream_commit_short == 'bb2'


def test_build_status_dirty_tree_blocks_apply(monkeypatch, tmp_path):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    (repo / '.git').mkdir()

    def fake_git(repo_path: Path, args: list[str], *, timeout: int = 120):
        seq = tuple(args)
        if seq == ('rev-parse', '--abbrev-ref', 'HEAD'):
            return subprocess.CompletedProcess(args, 0, 'main\n', '')
        if seq == ('rev-parse', '--short', 'HEAD'):
            return subprocess.CompletedProcess(args, 0, 'aa1\n', '')
        if seq == ('rev-parse', 'HEAD'):
            return subprocess.CompletedProcess(args, 0, 'aaa\n', '')
        if seq == ('status', '--porcelain'):
            return subprocess.CompletedProcess(args, 0, ' M file\n', '')
        if seq[:2] == ('fetch', 'origin'):
            return subprocess.CompletedProcess(args, 0, '', '')
        if seq == ('rev-list', '--count', 'HEAD..FETCH_HEAD'):
            return subprocess.CompletedProcess(args, 0, '1\n', '')
        if seq == ('log', '-1', '--pretty=%s', 'FETCH_HEAD'):
            return subprocess.CompletedProcess(args, 0, 'x\n', '')
        if seq == ('rev-parse', '--short', 'FETCH_HEAD'):
            return subprocess.CompletedProcess(args, 0, 'cc3\n', '')
        raise AssertionError(args)

    monkeypatch.setattr('moonwing.services.system_updates._git_run', fake_git)
    st = Settings(database_url='sqlite:///:memory:', update_repo_path=str(repo))
    gs = build_git_update_status(st)
    assert gs.working_tree_dirty is True
    assert gs.apply_available_reason
    assert 'dirty' in gs.apply_available_reason.lower()
