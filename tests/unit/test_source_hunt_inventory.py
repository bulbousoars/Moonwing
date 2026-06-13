from __future__ import annotations

from pathlib import Path

from moonwing.core.pipeline import StageContext
from moonwing.core.source_hunt.acquire import AcquireResult
from moonwing.core.source_hunt.inventory import InventoryStage


def _seed_repo(root: Path) -> None:
    """Build a small repo that exercises the exclusion rules."""
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("def x(): return 1\n")
    (root / "app" / "config.toml").write_text("[a]\nb = 1\n")

    (root / "tests").mkdir()
    (root / "tests" / "test_main.py").write_text("def t(): pass\n")

    (root / "docs").mkdir()
    (root / "docs" / "diagram.png").write_bytes(b"\x89PNG\x00\x00\x00\x00fake")

    (root / "node_modules").mkdir()
    (root / "node_modules" / "vendored.js").write_text("module.exports = {};\n")

    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    (root / "build").mkdir()
    (root / "build" / "out.min.js").write_text("a=1;")


def _ctx_with_acquire(root: Path) -> StageContext:
    ctx = StageContext(run_id=None, workdir=str(root.parent))
    ctx.put("acquire", AcquireResult(kind="path", source_ref=str(root), root=str(root), notes=[]))
    return ctx


def test_inventory_excludes_node_modules_and_dotgit(tmp_path):
    _seed_repo(tmp_path)
    ctx = _ctx_with_acquire(tmp_path)
    result = InventoryStage().run(ctx)

    relpaths = {f.relpath for f in result.files}
    assert "app/main.py" in relpaths
    assert "tests/test_main.py" in relpaths
    assert "app/config.toml" in relpaths

    # Excluded: node_modules + .git
    assert not any(r.startswith("node_modules/") for r in relpaths)
    assert not any(r.startswith(".git/") for r in relpaths)


def test_inventory_excludes_binary_by_extension(tmp_path):
    _seed_repo(tmp_path)
    ctx = _ctx_with_acquire(tmp_path)
    result = InventoryStage().run(ctx)

    relpaths = {f.relpath for f in result.files}
    assert "docs/diagram.png" not in relpaths
    assert "build/out.min.js" not in relpaths


def test_inventory_flags_text_vs_binary(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text("import os\n")
    (tmp_path / "src" / "blob").write_bytes(b"\x00" * 10)

    ctx = _ctx_with_acquire(tmp_path)
    result = InventoryStage().run(ctx)

    by_rel = {f.relpath: f for f in result.files}
    assert by_rel["src/code.py"].is_text is True
    assert by_rel["src/blob"].is_text is False


def test_inventory_orders_results_stably(tmp_path):
    _seed_repo(tmp_path)
    ctx = _ctx_with_acquire(tmp_path)
    a = InventoryStage().run(ctx)
    b = InventoryStage().run(ctx)
    assert [f.relpath for f in a.files] == [f.relpath for f in b.files]


def test_inventory_counts_scanned_and_excluded(tmp_path):
    _seed_repo(tmp_path)
    ctx = _ctx_with_acquire(tmp_path)
    result = InventoryStage().run(ctx)

    # 3 kept (.py + .toml + test) + 1 excluded by pattern (.png) — build/
    # and node_modules/ are pruned dirs so their contents never get scanned.
    assert result.total_files_scanned >= 4
    assert result.total_files_excluded >= 1
    # node_modules + .git + build -> excluded dirs
    assert result.excluded_dirs >= 3
