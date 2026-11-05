"""The US market clock (NYSE hours, New York time), so screens never call yesterday's close "today".

Regular session 09:30-16:00 ET, pre-market from 04:00, after hours until 20:00. Weekends and NYSE
holidays are closed. Half days (day after Thanksgiving, Christmas Eve) close at 13:00 ET."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PRE, OPEN, CLOSE, AFTER_END = dt.time(4, 0), dt.time(9, 30), dt.time(16, 0), dt.time(20, 0)
EARLY_CLOSE = dt.time(13, 0)

HOLIDAYS = {dt.date.fromisoformat(d) for d in [
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
]}
HALF_DAYS = {dt.date.fromisoformat(d) for d in ["2025-07-03", "2025-11-28", "2025-12-24", "2026-11-27", "2026-12-24", "2027-11-26"]}

LABEL = {"pre": "Pre-market", "open": "Open", "after": "After hours", "closed": "Closed"}


def is_session(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def close_time(d: dt.date) -> dt.time:
    return EARLY_CLOSE if d in HALF_DAYS else CLOSE


def last_completed_session(now: dt.datetime) -> dt.date:
    """The latest trading day whose closing bell has rung."""
    et = now.astimezone(ET)
    d = et.date()
    if is_session(d) and et.time() >= close_time(d):
        return d
    d -= dt.timedelta(days=1)
    while not is_session(d):
        d -= dt.timedelta(days=1)
    return d


def status(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    et = now.astimezone(ET)
    d, t = et.date(), et.time()
    if not is_session(d):
        state = "closed"
    elif t < PRE:
        state = "closed"
    elif t < OPEN:
        state = "pre"
    elif t < close_time(d):
        state = "open"
    elif t < AFTER_END:
        state = "after"
    else:
        state = "closed"
    last = last_completed_session(now)
    return {"state": state, "label": LABEL[state], "et_date": d.isoformat(), "last_session": last.isoformat(),
            "last_close_at": dt.datetime.combine(last, close_time(last), ET).astimezone(dt.timezone.utc).isoformat()}


def session_label(bar_date: dt.date | None, now: dt.datetime | None = None) -> dict:
    """How to name the day a "today" figure belongs to.

    A bar dated today's New York date is "Today" while that session runs (live) and in after hours
    (close). Any older bar, or the same bar after 20:00 ET, is the last session."""
    if bar_date is None:
        return {"word": "Last session", "title": "Last session", "live": False}
    st = status(now)
    same_day = bar_date.isoformat() == st["et_date"]
    # "Today" only while that session is running or just ended; by evening in Israel it is the last session
    is_today = same_day and st["state"] in ("pre", "open", "after")
    live = same_day and st["state"] == "open"
    word = "Today" if is_today else "Last session"
    day = f"{bar_date:%a} {bar_date.day} {bar_date:%b}"
    note = "live" if live else "pre-market" if same_day and st["state"] == "pre" else "close"
    return {"word": word, "title": f"{word}, {day} ({note})", "live": live}
