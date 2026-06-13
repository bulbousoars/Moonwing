"""File-read tools for the source-hunt hunter.

All operations are scoped to ``ctx.workdir``. Path-escape attempts (absolute
paths, ``..`` traversal, symlinks pointing outside) are rejected before
any I/O so a misbehaving model can't read host files.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from .registry import Capability, ToolContext, ToolDomain, ToolResult, ToolSpec


# Per-tool output caps — keep token cost predictable.
_MAX_READ_LINES = 400
_MAX_READ_BYTES = 80_000
_MAX_GLOB_HITS = 200
_MAX_GREP_HITS = 100


def _resolve_safe_path(workdir: str, relpath: str) -> Path:
    """Resolve ``relpath`` against ``workdir`` and assert containment.

    Raises ``ValueError`` if the resolved path is outside the workdir
    (catches both ``../`` traversal and absolute-path inputs after the
    OS normalizes them).
    """
    if not workdir:
        raise ValueError("workdir is not configured for this run")
    root = Path(workdir).resolve()
    candidate = (root / relpath).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workdir: {relpath!r}") from exc
    return candidate


def _is_safe_child(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def _read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return ToolResult(ok=False, output=None, error="path is required")

    start = int(args.get("start_line") or 1)
    count = int(args.get("line_count") or _MAX_READ_LINES)
    count = min(count, _MAX_READ_LINES)
    if start < 1:
        return ToolResult(ok=False, output=None, error="start_line must be >= 1")

    try:
        target = _resolve_safe_path(ctx.workdir or "", path)
    except ValueError as exc:
        return ToolResult(ok=False, output=None, error=str(exc))

    if not target.exists():
        return ToolResult(ok=False, output=None, error=f"not found: {path}")
    if not target.is_file():
        return ToolResult(ok=False, output=None, error=f"not a file: {path}")
    if target.stat().st_size > 10_000_000:
        return ToolResult(ok=False, output=None, error="file too large (>10MB)")

    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            # Skip lines before ``start``.
            for _ in range(start - 1):
                if fh.readline() == "":
                    return ToolResult(
                        ok=True,
                        output={
                            "path": path,
                            "start_line": start,
                            "lines": [],
                            "truncated_eof": True,
                        },
                    )
            collected: list[str] = []
            bytes_so_far = 0
            for _ in range(count):
                line = fh.readline()
                if line == "":
                    break
                bytes_so_far += len(line)
                if bytes_so_far > _MAX_READ_BYTES:
                    break
                collected.append(line.rstrip("\n"))
            truncated = fh.readline() != ""
    except OSError as exc:
        return ToolResult(ok=False, output=None, error=f"read failed: {exc}")

    return ToolResult(
        ok=True,
        output={
            "path": path,
            "start_line": start,
            "lines": collected,
            "truncated": truncated,
        },
    )


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    domain=ToolDomain.DATA,
    description=(
        "Read a slice of a text file in the run's working tree. Returns "
        "the requested lines as a list of strings. Defaults to the first "
        f"{_MAX_READ_LINES} lines; pass start_line and line_count to page."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the workdir, e.g. 'src/app.py'.",
            },
            "start_line": {"type": "integer", "minimum": 1},
            "line_count": {"type": "integer", "minimum": 1, "maximum": _MAX_READ_LINES},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    capabilities=frozenset(),
    handler=_read_file,
)


# ---------------------------------------------------------------------------
# glob_files
# ---------------------------------------------------------------------------

def _glob_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return ToolResult(ok=False, output=None, error="pattern is required")
    if not ctx.workdir:
        return ToolResult(ok=False, output=None, error="workdir is not configured")

    root = Path(ctx.workdir).resolve()
    hits: list[str] = []
    truncated = False

    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if not _is_safe_child(root, path):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if fnmatch.fnmatchcase(rel, pattern) or fnmatch.fnmatchcase(path.name, pattern):
                hits.append(rel)
                if len(hits) >= _MAX_GLOB_HITS:
                    truncated = True
                    break
    except OSError as exc:
        return ToolResult(ok=False, output=None, error=f"glob failed: {exc}")

    return ToolResult(ok=True, output={"pattern": pattern, "matches": hits, "truncated": truncated})


GLOB_FILES_SPEC = ToolSpec(
    name="glob_files",
    domain=ToolDomain.DATA,
    description=(
        "List files in the workdir matching a glob pattern. Patterns are "
        "matched against both the full relative path and the base name, "
        "e.g. '**/*.py', 'auth*.go', 'src/handlers/*.ts'."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    capabilities=frozenset(),
    handler=_glob_files,
)


# ---------------------------------------------------------------------------
# grep_files
# ---------------------------------------------------------------------------

def _grep_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return ToolResult(ok=False, output=None, error="pattern is required")
    glob = args.get("glob") or "**/*"
    flags = re.IGNORECASE if args.get("case_insensitive") else 0
    max_hits = min(int(args.get("max_hits") or _MAX_GREP_HITS), _MAX_GREP_HITS)
    if not ctx.workdir:
        return ToolResult(ok=False, output=None, error="workdir is not configured")

    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return ToolResult(ok=False, output=None, error=f"invalid regex: {exc}")

    root = Path(ctx.workdir).resolve()
    hits: list[dict[str, Any]] = []
    truncated = False

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if not _is_safe_child(root, path):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not (fnmatch.fnmatchcase(rel, glob) or fnmatch.fnmatchcase(path.name, glob)):
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if compiled.search(line):
                        hits.append(
                            {"path": rel, "line": lineno, "text": line.rstrip("\n")[:240]}
                        )
                        if len(hits) >= max_hits:
                            truncated = True
                            break
        except OSError:
            continue
        if truncated:
            break

    return ToolResult(
        ok=True,
        output={"pattern": pattern, "glob": glob, "hits": hits, "truncated": truncated},
    )


GREP_FILES_SPEC = ToolSpec(
    name="grep_files",
    domain=ToolDomain.DATA,
    description=(
        "Search file contents for a regex pattern. Optionally restrict "
        "with a glob like '**/*.py'. Returns up to a configurable number "
        "of (path, line, text) hits."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex"},
            "glob": {"type": "string", "description": "Optional file glob filter"},
            "case_insensitive": {"type": "boolean"},
            "max_hits": {"type": "integer", "minimum": 1, "maximum": _MAX_GREP_HITS},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    capabilities=frozenset(),
    handler=_grep_files,
)


FILE_READ_SPECS = (READ_FILE_SPEC, GLOB_FILES_SPEC, GREP_FILES_SPEC)
