"""Trading-calendar construction used by the H1 pipeline."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

SESSION_TIMEZONES = {
    "sydney": ZoneInfo("Australia/Sydney"),
    "tokyo": ZoneInfo("Asia/Tokyo"),
    "london": ZoneInfo("Europe/London"),
    "new_york": ZoneInfo("America/New_York"),
}


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def london_holidays(start_year: int, end_year: int) -> dict[date, str]:
    """Build the holiday labels used by the original DataOne notebooks."""

    result: dict[date, str] = {}
    for year in range(start_year, end_year + 1):
        easter = _easter_sunday(year)
        result[easter - timedelta(days=2)] = "Good Friday"
        result[easter + timedelta(days=1)] = "Easter Monday"
        result[date(year, 1, 1)] = "New Year's Day"
        result[date(year, 12, 25)] = "Christmas Day"
        result[date(year, 12, 26)] = "Boxing Day"
    return result


def build_week_calendar(
    start: pd.Timestamp, end: pd.Timestamp, *, days: int = 5
) -> pd.DataFrame:
    start = pd.Timestamp(start).tz_convert("UTC").normalize()
    end = pd.Timestamp(end).tz_convert("UTC").normalize()
    while start.day_name() != "Sunday":
        start += pd.Timedelta(days=1)
    while end.day_name() != "Friday":
        end -= pd.Timedelta(days=1)

    columns = [
        "index",
        "start_date",
        "end_date",
        "start_day_name",
        "end_day_name",
        "wid",
        "week_number",
        "year",
        "month",
        "quarter",
    ]
    if start > end:
        return pd.DataFrame(columns=columns)

    starts = pd.date_range(start=start, end=end, freq="7D")
    frame = pd.DataFrame({"start_date": starts})
    frame.insert(0, "index", range(len(frame)))
    frame["end_date"] = frame["start_date"] + pd.Timedelta(days=days)
    frame["start_day_name"] = frame["start_date"].dt.day_name()
    frame["end_day_name"] = frame["end_date"].dt.day_name()
    frame["wid"] = range(len(frame))
    frame["week_number"] = frame["end_date"].dt.isocalendar().week
    frame["year"] = frame["end_date"].dt.year
    frame["month"] = frame["end_date"].dt.month
    frame["quarter"] = frame["end_date"].dt.quarter
    return frame.loc[:, columns]


def build_day_calendar(weeks: pd.DataFrame, *, days: int = 5) -> pd.DataFrame:
    columns = [
        "wid",
        "dsid",
        "day_start",
        "day_end",
        "day_start_name",
        "day_end_name",
        "is_holiday",
        "holiday_name",
    ]
    if weeks.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    holidays = london_holidays(
        int(weeks["year"].min()) - 1, int(weeks["year"].max()) + 1
    )
    for week in weeks.itertuples(index=False):
        for offset in range(days):
            day_start = week.start_date + pd.Timedelta(days=offset)
            day_end = day_start + pd.Timedelta(days=1)
            holiday_name = holidays.get(day_end.date())
            rows.append(
                {
                    "wid": week.wid,
                    "dsid": len(rows),
                    "day_start": day_start,
                    "day_end": day_end,
                    "day_start_name": day_start.day_name(),
                    "day_end_name": day_end.day_name(),
                    "is_holiday": holiday_name is not None,
                    "holiday_name": holiday_name,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _session_columns(hours: pd.DatetimeIndex) -> pd.DataFrame:
    frame = pd.DataFrame({"utc_date": hours})
    sydney = hours.tz_convert(SESSION_TIMEZONES["sydney"])
    tokyo = hours.tz_convert(SESSION_TIMEZONES["tokyo"])
    london = hours.tz_convert(SESSION_TIMEZONES["london"])
    new_york = hours.tz_convert(SESSION_TIMEZONES["new_york"])

    frame["utc_day_name"] = hours.day_name()
    frame["sydney_local_date"] = sydney
    frame["sydney_dst"] = [bool(value.dst()) for value in sydney]
    frame["sydney_dst_abbr"] = [value.tzname() for value in sydney]
    frame["is_sydney_session"] = np.where(
        frame["sydney_dst_abbr"].eq("AEST"),
        (sydney.hour >= 7) & (sydney.hour < 16),
        (sydney.hour >= 8) & (sydney.hour < 17),
    )
    frame["tokyo_local_date"] = tokyo
    frame["is_tokyo_session"] = (tokyo.hour >= 9) & (tokyo.hour < 18)
    frame["london_local_date"] = london
    frame["london_dst"] = [bool(value.dst()) for value in london]
    frame["is_london_session"] = (london.hour >= 8) & (london.hour < 16)
    frame["new_york_local_date"] = new_york
    frame["new_york_dst"] = [bool(value.dst()) for value in new_york]
    frame["is_new_york_session"] = (new_york.hour >= 8) & (new_york.hour < 17)
    return frame


def build_hour_calendar(days: pd.DataFrame, *, market: str = "fx") -> pd.DataFrame:
    """Build the same session-trimmed expected hours as the legacy workflow."""

    columns = [
        "wid",
        "dsid",
        "hsid",
        "utc_date",
        "utc_day_name",
        "sydney_local_date",
        "sydney_dst",
        "sydney_dst_abbr",
        "is_sydney_session",
        "tokyo_local_date",
        "is_tokyo_session",
        "london_local_date",
        "london_dst",
        "is_london_session",
        "new_york_local_date",
        "new_york_dst",
        "is_new_york_session",
    ]
    if days.empty:
        return pd.DataFrame(columns=columns)

    groups: list[pd.DataFrame] = []
    for day in days.itertuples(index=False):
        hours = pd.date_range(
            day.day_start, day.day_end + pd.Timedelta(hours=23), freq="h"
        )
        candidate = _session_columns(hours)
        candidate.insert(0, "dsid", day.dsid)
        candidate.insert(0, "wid", day.wid)

        start_mask = (
            candidate["utc_date"].dt.date.eq(day.day_start.date())
            & candidate["is_sydney_session"]
            & candidate["utc_date"].dt.hour.ge(19)
            & ~candidate["is_new_york_session"]
        )
        end_mask = candidate["utc_date"].dt.date.eq(day.day_end.date()) & candidate[
            "new_york_local_date"
        ].dt.hour.eq(16)
        starts = np.flatnonzero(start_mask.to_numpy())
        ends = np.flatnonzero(end_mask.to_numpy())
        if len(starts) and len(ends):
            selected = candidate.iloc[starts[0] : ends[0] + 1]
            if market == "metals":
                selected = selected[selected["new_york_local_date"].dt.hour.ne(17)]
            groups.append(selected)

    if not groups:
        return pd.DataFrame(columns=columns)
    result = pd.concat(groups, ignore_index=True)
    result.insert(2, "hsid", range(len(result)))
    return result.loc[:, columns]


def build_continuous_hour_calendar(
    start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Build a 24/7 hourly calendar for crypto and generic instruments."""

    hours = pd.date_range(start.floor("h"), end.floor("h"), freq="h")
    result = _session_columns(hours)
    sunday = result["utc_date"] - pd.to_timedelta(
        (result["utc_date"].dt.dayofweek + 1) % 7, unit="D"
    )
    week_codes, _ = pd.factorize(sunday.dt.normalize())
    day_codes, _ = pd.factorize(result["utc_date"].dt.normalize())
    result.insert(0, "wid", week_codes)
    result.insert(1, "dsid", day_codes)
    result.insert(2, "hsid", range(len(result)))
    return result
