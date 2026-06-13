from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonwing.core.pipeline import StageContext
from moonwing.core.source_hunt.acquire import (
    AcquireStage,
    SourceAcquisitionError,
)


def _ctx(tmp_path: Path, **extras) -> StageContext:
    return StageContext(run_id=None, workdir=str(tmp_path), extras=extras)


# ----------------------- path mode ------------------------------------


def test_acquire_path_succeeds_for_existing_dir(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("print('hi')\n")

    ctx = _ctx(tmp_path, input_kind="path", source_ref=str(src))
    result = AcquireStage().run(ctx)
    assert result.kind == "path"
    assert Path(result.root) == src.resolve()


def test_acquire_path_rejects_missing(tmp_path):
    ctx = _ctx(tmp_path, input_kind="path", source_ref=str(tmp_path / "nope"))
    with pytest.raises(SourceAcquisitionError):
        AcquireStage().run(ctx)


def test_acquire_path_rejects_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hi")
    ctx = _ctx(tmp_path, input_kind="path", source_ref=str(f))
    with pytest.raises(SourceAcquisitionError):
        AcquireStage().run(ctx)


# ----------------------- repo mode ------------------------------------


def test_acquire_repo_calls_git_clone_and_records_notes(tmp_path):
    invocations = []

    def fake_run(cmd, capture_output, text, timeout):
        invocations.append({"cmd": cmd, "timeout": timeout})
        # Pretend git created the dir.
        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    stage = AcquireStage(runner=fake_run)
    ctx = _ctx(
        tmp_path,
        input_kind="repo",
        source_ref="https://github.com/example/repo.git",
        git_clone_timeout=42,
    )
    result = stage.run(ctx)

    assert result.kind == "repo"
    assert Path(result.root) == tmp_path / "source"
    assert invocations[0]["cmd"][:3] == ["git", "clone", "--depth"]
    assert "https://github.com/example/repo.git" in invocations[0]["cmd"]
    assert invocations[0]["timeout"] == 42


def test_acquire_repo_rejects_unsupported_scheme(tmp_path):
    stage = AcquireStage(runner=lambda *a, **kw: None)
    ctx = _ctx(tmp_path, input_kind="repo", source_ref="file:///etc/passwd")
    with pytest.raises(SourceAcquisitionError):
        stage.run(ctx)


def test_acquire_repo_raises_on_git_failure(tmp_path):
    def fake_run(cmd, capture_output, text, timeout):
        return SimpleNamespace(returncode=128, stdout="", stderr="fatal: not found")

    stage = AcquireStage(runner=fake_run)
    ctx = _ctx(tmp_path, input_kind="repo", source_ref="https://example.com/x.git")
    with pytest.raises(SourceAcquisitionError) as info:
        stage.run(ctx)
    assert "git clone failed" in str(info.value)
    assert "fatal: not found" in str(info.value)


def test_acquire_repo_handles_timeout(tmp_path):
    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    stage = AcquireStage(runner=fake_run)
    ctx = _ctx(tmp_path, input_kind="repo", source_ref="https://example.com/x.git")
    with pytest.raises(SourceAcquisitionError) as info:
        stage.run(ctx)
    assert "timed out" in str(info.value)


def test_acquire_repo_handles_missing_git(tmp_path):
    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError("git: not found")

    stage = AcquireStage(runner=fake_run)
    ctx = _ctx(tmp_path, input_kind="repo", source_ref="https://example.com/x.git")
    with pytest.raises(SourceAcquisitionError) as info:
        stage.run(ctx)
    assert "git binary not found" in str(info.value)


# ----------------------- guards ---------------------------------------


def test_requires_source_ref(tmp_path):
    ctx = _ctx(tmp_path, input_kind="repo", source_ref="")
    with pytest.raises(SourceAcquisitionError):
        AcquireStage().run(ctx)


def test_rejects_unknown_input_kind(tmp_path):
    ctx = _ctx(tmp_path, input_kind="binary", source_ref="x")
    with pytest.raises(SourceAcquisitionError):
        AcquireStage().run(ctx)
