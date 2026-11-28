"""Scheduled US macro events, for filters that stand aside around them.

Every event here was on a published schedule weeks or months before it
happened, so a backtest that uses it has no look-ahead and no data lag:
the live system knows the date and time in advance. Unscheduled events (the
emergency FOMC meetings of March 2020) are deliberately left out, since
nobody could have stood aside before them.

- FOMC: the statement day of each scheduled meeting, 14:00 New York time
  (the statement's release time throughout 2020-2027). Source:
  federalreserve.gov/monetarypolicy/fomccalendars.htm and
  fomchistorical2020.htm, fetched 2026-11-26. Notation votes, the
  unscheduled March 2 and 15 2020 meetings and the cancelled March 17-18
  2020 meeting are excluded.
- CPI and the jobs report (Employment Situation): 08:30 New York time, dates
  from FRED's release-dates API (release 10 and 50), which needs a free
  API key (FRED_API_KEY). Without one they're simply unavailable."""

from zoneinfo import ZoneInfo

import pandas as pd
import requests

NEW_YORK = ZoneInfo("America/New_York")

FOMC_STATEMENT_DATES = (
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05",
    "2020-12-16", "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28",
    "2021-09-22", "2021-11-03", "2021-12-15", "2022-01-26", "2022-03-16", "2022-05-04",
    "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14", "2023-02-01",
    "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01",
    "2023-12-13", "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18", "2025-01-29", "2025-03-19", "2025-05-07",
    "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10", "2026-01-28",
    "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
    "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28",
    "2027-09-15", "2027-10-27", "2027-11-17",
)

FRED_RELEASES = {"cpi": 10, "nfp": 50}
FRED_URL = "https://api.stlouisfed.org/fred/release/dates"
RELEASE_TIME = {"fomc": (14, 0), "cpi": (8, 30), "nfp": (8, 30)}
KINDS = ("fomc", "cpi", "nfp")


def _at(dates, kind: str) -> pd.DatetimeIndex:
    hour, minute = RELEASE_TIME[kind]
    local = [pd.Timestamp(d).replace(hour=hour, minute=minute).tz_localize(NEW_YORK) for d in dates]
    return pd.DatetimeIndex(local).tz_convert("UTC")


def fetch_fred_release_dates(kind: str, api_key: str, start: str = "2019-01-01", timeout: int = 30) -> list[str]:
    """Past release dates of CPI or the jobs report, as YYYY-MM-DD."""
    response = requests.get(FRED_URL, params={
        "release_id": FRED_RELEASES[kind], "api_key": api_key, "file_type": "json",
        "realtime_start": start, "include_release_dates_with_no_data": "false", "sort_order": "asc",
        "limit": 10000,
    }, timeout=timeout)
    response.raise_for_status()
    return [row["date"] for row in response.json()["release_dates"]]


def events(kinds=("fomc",), fred_dates: dict[str, list[str]] | None = None) -> pd.DataFrame:
    """Event times (UTC) of the given kinds, sorted, with a `kind` column.
    CPI/NFP need their dates in `fred_dates`."""
    frames = []
    for kind in kinds:
        if kind == "fomc":
            dates = FOMC_STATEMENT_DATES
        elif kind in FRED_RELEASES:
            if not fred_dates or kind not in fred_dates:
                raise ValueError(f"no {kind} release dates: they come from FRED and need FRED_API_KEY")
            dates = fred_dates[kind]
        else:
            raise ValueError(f"unknown event kind {kind!r}; known: {', '.join(KINDS)}")
        frames.append(pd.DataFrame({"kind": kind}, index=_at(dates, kind)))
    if not frames:
        return pd.DataFrame({"kind": []}, index=pd.DatetimeIndex([], tz="UTC"))
    out = pd.concat(frames).sort_index()
    out.index.name = "event_time"
    return out


def event_days(ev: pd.DataFrame) -> set:
    """UTC dates with at least one event."""
    return {t.normalize() for t in ev.index}
