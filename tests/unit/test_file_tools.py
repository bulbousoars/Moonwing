from __future__ import annotations

from pathlib import Path

import pytest

from moonwing.core.tools import ToolContext
from moonwing.core.tools.file_tools import (
    GLOB_FILES_SPEC,
    GREP_FILES_SPEC,
    READ_FILE_SPEC,
)


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "import os\n"
        "PASSWORD = 'hunter2'  # do not commit\n"
        "def run():\n"
        "    return os.environ.get('TOKEN')\n"
    )
    (tmp_path / "src" / "config.py").write_text(
        "DEBUG = True\nAPI_KEY = 'sk-test'\n"
    )
    (tmp_path / "README.md").write_text("hello\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def t(): pass\n")
    return tmp_path


def _ctx(workdir: Path) -> ToolContext:
    return ToolContext(run_id=None, workdir=str(workdir))


# ----------------------- read_file -----------------------------------


def test_read_file_returns_lines(workdir):
    result = READ_FILE_SPEC.handler({"path": "src/main.py"}, _ctx(workdir))
    assert result.ok
    assert "import os" in result.output["lines"][0]
    assert result.output["start_line"] == 1
    assert result.output["truncated"] is False


def test_read_file_supports_line_range(workdir):
    result = READ_FILE_SPEC.handler(
        {"path": "src/main.py", "start_line": 2, "line_count": 2}, _ctx(workdir)
    )
    assert result.ok
    assert "PASSWORD" in result.output["lines"][0]
    assert result.output["lines"][1].startswith("def run")


def test_read_file_rejects_traversal(workdir):
    result = READ_FILE_SPEC.handler({"path": "../../etc/passwd"}, _ctx(workdir))
    assert not result.ok
    assert "escapes workdir" in result.error


def test_read_file_rejects_absolute_path(workdir):
    result = READ_FILE_SPEC.handler({"path": "C:/Windows/system32/config"}, _ctx(workdir))
    assert not result.ok
    assert "escapes workdir" in result.error


def test_read_file_missing_path(workdir):
    result = READ_FILE_SPEC.handler({}, _ctx(workdir))
    assert not result.ok
    assert "required" in result.error


def test_read_file_missing_file(workdir):
    result = READ_FILE_SPEC.handler({"path": "src/nope.py"}, _ctx(workdir))
    assert not result.ok
    assert "not found" in result.error


def test_read_file_no_workdir():
    result = READ_FILE_SPEC.handler({"path": "x"}, ToolContext(run_id=None))
    assert not result.ok
    assert "workdir is not configured" in result.error


# ----------------------- glob_files ---------------------------------


def test_glob_files_finds_by_extension(workdir):
    result = GLOB_FILES_SPEC.handler({"pattern": "**/*.py"}, _ctx(workdir))
    assert result.ok
    matches = set(result.output["matches"])
    assert "src/main.py" in matches
    assert "src/config.py" in matches
    assert "tests/test_main.py" in matches
    assert "README.md" not in matches


def test_glob_files_matches_basename(workdir):
    result = GLOB_FILES_SPEC.handler({"pattern": "test_*.py"}, _ctx(workdir))
    assert result.ok
    assert "tests/test_main.py" in result.output["matches"]


def test_glob_files_requires_pattern(workdir):
    result = GLOB_FILES_SPEC.handler({}, _ctx(workdir))
    assert not result.ok


# ----------------------- grep_files ---------------------------------


def test_grep_finds_pattern_in_python_files(workdir):
    result = GREP_FILES_SPEC.handler(
        {"pattern": r"PASSWORD\s*=", "glob": "**/*.py"}, _ctx(workdir)
    )
    assert result.ok
    hits = result.output["hits"]
    assert len(hits) == 1
    assert hits[0]["path"] == "src/main.py"
    assert hits[0]["line"] == 2
    assert "PASSWORD" in hits[0]["text"]


def test_grep_case_insensitive(workdir):
    result = GREP_FILES_SPEC.handler(
        {"pattern": "password", "case_insensitive": True}, _ctx(workdir)
    )
    assert result.ok
    assert any("PASSWORD" in h["text"] for h in result.output["hits"])


def test_grep_rejects_invalid_regex(workdir):
    result = GREP_FILES_SPEC.handler({"pattern": "[unterminated"}, _ctx(workdir))
    assert not result.ok
    assert "invalid regex" in result.error


def test_grep_respects_max_hits(workdir):
    # Sprinkle the same token across many lines.
    spam = workdir / "src" / "spam.py"
    spam.write_text("\n".join(f"BANG_{i} = {i}" for i in range(50)))

    result = GREP_FILES_SPEC.handler(
        {"pattern": r"^BANG_\d+", "max_hits": 5}, _ctx(workdir)
    )
    assert result.ok
    assert len(result.output["hits"]) == 5
    assert result.output["truncated"] is True


def test_glob_files_does_not_return_symlink_escape(workdir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 'outside'\n")
    link = workdir / "src" / "outside_link.py"
    try:
        link.symlink_to(outside / "secret.py")
    except OSError:
        pytest.skip("symlink creation not permitted on this platform")

    result = GLOB_FILES_SPEC.handler({"pattern": "**/*.py"}, _ctx(workdir))

    assert result.ok
    assert "src/outside_link.py" not in result.output["matches"]


def test_grep_files_does_not_read_symlink_escape(workdir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("EXTERNAL_SECRET = 'outside'\n")
    link = workdir / "src" / "outside_link.py"
    try:
        link.symlink_to(outside / "secret.py")
    except OSError:
        pytest.skip("symlink creation not permitted on this platform")

    result = GREP_FILES_SPEC.handler(
        {"pattern": "EXTERNAL_SECRET", "glob": "**/*.py"}, _ctx(workdir)
    )

    assert result.ok
    assert result.output["hits"] == []


# ----------------------- registration --------------------------------


def test_file_tools_appear_in_default_registry():
    from moonwing.core.tools import DEFAULT_REGISTRY

    names = DEFAULT_REGISTRY.names()
    for n in ("read_file", "glob_files", "grep_files"):
        assert n in names
