from __future__ import annotations

import json
from typing import Any

import pytest

from moonwing.core.llm import LLMResponse, LLMUsage
from moonwing.core.pipeline import StageContext, StageSkipped
from moonwing.core.source_hunt.inventory import FileEntry, InventoryResult
from moonwing.core.source_hunt.rank import RankStage, RankedFile


class ScriptedAdapter:
    provider = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)

    def list_models(self, **kwargs):
        return []


def _ctx_with_inventory(files: list[FileEntry], **extras) -> StageContext:
    ctx = StageContext(
        run_id=None,
        extras={"llm_model": "m", "llm_api_key": "k", **extras},
    )
    ctx.put("inventory", InventoryResult(root="/tmp/repo", files=files))
    return ctx


def test_rank_returns_valid_picks_only():
    files = [
        FileEntry("src/auth.py", 1000, ".py", True),
        FileEntry("src/utils.py", 200, ".py", True),
        FileEntry("docs/readme.md", 50, ".md", True),
    ]
    payload = json.dumps({
        "ranked": [
            {"path": "src/auth.py", "concern": "auth", "reason": "session handling"},
            {"path": "does/not/exist.py", "concern": "injection", "reason": "garbage"},
        ]
    })
    adapter = ScriptedAdapter([LLMResponse(text=payload, usage=LLMUsage())])
    ctx = _ctx_with_inventory(files, llm_adapter=adapter)

    result = RankStage().run(ctx)
    assert len(result.ranked) == 1
    assert result.ranked[0] == RankedFile(
        path="src/auth.py", concern="auth", reason="session handling"
    )


def test_rank_normalizes_unknown_concern_to_other():
    files = [FileEntry("src/x.py", 100, ".py", True)]
    payload = json.dumps({
        "ranked": [{"path": "src/x.py", "concern": "blockchain_voodoo", "reason": "x"}]
    })
    adapter = ScriptedAdapter([LLMResponse(text=payload, usage=LLMUsage())])
    ctx = _ctx_with_inventory(files, llm_adapter=adapter)

    result = RankStage().run(ctx)
    assert result.ranked[0].concern == "other"


def test_rank_handles_fenced_json():
    files = [FileEntry("a.py", 100, ".py", True)]
    payload = "```json\n" + json.dumps({"ranked": [
        {"path": "a.py", "concern": "auth", "reason": "x"}
    ]}) + "\n```"
    adapter = ScriptedAdapter([LLMResponse(text=payload, usage=LLMUsage())])
    ctx = _ctx_with_inventory(files, llm_adapter=adapter)
    result = RankStage().run(ctx)
    assert len(result.ranked) == 1


def test_rank_skipped_when_inventory_empty():
    adapter = ScriptedAdapter([])
    ctx = _ctx_with_inventory([], llm_adapter=adapter)
    with pytest.raises(StageSkipped):
        RankStage().run(ctx)


def test_rank_respects_top_n():
    files = [FileEntry(f"f{i}.py", 100, ".py", True) for i in range(5)]
    payload = json.dumps({
        "ranked": [{"path": f"f{i}.py", "concern": "auth", "reason": "x"} for i in range(5)]
    })
    adapter = ScriptedAdapter([LLMResponse(text=payload, usage=LLMUsage())])
    ctx = _ctx_with_inventory(files, llm_adapter=adapter, top_n=2)
    result = RankStage().run(ctx)
    assert len(result.ranked) == 2


def test_rank_tolerates_bad_json_payload():
    files = [FileEntry("a.py", 100, ".py", True)]
    adapter = ScriptedAdapter([LLMResponse(text="this is not json", usage=LLMUsage())])
    ctx = _ctx_with_inventory(files, llm_adapter=adapter)
    result = RankStage().run(ctx)
    assert result.ranked == []
