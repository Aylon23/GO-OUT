"""Time-weighted return, XIRR, range selection and shadow portfolios."""
from __future__ import annotations

import bisect
import datetime as dt
import math

import numpy as np
import pandas as pd

RANGES = ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "MAX")


def range_start(code: str, today: dt.date, first: dt.date) -> dt.date:
    code = code.upper()
    if code == "MAX":
        return first
    if code == "YTD":
        return dt.date(today.year - 1, 12, 31)
    months = {"1M": 1, "3M": 3, "1Y": 12, "3Y": 36, "5Y": 60}[code]
    y, m = today.year, today.month - months
    while m <= 0:
        m += 12
        y -= 1
    day = min(today.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.date(y, m, day)


def base_index(calendar: list[dt.date], start: dt.date) -> int:
    """Calendar position used as the range base: last day on or before `start` (or the first day)."""
    i = bisect.bisect_right(calendar, start) - 1
    return max(i, 0)


def end_index(calendar: list[dt.date], end: dt.date | None) -> int:
    if end is None:
        return len(calendar) - 1
    return max(bisect.bisect_right(calendar, end) - 1, 0)


def twr(ret: pd.Series, b: int, e: int) -> float:
    """Chained daily returns over (b, e]."""
    r = ret.iloc[b + 1 : e + 1].to_numpy()
    return float(np.prod(1.0 + r) - 1.0)


def rebased(index_or_price: pd.Series, b: int, e: int) -> pd.Series:
    s = index_or_price.iloc[b : e + 1]
    base = s.iloc[0]
    if base == 0 or not math.isfinite(base):
        return s * np.nan
    return s / base * 100.0


def twr_index(ret: pd.Series) -> pd.Series:
    return (1.0 + ret).cumprod()


def xirr(flows: list[tuple[dt.date, float]], guess: float = 0.1) -> float | None:
    """Annualized money-weighted return. Negative amounts = money put in, positive = money taken out
    (including the final value). Returns None when undefined."""
    flows = [(d, a) for d, a in flows if abs(a) > 1e-12]
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    d0 = min(d for d, _ in flows)
    ts = np.array([(d - d0).days / 365.0 for d, _ in flows])
    amts = np.array([a for _, a in flows])

    def npv(r: float) -> float:
        return float(np.sum(amts / (1.0 + r) ** ts))

    def dnpv(r: float) -> float:
        return float(np.sum(-ts * amts / (1.0 + r) ** (ts + 1)))

    r = guess
    for _ in range(100):
        try:
            v, dv = npv(r), dnpv(r)
        except (OverflowError, ZeroDivisionError, FloatingPointError):
            break
        if dv == 0 or not math.isfinite(v):
            break
        step = v / dv
        r_new = r - step
        if r_new <= -0.9999:
            r_new = (r - 0.9999) / 2
        if abs(r_new - r) < 1e-10:
            return float(r_new)
        r = r_new
    # bisection fallback
    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def range_xirr(value: pd.Series, flow: pd.Series, b: int, e: int) -> float | None:
    """XIRR for the range: starting value counts as money in at the base date, flows in (b, e], value out at e."""
    dates = list(value.index)
    out: list[tuple[dt.date, float]] = []
    if value.iloc[b] > 0:
        out.append((dates[b], -float(value.iloc[b])))
    for i in range(b + 1, e + 1):
        if flow.iloc[i] != 0:
            out.append((dates[i], -float(flow.iloc[i])))
    out.append((dates[e], float(value.iloc[e])))
    return xirr(out)


def shadow(value: pd.Series, flow: pd.Series, price: pd.Series, b: int, e: int) -> pd.Series:
    """Replay the same money into another asset: the starting value at b, then every external flow
    on its own day, bought at that day's total-return price (already in the base currency)."""
    p = price.to_numpy()
    units = float(value.iloc[b]) / p[b] if p[b] > 0 else 0.0
    out = np.zeros(e - b + 1)
    out[0] = units * p[b]
    fl = flow.to_numpy()
    for k, i in enumerate(range(b + 1, e + 1), start=1):
        if fl[i] != 0 and p[i] > 0:
            units = max(units + fl[i] / p[i], 0.0)
        out[k] = units * p[i]
    return pd.Series(out, index=value.index[b : e + 1])
