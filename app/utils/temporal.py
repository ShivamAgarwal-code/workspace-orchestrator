"""Timezone-aware resolution of natural-language date phrases ("next week", "next Tuesday", ...)
into absolute UTC datetime ranges.

The intent classifier deliberately does NOT resolve dates itself (see intent/prompts.py) — it
extracts the phrase verbatim, and this module resolves it against the user's actual timezone and
"now", so the same phrase means the right thing regardless of when/where the query is asked.

Convention for "next <weekday>": the closest *future* occurrence, non-inclusive of today (so if
today is Tuesday, "next Tuesday" means 7 days from now, not today) — this matches how most people
use the phrase in a scheduling context, though it is a documented, debatable convention.
"""
import re
from datetime import date, datetime, time, timedelta

from zoneinfo import ZoneInfo

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def resolve_date_phrase(phrase: str | None, now_utc: datetime, tz_name: str = "UTC") -> tuple[datetime | None, datetime | None]:
    """Returns (since, until) in UTC, both tz-aware, or (None, None) if unresolvable."""
    if not phrase:
        return None, None

    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    phrase = phrase.lower().strip()

    def day_bounds(d: date) -> tuple[datetime, datetime]:
        start = datetime.combine(d, time.min, tzinfo=tz)
        end = datetime.combine(d, time.max, tzinfo=tz)
        return start.astimezone(now_utc.tzinfo), end.astimezone(now_utc.tzinfo)

    def week_bounds(monday: date) -> tuple[datetime, datetime]:
        s, _ = day_bounds(monday)
        _, e = day_bounds(monday + timedelta(days=6))
        return s, e

    def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
        first = date(year, month, 1)
        next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        s, _ = day_bounds(first)
        _, e = day_bounds(next_first - timedelta(days=1))
        return s, e

    if phrase in ("this month", "current month"):
        return month_bounds(local_now.year, local_now.month)
    if phrase in ("last month", "previous month"):
        y, m = (local_now.year - 1, 12) if local_now.month == 1 else (local_now.year, local_now.month - 1)
        return month_bounds(y, m)
    if phrase == "next month":
        y, m = (local_now.year + 1, 1) if local_now.month == 12 else (local_now.year, local_now.month + 1)
        return month_bounds(y, m)

    if phrase == "today":
        return day_bounds(local_now.date())
    if phrase == "tomorrow":
        return day_bounds(local_now.date() + timedelta(days=1))
    if phrase == "yesterday":
        return day_bounds(local_now.date() - timedelta(days=1))

    this_monday = local_now.date() - timedelta(days=local_now.weekday())
    if phrase in ("this week", "current week"):
        return week_bounds(this_monday)
    if phrase == "next week":
        return week_bounds(this_monday + timedelta(days=7))
    if phrase == "last week":
        return week_bounds(this_monday - timedelta(days=7))

    match = re.match(r"next (\w+day)\b", phrase)
    weekday_name = match.group(1) if match else (phrase if phrase in _WEEKDAYS else None)
    if weekday_name in _WEEKDAYS:
        target = _WEEKDAYS[weekday_name]
        days_ahead = (target - local_now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return day_bounds(local_now.date() + timedelta(days=days_ahead))

    return None, None
