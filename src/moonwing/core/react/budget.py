"""Budget enforcement for ReAct loops.

Hard caps in three dimensions:

  * ``max_iterations`` — number of LLM round-trips
  * ``max_total_input_tokens`` — sum of prompt tokens across iterations
  * ``max_wall_seconds`` — wall-clock budget for the whole run

Any cap can be left as ``None`` to disable that dimension. The agent
calls ``advance()`` at the top of each iteration; if any cap is reached,
``BudgetExceededError`` is raised so the agent can return a partial
transcript with a clear stop reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceededError(RuntimeError):
    def __init__(self, dimension: str, limit: float, used: float) -> None:
        super().__init__(f"budget exhausted: {dimension} {used} >= {limit}")
        self.dimension = dimension
        self.limit = limit
        self.used = used


@dataclass
class Budget:
    """Static caps + monotonic counters.

    Defaults are tuned for a moderate network scan: a dozen LLM hops,
    a couple hundred thousand prompt tokens, and a 10-minute wall clock.
    """

    max_iterations: int = 12
    max_total_input_tokens: int | None = 200_000
    max_wall_seconds: float | None = 600.0
    started_at: float = field(default_factory=time.monotonic)

    iterations_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0

    def advance(self) -> None:
        """Call before each new LLM round-trip."""
        if self.max_iterations is not None and self.iterations_used >= self.max_iterations:
            raise BudgetExceededError(
                "iterations", self.max_iterations, self.iterations_used
            )
        if self.max_wall_seconds is not None:
            elapsed = time.monotonic() - self.started_at
            if elapsed >= self.max_wall_seconds:
                raise BudgetExceededError(
                    "wall_seconds", self.max_wall_seconds, elapsed
                )
        if (
            self.max_total_input_tokens is not None
            and self.input_tokens_used >= self.max_total_input_tokens
        ):
            raise BudgetExceededError(
                "input_tokens", self.max_total_input_tokens, self.input_tokens_used
            )
        self.iterations_used += 1

    def record_usage(self, *, input_tokens: int | None, output_tokens: int | None) -> None:
        if input_tokens:
            self.input_tokens_used += int(input_tokens)
        if output_tokens:
            self.output_tokens_used += int(output_tokens)

    def state(self) -> "BudgetState":
        return BudgetState(
            iterations_used=self.iterations_used,
            input_tokens_used=self.input_tokens_used,
            output_tokens_used=self.output_tokens_used,
            wall_seconds_elapsed=time.monotonic() - self.started_at,
        )


@dataclass(frozen=True)
class BudgetState:
    iterations_used: int
    input_tokens_used: int
    output_tokens_used: int
    wall_seconds_elapsed: float
