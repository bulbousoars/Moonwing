"""End-to-end pipeline test: walk a tiny real-ish repo and collect findings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from moonwing.core.llm import LLMResponse, LLMUsage
from moonwing.core.pipeline import StageContext, StageOutcome
from moonwing.core.source_hunt import build_source_hunt_pipeline


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


def _seed_repo(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "auth.py").write_text(
        "def login(user, password):\n"
        "    return user == 'admin' and password == 'admin'\n"
    )
    (root / "src" / "api.py").write_text(
        "import requests\n"
        "def fetch(url):\n"
        "    return requests.get(url).text\n"  # ssrf-ish
    )
    (root / "README.md").write_text("Hello\n")


def test_full_source_hunt_pipeline_runs_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    rank_payload = json.dumps({
        "ranked": [
            {"path": "src/auth.py", "concern": "auth", "reason": "credential check"},
            {"path": "src/api.py", "concern": "ssrf", "reason": "user-controlled URL"},
        ]
    })
    finding_auth = {
        "title": "Hardcoded admin/admin credential check",
        "severity": "high",
        "description": "login() admits user=='admin' && password=='admin'",
        "evidence": ["src/auth.py:2"],
    }
    finding_api = {
        "title": "Unrestricted URL fetch (potential SSRF)",
        "severity": "medium",
        "description": "fetch() forwards user-controlled URL",
        "evidence": ["src/api.py:3"],
    }
    adapter = ScriptedAdapter(
        [
            LLMResponse(text=rank_payload, usage=LLMUsage(input_tokens=100, output_tokens=50)),
            LLMResponse(text=json.dumps({"findings": [finding_auth]}), usage=LLMUsage()),
            LLMResponse(text=json.dumps({"findings": [finding_api]}), usage=LLMUsage()),
        ]
    )

    events: list[tuple[str, dict]] = []
    ctx = StageContext(
        run_id=None,
        workdir=str(tmp_path),
        extras={
            "input_kind": "path",
            "source_ref": str(repo),
            "llm_adapter": adapter,
            "llm_model": "m",
            "llm_api_key": "k",
        },
        on_event=lambda kind, payload: events.append((kind, payload)),
    )

    result = build_source_hunt_pipeline().run(ctx)
    assert result.ok
    assert [r.name for r in result.stage_results] == [
        "acquire",
        "inventory",
        "rank",
        "hunt",
    ]
    assert all(r.outcome == StageOutcome.SUCCESS for r in result.stage_results)

    hunt_output = ctx.get("hunt")
    assert hunt_output is not None
    assert len(hunt_output.merged_findings) == 2
    titles = {f["title"] for f in hunt_output.merged_findings}
    assert "Hardcoded admin/admin credential check" in titles
    assert "Unrestricted URL fetch (potential SSRF)" in titles

    # Each finding got its source file + concern stamped on.
    by_file = {f["source_file"]: f for f in hunt_output.merged_findings}
    assert by_file["src/auth.py"]["concern"] == "auth"
    assert by_file["src/api.py"]["concern"] == "ssrf"

    # Adapter was called 3 times: 1 rank + 2 hunt.
    assert len(adapter.calls) == 3
    # First call (rank) has no tools.
    assert adapter.calls[0].get("tools") is None
    # Hunt calls expose only the allowed tool set.
    hunt_tool_names = {t["function"]["name"] for t in adapter.calls[1]["tools"]}
    assert "read_file" in hunt_tool_names
    assert "nmap_scan" not in hunt_tool_names  # network tool not offered to hunter

    # Lifecycle events fired in the right shape.
    kinds = [k for k, _ in events]
    assert kinds.count("stage_start") == 4
    assert kinds.count("stage_end") == 4
