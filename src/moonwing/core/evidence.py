"""Evidence ladder — confidence/validation state machine for findings.

A finding moves up the ladder as evidence strengthens:

    suspicion              — AI flagged a potential issue, no corroboration
    static_corroborated    — independent static analysis (semgrep, taint, etc.) agrees
    reproduced             — dynamic reproduction (fuzzer crash, probe response, etc.)
    root_cause             — explained mechanism, not just observed symptom
    exploit_demonstrated   — working PoC against the target
    patch_validated        — fix applied and verified against the same reproducer

Plus a terminal ``rejected`` state for false positives.

The ladder is intentionally separate from ``severity`` (impact estimate) and
``status`` (workflow: open/closed/remediated). A critical-severity finding can
still sit at ``suspicion`` until corroborated; a low-severity finding can ride
the ladder all the way to ``patch_validated`` if someone takes it seriously.
"""

from __future__ import annotations

from enum import StrEnum


class EvidenceLevel(StrEnum):
    SUSPICION = "suspicion"
    STATIC_CORROBORATED = "static_corroborated"
    REPRODUCED = "reproduced"
    ROOT_CAUSE = "root_cause"
    EXPLOIT_DEMONSTRATED = "exploit_demonstrated"
    PATCH_VALIDATED = "patch_validated"
    REJECTED = "rejected"


_LADDER_ORDER: tuple[EvidenceLevel, ...] = (
    EvidenceLevel.SUSPICION,
    EvidenceLevel.STATIC_CORROBORATED,
    EvidenceLevel.REPRODUCED,
    EvidenceLevel.ROOT_CAUSE,
    EvidenceLevel.EXPLOIT_DEMONSTRATED,
    EvidenceLevel.PATCH_VALIDATED,
)

_TERMINAL: frozenset[EvidenceLevel] = frozenset(
    {EvidenceLevel.PATCH_VALIDATED, EvidenceLevel.REJECTED}
)


class EvidenceTransitionError(ValueError):
    """Raised when a requested ladder transition is not allowed."""


def rank(level: EvidenceLevel) -> int:
    """Numeric ladder position. ``REJECTED`` returns -1 (off-ladder)."""
    if level == EvidenceLevel.REJECTED:
        return -1
    return _LADDER_ORDER.index(level)


def is_terminal(level: EvidenceLevel) -> bool:
    return level in _TERMINAL


def can_transition(current: EvidenceLevel, target: EvidenceLevel) -> bool:
    """Return True iff ``current → target`` is an allowed transition.

    Rules:
      * Terminal states (``patch_validated``, ``rejected``) admit no outbound moves.
      * Any non-terminal state may transition to ``rejected``.
      * Otherwise, only strictly-higher rungs are allowed (forward-only,
        but skipping rungs is permitted — e.g. an AI fuzzing pass can jump
        ``suspicion → reproduced`` in one step).
    """
    if current == target:
        return False
    if is_terminal(current):
        return False
    if target == EvidenceLevel.REJECTED:
        return True
    return rank(target) > rank(current)


def assert_transition(current: EvidenceLevel, target: EvidenceLevel) -> None:
    """Raise ``EvidenceTransitionError`` if the transition is not allowed."""
    if not can_transition(current, target):
        raise EvidenceTransitionError(
            f"cannot transition evidence_level {current.value!r} -> {target.value!r}"
        )


def parse_level(value: str | EvidenceLevel) -> EvidenceLevel:
    """Coerce a string (e.g. from JSON/form data) into ``EvidenceLevel``."""
    if isinstance(value, EvidenceLevel):
        return value
    try:
        return EvidenceLevel(value)
    except ValueError as exc:
        raise EvidenceTransitionError(f"unknown evidence_level: {value!r}") from exc
