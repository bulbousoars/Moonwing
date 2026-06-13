from __future__ import annotations

import pytest

from moonwing.core.evidence import (
    EvidenceLevel,
    EvidenceTransitionError,
    assert_transition,
    can_transition,
    is_terminal,
    parse_level,
    rank,
)


def test_rank_orders_levels_ascending():
    assert rank(EvidenceLevel.SUSPICION) == 0
    assert rank(EvidenceLevel.STATIC_CORROBORATED) == 1
    assert rank(EvidenceLevel.REPRODUCED) == 2
    assert rank(EvidenceLevel.ROOT_CAUSE) == 3
    assert rank(EvidenceLevel.EXPLOIT_DEMONSTRATED) == 4
    assert rank(EvidenceLevel.PATCH_VALIDATED) == 5


def test_rank_rejected_is_off_ladder():
    assert rank(EvidenceLevel.REJECTED) == -1


def test_terminal_states():
    assert is_terminal(EvidenceLevel.PATCH_VALIDATED)
    assert is_terminal(EvidenceLevel.REJECTED)
    assert not is_terminal(EvidenceLevel.SUSPICION)
    assert not is_terminal(EvidenceLevel.EXPLOIT_DEMONSTRATED)


def test_forward_single_step_allowed():
    assert can_transition(EvidenceLevel.SUSPICION, EvidenceLevel.STATIC_CORROBORATED)


def test_forward_skipping_rungs_allowed():
    # AI fuzzing pass jumps straight to reproduced
    assert can_transition(EvidenceLevel.SUSPICION, EvidenceLevel.REPRODUCED)
    assert can_transition(EvidenceLevel.STATIC_CORROBORATED, EvidenceLevel.EXPLOIT_DEMONSTRATED)


def test_backward_disallowed():
    assert not can_transition(EvidenceLevel.REPRODUCED, EvidenceLevel.STATIC_CORROBORATED)
    assert not can_transition(EvidenceLevel.PATCH_VALIDATED, EvidenceLevel.SUSPICION)


def test_self_transition_disallowed():
    assert not can_transition(EvidenceLevel.REPRODUCED, EvidenceLevel.REPRODUCED)


def test_any_nonterminal_can_be_rejected():
    for level in [
        EvidenceLevel.SUSPICION,
        EvidenceLevel.STATIC_CORROBORATED,
        EvidenceLevel.REPRODUCED,
        EvidenceLevel.ROOT_CAUSE,
        EvidenceLevel.EXPLOIT_DEMONSTRATED,
    ]:
        assert can_transition(level, EvidenceLevel.REJECTED), level


def test_terminal_states_are_truly_terminal():
    for terminal in (EvidenceLevel.PATCH_VALIDATED, EvidenceLevel.REJECTED):
        for target in EvidenceLevel:
            assert not can_transition(terminal, target), (terminal, target)


def test_assert_transition_raises_on_invalid():
    with pytest.raises(EvidenceTransitionError):
        assert_transition(EvidenceLevel.ROOT_CAUSE, EvidenceLevel.SUSPICION)


def test_assert_transition_passes_on_valid():
    # No raise
    assert_transition(EvidenceLevel.SUSPICION, EvidenceLevel.ROOT_CAUSE)


def test_parse_level_accepts_strings():
    assert parse_level("suspicion") == EvidenceLevel.SUSPICION
    assert parse_level("patch_validated") == EvidenceLevel.PATCH_VALIDATED


def test_parse_level_rejects_unknown():
    with pytest.raises(EvidenceTransitionError):
        parse_level("not_a_level")


def test_parse_level_is_idempotent():
    assert parse_level(EvidenceLevel.ROOT_CAUSE) == EvidenceLevel.ROOT_CAUSE
