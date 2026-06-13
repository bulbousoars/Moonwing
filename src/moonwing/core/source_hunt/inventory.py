"""Stage 2 — walk the acquired tree and build a file inventory.

Cheap, deterministic, side-effect free. Output feeds the rank stage so
the LLM sees a digestible directory tree instead of the whole repo.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from moonwing.core.pipeline import StageContext
from .acquire import AcquireResult

logger = logging.getLogger("moonwing.source_hunt.inventory")


# Conservative defaults — these mostly match what `ripgrep --hidden` excludes
# by default plus a few language-specific build/vendored dirs.
_DEFAULT_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "coverage",
    }
)

# Files we never want a hunter to spend tokens on.
_DEFAULT_EXCLUDE_PATTERNS = (
    "*.min.js",
    "*.map",
    "*.lock",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.pdf",
    "*.zip",
    "*.gz",
    "*.tar",
    "*.bz2",
    "*.xz",
    "*.7z",
    "*.so",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.class",
    "*.jar",
    "*.pyc",
    "*.pyo",
    "*.o",
    "*.a",
)


@dataclass
class FileEntry:
    """One file in the inventory."""

    relpath: str       # POSIX-style relative path
    size_bytes: int
    extension: str
    is_text: bool


@dataclass
class InventoryResult:
    root: str
    files: list[FileEntry] = field(default_factory=list)
    total_files_scanned: int = 0
    total_files_excluded: int = 0
    excluded_dirs: int = 0


_MAX_FILE_SIZE_BYTES = 1_000_000  # skip files > 1MB; almost always vendored


def _is_excluded_dir(name: str) -> bool:
    return name in _DEFAULT_EXCLUDE_DIRS


def _is_excluded_file(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in _DEFAULT_EXCLUDE_PATTERNS)


def _looks_textual(path: Path, sniff_bytes: int = 4096) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(sniff_bytes)
    except OSError:
        return False
    if not chunk:
        return True  # empty file — treat as text
    # Heuristic: NUL byte or >30% non-text bytes → binary.
    if b"\x00" in chunk:
        return False
    text_chars = bytes(range(0x20, 0x7F)) + b"\r\n\t\f\b"
    nontext = sum(b not in text_chars for b in chunk)
    return (nontext / len(chunk)) <= 0.30


@dataclass
class InventoryStage:
    name: str = "inventory"

    def run(self, ctx: StageContext) -> InventoryResult:
        acquire: AcquireResult | None = ctx.get("acquire")
        if acquire is None:
            raise RuntimeError("inventory stage requires 'acquire' output in context")

        root = Path(acquire.root)
        result = InventoryResult(root=str(root))

        for dirpath, dirnames, filenames in os.walk(root):
            # In-place filter dirnames to prune the walk.
            kept_dirs = []
            for d in list(dirnames):
                if _is_excluded_dir(d):
                    result.excluded_dirs += 1
                else:
                    kept_dirs.append(d)
            dirnames[:] = kept_dirs

            for fname in filenames:
                result.total_files_scanned += 1
                if _is_excluded_file(fname):
                    result.total_files_excluded += 1
                    continue

                abs_path = Path(dirpath) / fname
                try:
                    stat = abs_path.stat()
                except OSError:
                    result.total_files_excluded += 1
                    continue

                if stat.st_size > _MAX_FILE_SIZE_BYTES:
                    result.total_files_excluded += 1
                    continue

                rel = abs_path.relative_to(root).as_posix()
                result.files.append(
                    FileEntry(
                        relpath=rel,
                        size_bytes=stat.st_size,
                        extension=abs_path.suffix.lower(),
                        is_text=_looks_textual(abs_path),
                    )
                )

        # Stable order — smaller files first within each directory keeps
        # the LLM's view tidy and reduces token spend on early scans.
        result.files.sort(key=lambda f: (f.relpath.count("/"), f.relpath))
        return result
