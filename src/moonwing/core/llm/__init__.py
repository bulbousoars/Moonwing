"""Unified LLM adapter layer.

A single seam — ``LLMAdapter`` — that every Moonwing component (the ReAct
loop, source-hunt pipeline, doctor checks, the existing one-shot scan
executor) calls through. Provider-specific HTTP detail stays here.
"""

from __future__ import annotations

from .base import (
    LLMAdapter,
    LLMError,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
)
from .factory import SUPPORTED_PROVIDERS, get_adapter

__all__ = [
    "LLMAdapter",
    "LLMError",
    "LLMResponse",
    "LLMToolCall",
    "LLMUsage",
    "SUPPORTED_PROVIDERS",
    "get_adapter",
]
