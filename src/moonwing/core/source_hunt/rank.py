"""Stage 3 — rank files by security-interest using one LLM call.

The rank stage compresses the inventory into a digestible file list,
asks the LLM to pick the top-N files most worth a deep look, and labels
each pick with a concern tag (auth, injection, deserialization, …).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from moonwing.core.llm import LLMAdapter, LLMError
from moonwing.core.pipeline import StageContext, StageSkipped
from .inventory import InventoryResult

logger = logging.getLogger("moonwing.source_hunt.rank")


_SYSTEM_PROMPT = """\
You are triaging a code repository for security review. You will be given
a flat list of file paths and sizes. Pick the files most likely to contain
security-relevant logic — auth, authn/authz, input parsing, deserialization,
crypto, secrets, network I/O, command execution, file I/O with untrusted
input, multi-tenant isolation. For each pick, assign one concern label
from this set:

  auth, authz, deserialization, injection, secrets, ssrf, path_traversal,
  command_exec, crypto, supply_chain, multitenant_isolation,
  insecure_default, other

Respond with strict JSON only, no prose:

{"ranked": [
  {"path": "<relative path>", "concern": "<label>", "reason": "<one short sentence>"},
  ...
]}
"""


_USER_TEMPLATE = """\
Repository root: {root}
Total files (after default exclusions): {file_count}

File list (relpath, size_bytes):
{file_listing}

Pick the {top_n} most security-relevant files. Bias toward source code
files. Ignore tests, fixtures, generated code, docs, and config unless
they look like a likely exposure surface.
"""


@dataclass
class RankedFile:
    path: str
    concern: str
    reason: str


@dataclass
class RankResult:
    ranked: list[RankedFile] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


_VALID_CONCERNS = frozenset(
    {
        "auth", "authz", "deserialization", "injection", "secrets", "ssrf",
        "path_traversal", "command_exec", "crypto", "supply_chain",
        "multitenant_isolation", "insecure_default", "other",
    }
)


def _format_listing(files, max_files: int = 600) -> str:
    """Produce a compact line-per-file table for the prompt."""
    rows: list[str] = []
    for entry in files[:max_files]:
        rows.append(f"{entry.relpath}\t{entry.size_bytes}")
    if len(files) > max_files:
        rows.append(f"... and {len(files) - max_files} more files truncated ...")
    return "\n".join(rows)


def _parse_ranked_json(text: str) -> dict[str, Any]:
    """Tolerant JSON parser — handles fenced code and stray prose."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            head, rest = cleaned.split("\n", 1)
            if head.strip().lower() in ("json", "json5"):
                cleaned = rest
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        cleaned = cleaned[first : last + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"ranked": []}


@dataclass
class RankStage:
    """LLM-driven file ranker.

    Reads dependencies from ``ctx.extras``:
      * ``llm_adapter`` (required) — provides chat_with_tools
      * ``llm_model`` (required)
      * ``llm_api_key`` (required)
      * ``top_n`` (optional, default 12)
      * ``llm_timeout`` (optional, default 120)
    """

    name: str = "rank"

    def run(self, ctx: StageContext) -> RankResult:
        inventory: InventoryResult | None = ctx.get("inventory")
        if inventory is None:
            raise RuntimeError("rank stage requires 'inventory' output in context")
        if not inventory.files:
            raise StageSkipped("inventory is empty — nothing to rank")

        adapter: LLMAdapter | None = ctx.extras.get("llm_adapter")
        model = ctx.extras.get("llm_model")
        api_key = ctx.extras.get("llm_api_key")
        if adapter is None or not model:
            raise RuntimeError("rank stage requires llm_adapter and llm_model in extras")

        top_n = int(ctx.extras.get("top_n") or 12)
        user_prompt = _USER_TEMPLATE.format(
            root=inventory.root,
            file_count=len(inventory.files),
            file_listing=_format_listing(inventory.files),
            top_n=top_n,
        )
        timeout = int(ctx.extras.get("llm_timeout") or 120)

        try:
            response = adapter.chat_with_tools(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                tools=None,
                model=model,
                api_key=api_key or "",
                temperature=0.1,
                max_tokens=4096,
                timeout=timeout,
                response_format="json_object",
            )
        except LLMError as exc:
            raise RuntimeError(f"LLM ranking call failed: {exc}") from exc

        parsed = _parse_ranked_json(response.text)
        raw_list = parsed.get("ranked") if isinstance(parsed, dict) else None
        if not isinstance(raw_list, list):
            raw_list = []

        valid_paths = {f.relpath for f in inventory.files}
        ranked: list[RankedFile] = []
        for item in raw_list[:top_n]:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not isinstance(path, str) or path not in valid_paths:
                logger.info("dropping hallucinated rank entry: %r", path)
                continue
            concern = item.get("concern", "other")
            if concern not in _VALID_CONCERNS:
                concern = "other"
            reason = str(item.get("reason", ""))[:300]
            ranked.append(RankedFile(path=path, concern=concern, reason=reason))

        usage_raw = response.usage.raw if response.usage else {}
        return RankResult(ranked=ranked, raw=parsed, usage=usage_raw or {})
