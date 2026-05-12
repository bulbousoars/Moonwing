from datetime import datetime, timezone

import pytest

from moonwing.services.run_schedule_service import (
    compute_next_run_utc,
    validate_cron_expression,
    validate_timezone,
)


def test_validate_cron_five_fields():
    assert validate_cron_expression("0 * * * *") == "0 * * * *"


def test_validate_cron_rejects_wrong_field_count():
    with pytest.raises(ValueError, match="5 fields"):
        validate_cron_expression("0 * * *")


def test_validate_timezone():
    assert validate_timezone("UTC") == "UTC"
    assert validate_timezone("  America/New_York ") == "America/New_York"


def test_validate_timezone_unknown():
    with pytest.raises(ValueError, match="unknown timezone"):
        validate_timezone("Not/A_Real_Zone")


def test_compute_next_run_utc_hourly():
    anchor = datetime(2026, 5, 12, 10, 15, tzinfo=timezone.utc)
    nxt = compute_next_run_utc(cron_expression="0 * * * *", timezone_name="UTC", anchor_utc=anchor)
    assert nxt > anchor
    assert nxt.hour == 11
    assert nxt.minute == 0
