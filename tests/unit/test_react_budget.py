from __future__ import annotations

import time

import pytest

from moonwing.core.react.budget import Budget, BudgetExceededError


def test_advance_counts_iterations_and_caps():
    b = Budget(max_iterations=3, max_total_input_tokens=None, max_wall_seconds=None)
    for i in range(3):
        b.advance()
        assert b.iterations_used == i + 1
    with pytest.raises(BudgetExceededError) as info:
        b.advance()
    assert info.value.dimension == "iterations"


def test_record_usage_accumulates_tokens():
    b = Budget(max_iterations=10, max_total_input_tokens=100, max_wall_seconds=None)
    b.advance()
    b.record_usage(input_tokens=40, output_tokens=20)
    b.advance()
    b.record_usage(input_tokens=70, output_tokens=10)
    with pytest.raises(BudgetExceededError) as info:
        b.advance()
    assert info.value.dimension == "input_tokens"
    assert b.input_tokens_used == 110


def test_wall_seconds_cap():
    b = Budget(max_iterations=100, max_total_input_tokens=None, max_wall_seconds=0.01)
    b.advance()
    time.sleep(0.02)
    with pytest.raises(BudgetExceededError) as info:
        b.advance()
    assert info.value.dimension == "wall_seconds"


def test_none_caps_disable_dimension():
    b = Budget(max_iterations=None, max_total_input_tokens=None, max_wall_seconds=None)
    for _ in range(50):
        b.advance()
    # Should not raise — every cap is None.
    assert b.iterations_used == 50


def test_state_snapshot():
    b = Budget(max_iterations=5)
    b.advance()
    b.record_usage(input_tokens=10, output_tokens=20)
    snap = b.state()
    assert snap.iterations_used == 1
    assert snap.input_tokens_used == 10
    assert snap.output_tokens_used == 20
    assert snap.wall_seconds_elapsed >= 0
