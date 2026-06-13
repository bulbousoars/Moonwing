"""Stage 1 — acquire source code into the workdir."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moonwing.core.pipeline import StageContext

logger = logging.getLogger("moonwing.source_hunt.acquire")


class SourceAcquisitionError(RuntimeError):
    pass


@dataclass
class AcquireResult:
    kind: str          # 'repo' | 'path'
    source_ref: str    # original input (url or path)
    root: str          # absolute path inside workdir to scan
    notes: list[str]   # human-readable steps taken


@dataclass
class AcquireStage:
    """Resolve ``source_ref`` into a local directory under ``ctx.workdir``.

    Inputs (read from ``ctx.extras``):
      * ``input_kind`` — 'repo' or 'path'
      * ``source_ref`` — URL for repo, absolute path for path
      * ``git_clone_timeout`` — optional override, default 300s

    Output: ``AcquireResult`` keyed under ``ctx.data["acquire"]``.
    """

    name: str = "acquire"
    runner: Any = None  # injectable subprocess.run-shaped callable for tests

    def run(self, ctx: StageContext) -> AcquireResult:
        input_kind = ctx.extras.get("input_kind") or "repo"
        source_ref = (ctx.extras.get("source_ref") or "").strip()
        if not source_ref:
            raise SourceAcquisitionError("source_ref is empty")
        if not ctx.workdir:
            raise SourceAcquisitionError("workdir is required")

        workdir = Path(ctx.workdir)
        workdir.mkdir(parents=True, exist_ok=True)

        if input_kind == "path":
            return self._acquire_path(source_ref, ctx)
        if input_kind == "repo":
            return self._acquire_repo(source_ref, workdir, ctx)
        raise SourceAcquisitionError(f"unsupported input_kind for source_hunt: {input_kind!r}")

    # ----- repo --------------------------------------------------------

    def _acquire_repo(
        self, url: str, workdir: Path, ctx: StageContext
    ) -> AcquireResult:
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("git@")):
            raise SourceAcquisitionError(f"unsupported repo URL scheme: {url!r}")

        clone_dir = workdir / "source"
        if clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)

        timeout = int(ctx.extras.get("git_clone_timeout") or 300)
        cmd = ["git", "clone", "--depth", "1", "--no-tags", url, str(clone_dir)]
        runner = self.runner or subprocess.run
        notes = [f"running: {' '.join(cmd)}"]

        try:
            result = runner(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise SourceAcquisitionError("git binary not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceAcquisitionError(
                f"git clone timed out after {timeout}s"
            ) from exc

        returncode = getattr(result, "returncode", 1)
        stderr = (getattr(result, "stderr", "") or "")[:500]
        if returncode != 0:
            raise SourceAcquisitionError(
                f"git clone failed (exit {returncode}): {stderr or 'no stderr'}"
            )

        notes.append(f"cloned into {clone_dir}")
        return AcquireResult(
            kind="repo", source_ref=url, root=str(clone_dir), notes=notes
        )

    # ----- path --------------------------------------------------------

    def _acquire_path(self, path: str, ctx: StageContext) -> AcquireResult:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise SourceAcquisitionError(f"path does not exist: {resolved}")
        if not resolved.is_dir():
            raise SourceAcquisitionError(f"path is not a directory: {resolved}")
        if not os.access(resolved, os.R_OK):
            raise SourceAcquisitionError(f"path not readable: {resolved}")
        return AcquireResult(
            kind="path",
            source_ref=path,
            root=str(resolved),
            notes=[f"using existing path {resolved}"],
        )
