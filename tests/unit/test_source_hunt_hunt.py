from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from moonwing.core.llm import LLMResponse, LLMUsage
from moonwing.core.pipeline import StageContext, StageSkipped
from moonwing.core.source_hunt.acquire import AcquireResult
from moonwing.core.source_hunt.hunt import HuntStage
from moonwing.core.source_hunt.rank import RankResult, RankedFile


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


def _hunt_ctx(tmp_path: Path, *, adapter, ranked, **extras) -> StageContext:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(): pass\n")

    ctx = StageContext(
        run_id=None,
        workdir=str(tmp_path),
        extras={
            "llm_adapter": adapter,
            "llm_model": "m",
            "llm_api_key": "k",
            **extras,
        },
    )
    ctx.put(
        "acquire",
        AcquireResult(kind="path", source_ref=str(tmp_path), root=str(tmp_path), notes=[]),
    )
    ctx.put("rank", RankResult(ranked=ranked))
    return ctx


def test_hunt_skipped_when_no_ranked_files(tmp_path):
    adapter = ScriptedAdapter([])
    ctx = _hunt_ctx(tmp_path, adapter=adapter, ranked=[])
    with pytest.raises(StageSkipped):
        HuntStage().run(ctx)


def test_hunt_runs_per_file_and_merges_findings(tmp_path):
    ranked = [
        RankedFile(path="src/auth.py", concern="auth", reason="login"),
        RankedFile(path="src/config.py", concern="secrets", reason="key"),
    ]
    f1 = {"title": "missing csrf", "severity": "medium", "evidence": ["src/auth.py:1"]}
    f2 = {"title": "hardcoded api key", "severity": "high", "evidence": ["src/config.py:1"]}
    adapter = ScriptedAdapter(
        [
            LLMResponse(text=json.dumps({"findings": [f1]}), usage=LLMUsage(input_tokens=10, output_tokens=5)),
            LLMResponse(text=json.dumps({"findings": [f2]}), usage=LLMUsage(input_tokens=10, output_tokens=5)),
        ]
    )
    ctx = _hunt_ctx(tmp_path, adapter=adapter, ranked=ranked)
    (tmp_path / "src" / "config.py").write_text("API_KEY = 'sk-x'\n")

    result = HuntStage().run(ctx)
    assert len(result.per_file) == 2
    assert len(result.merged_findings) == 2

    # Findings annotated with source path and concern.
    assert {f["source_file"] for f in result.merged_findings} == {"src/auth.py", "src/config.py"}
    assert {f["concern"] for f in result.merged_findings} == {"auth", "secrets"}


def test_hunt_event_hook_fires_per_file(tmp_path):
    ranked = [RankedFile(path="src/auth.py", concern="auth", reason="x")]
    adapter = ScriptedAdapter([LLMResponse(text=json.dumps({"findings": []}), usage=LLMUsage())])

    events: list[tuple[str, dict]] = []
    ctx = _hunt_ctx(
        tmp_path, adapter=adapter, ranked=ranked,
        on_hunter_event=lambda kind, payload: events.append((kind, payload)),
    )
    HuntStage().run(ctx)

    kinds = [k for k, _ in events]
    assert kinds == ["file_start", "file_end"]
    end_payload = events[1][1]
    assert end_payload["ok"] is True
    assert end_payload["findings"] == 0


def test_hunt_max_files_caps_audited_files(tmp_path):
    ranked = [
        RankedFile(path="src/auth.py", concern="auth", reason="x"),
        RankedFile(path="src/config.py", concern="secrets", reason="x"),
    ]
    adapter = ScriptedAdapter(
        [LLMResponse(text=json.dumps({"findings": []}), usage=LLMUsage())]
    )
    ctx = _hunt_ctx(tmp_path, adapter=adapter, ranked=ranked, hunter_max_files=1)
    (tmp_path / "src" / "config.py").write_text("API_KEY = 'sk-x'\n")

    result = HuntStage().run(ctx)
    assert len(result.per_file) == 1
    # Only one adapter call should have happened.
    assert len(adapter.calls) == 1


def test_hunt_only_offers_allowed_tools(tmp_path):
    ranked = [RankedFile(path="src/auth.py", concern="auth", reason="x")]
    adapter = ScriptedAdapter([LLMResponse(text=json.dumps({"findings": []}), usage=LLMUsage())])
    ctx = _hunt_ctx(tmp_path, adapter=adapter, ranked=ranked)
    HuntStage().run(ctx)

    offered = {t["function"]["name"] for t in adapter.calls[0]["tools"]}
    assert offered == {"read_file", "glob_files", "grep_files", "kg_query", "meta_note"}


def test_hunt_returns_partial_when_one_file_errors(tmp_path):
    ranked = [
        RankedFile(path="src/auth.py", concern="auth", reason="x"),
        RankedFile(path="src/config.py", concern="secrets", reason="x"),
    ]
    # First file: valid findings. Second: garbage text -> 0 findings, no crash.
    adapter = ScriptedAdapter(
        [
            LLMResponse(
                text=json.dumps({"findings": [{"title": "ok", "severity": "low"}]}),
                usage=LLMUsage(),
            ),
            LLMResponse(text="not json", usage=LLMUsage()),
        ]
    )
    ctx = _hunt_ctx(tmp_path, adapter=adapter, ranked=ranked)
    (tmp_path / "src" / "config.py").write_text("x = 1\n")

    result = HuntStage().run(ctx)
    assert len(result.merged_findings) == 1
    assert len(result.per_file) == 2
    assert result.per_file[1].findings == []  # second file gave nothing usable
