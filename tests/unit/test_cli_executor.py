from __future__ import annotations

import json

import pytest

from moonwing.worker.executor import ExecutionError, _parse_payload


def test_parse_payload_uses_nested_response_json_when_top_level_findings_empty():
    nested = {
        "findings": [
            {
                "title": "Code Injection via eval()",
                "severity": "critical",
                "evidence": ["app/routes/contributions.js:32-34"],
            }
        ]
    }
    stdout = json.dumps(
        {
            "findings": [],
            "response": json.dumps(nested),
            "stats": {"models": {"gemini": {"api": {"totalRequests": 1}}}},
        }
    )

    payload = _parse_payload(stdout)

    assert payload["findings"] == nested["findings"]
    assert payload["response"] == json.dumps(nested)


def test_parse_payload_extracts_codex_jsonl_agent_message_text():
    final = {
        "findings": [
            {
                "title": "NoSQL Injection via $where operator",
                "severity": "high",
                "evidence": ["app/data/allocations-dao.js:73"],
            }
        ]
    }
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "agent_message",
                        "text": "I am checking the repository first.",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_1",
                        "type": "agent_message",
                        "text": "```json\n" + json.dumps(final) + "\n```",
                    },
                }
            ),
        ]
    )

    payload = _parse_payload(stdout)

    assert payload["findings"] == final["findings"]


def test_parse_payload_error_retains_stdout_for_diagnostics():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "no json here"}}),
        ]
    )

    with pytest.raises(ExecutionError) as exc_info:
        _parse_payload(stdout)

    assert exc_info.value.stdout == stdout
