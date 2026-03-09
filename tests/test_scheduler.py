from datetime import datetime, timezone

import pytest

from spotify_playlist_tracker.scheduler import ScheduleError, next_run_after


def test_next_run_after_supports_daily_preset() -> None:
    reference = datetime(2026, 3, 9, 10, 15, tzinfo=timezone.utc)

    next_run = next_run_after("daily", reference)

    assert next_run == datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)


def test_next_run_after_supports_cron_expression() -> None:
    reference = datetime(2026, 3, 9, 10, 15, tzinfo=timezone.utc)

    next_run = next_run_after("30 11 * * *", reference)

    assert next_run == datetime(2026, 3, 9, 11, 30, tzinfo=timezone.utc)


def test_next_run_after_rejects_invalid_schedule() -> None:
    with pytest.raises(ScheduleError):
        next_run_after("not-a-schedule")