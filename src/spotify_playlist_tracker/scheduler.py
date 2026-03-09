from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter

from .models import utc_now


PRESET_SCHEDULES = {
    "hourly": "0 * * * *",
    "daily": "0 0 * * *",
    "weekly": "0 0 * * 0",
    "monthly": "0 0 1 * *",
}


class ScheduleError(RuntimeError):
    pass


def normalize_schedule(schedule: str) -> str:
    normalized = schedule.strip().lower()
    return PRESET_SCHEDULES.get(normalized, schedule.strip())


def validate_schedule(schedule: str) -> str:
    normalized = normalize_schedule(schedule)
    try:
        croniter(normalized, utc_now())
    except (ValueError, KeyError) as error:
        raise ScheduleError(
            "TRACKER_SCHEDULE must be a supported preset like daily/hourly or a valid 5-field cron expression."
        ) from error
    return normalized


def next_run_after(schedule: str, reference: datetime | None = None) -> datetime:
    base_time = reference or utc_now()
    normalized = validate_schedule(schedule)
    next_value = croniter(normalized, base_time).get_next(datetime)
    if next_value.tzinfo is None:
        next_value = next_value.replace(tzinfo=timezone.utc)
    return next_value.astimezone(timezone.utc)