#!/usr/bin/env python3
"""CLI wrapper for the LLM provider doctor.

Usage:
    moonwing-doctor.py --provider openai --model gpt-5.5 --api-key $KEY
    moonwing-doctor.py --provider anthropic --model claude-sonnet-4-6 \\
                       --api-key $KEY --json

Exit code is 0 only when reachable + auth_ok + (tool_use_ok if model given).
"""

from __future__ import annotations

import argparse
import json
import sys
from textwrap import indent

from moonwing.services.llm_doctor import check_provider


def _format_human(health) -> str:
    bits = [
        f"provider:       {health.provider}",
        f"model:          {health.model or '(not specified)'}",
        f"reachable:      {health.reachable}",
        f"auth_ok:        {health.auth_ok}",
        f"tool_use_ok:    {health.tool_use_ok}",
    ]
    if health.models_listed is not None:
        bits.append(f"models_listed:  {health.models_listed}")
    if health.error:
        bits.append(f"error:          {health.error}")
    if health.detail:
        bits.append("detail:\n" + indent(json.dumps(health.detail, indent=2), "  "))
    return "\n".join(bits)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Moonwing LLM provider health check")
    p.add_argument(
        "--provider", required=True,
        choices=("anthropic", "openai", "openrouter", "ollama", "google"),
    )
    p.add_argument("--api-key", required=True)
    p.add_argument("--model")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)

    health = check_provider(
        provider=args.provider,
        api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(health.to_dict(), indent=2, default=str))
    else:
        print(_format_human(health))

    healthy = health.reachable and health.auth_ok and (not args.model or health.tool_use_ok)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
