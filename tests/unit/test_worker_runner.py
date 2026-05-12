import pytest

from moonwing.worker.clearwing_runner import ClearwingCommandError, append_operator_ai_instruction, build_clearwing_command


def test_append_operator_ai_instruction_truncates():
    long = "x" * 100
    out = append_operator_ai_instruction("base", long, max_chars=5)
    assert "xxxxx" in out
    assert "…(truncated)" in out


def test_build_command_includes_ai_instruction_in_prompt():
    command = build_clearwing_command(
        job_family="source_hunt",
        input_kind="repo",
        source_ref="https://github.com/example/repo.git",
        provider="anthropic",
        model="claude-sonnet-4-6",
        ai_instruction="Focus on OIDC callbacks.",
    )
    prompt = command[command.index("-p") + 1]
    assert "OIDC" in prompt
    assert "Additional instructions from the operator" in prompt


def test_build_command_for_anthropic_network_scan():
    command = build_clearwing_command(
        job_family="network_scan",
        input_kind="repo",
        source_ref="192.0.2.15",
        provider="anthropic",
        model="claude-sonnet-4-6",
        nmap_output="Nmap scan report for 192.0.2.15\n22/tcp open ssh",
    )

    assert command[0] == "claude"
    assert "--model" in command
    assert "claude-sonnet-4-6" in command
    assert "--print" in command
    assert "nmap" in command[-1].lower()  # prompt contains nmap analysis instructions


def test_build_command_for_openai_source_hunt():
    command = build_clearwing_command(
        job_family="source_hunt",
        input_kind="repo",
        source_ref="https://github.com/example/repo.git",
        provider="openai",
        model="gpt-4o",
    )

    assert command[0] == "codex"
    # codex uses -m for model selection
    assert "-m" in command
    assert "gpt-4o" in command
    assert "https://github.com/example/repo.git" in command[-1]


def test_build_command_for_ollama():
    command = build_clearwing_command(
        job_family="network_scan",
        input_kind="repo",
        source_ref="10.0.0.1",
        provider="ollama",
        model="llama3.1",
    )

    assert command[0] == "codex"
    # codex routes ollama via --local-provider
    assert "--local-provider" in command
    assert "ollama" in command


def test_build_command_legacy_clearwing_binary():
    command = build_clearwing_command(
        job_family="source_hunt",
        input_kind="repo",
        source_ref="https://github.com/example/repo.git",
        clearwing_binary="/opt/clearwing/bin/clearwing",
    )

    assert command == ["/opt/clearwing/bin/clearwing", "sourcehunt", "https://github.com/example/repo.git"]


def test_build_command_legacy_network_scan():
    command = build_clearwing_command(
        job_family="network_scan",
        input_kind="repo",
        source_ref="192.0.2.15",
        clearwing_binary="/opt/clearwing/bin/clearwing",
    )

    assert command == ["/opt/clearwing/bin/clearwing", "scan", "192.0.2.15"]


def test_build_command_rejects_invalid_inputs():
    with pytest.raises(ClearwingCommandError):
        build_clearwing_command(
            job_family="bad_job",
            input_kind="repo",
            source_ref="https://github.com/example/repo.git",
        )

    with pytest.raises(ClearwingCommandError):
        build_clearwing_command(
            job_family="source_hunt",
            input_kind="bad_input",
            source_ref="https://github.com/example/repo.git",
        )

    with pytest.raises(ClearwingCommandError):
        build_clearwing_command(
            job_family="source_hunt",
            input_kind="repo",
            source_ref="",
        )
