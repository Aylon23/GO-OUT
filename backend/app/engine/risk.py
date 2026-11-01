"""Risk statistics on a value index (portfolio TWR index or benchmark total-return price)."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd


def daily_returns(index: pd.Series) -> pd.Series:
    return index.pct_change().iloc[1:].fillna(0.0)


def periods_per_year(index: pd.Series) -> float:
    """Observations per year on the unified calendar (forward-filled days count, so we measure it)."""
    n = len(index) - 1
    if n < 2:
        return 252.0
    days = (index.index[-1] - index.index[0]).days
    if days <= 0:
        return 252.0
    return n / (days / 365.25)


def cagr(index: pd.Series) -> float | None:
    if len(index) < 2 or index.iloc[0] <= 0:
        return None
    days = (index.index[-1] - index.index[0]).days
    if days <= 0:
        return None
    total = index.iloc[-1] / index.iloc[0]
    if days < 365:
        return float(total - 1.0)  # do not annualize less than a year
    return float(total ** (365.25 / days) - 1.0)


def volatility(index: pd.Series) -> float | None:
    r = daily_returns(index)
    if len(r) < 2:
        return None
    return float(r.std(ddof=1) * math.sqrt(periods_per_year(index)))


def drawdown_series(index: pd.Series) -> pd.Series:
    peak = index.cummax()
    return index / peak - 1.0


def max_drawdown(index: pd.Series) -> dict:
    """Worst peak-to-trough drop, with peak, trough and recovery dates (recovery None if not yet)."""
    if len(index) < 2:
        return {"value": 0.0, "peak": None, "trough": None, "recovery": None}
    dd = drawdown_series(index)
    trough_pos = int(np.argmin(dd.to_numpy()))
    value = float(dd.iloc[trough_pos])
    if value >= 0:
        return {"value": 0.0, "peak": None, "trough": None, "recovery": None}
    peak_pos = int(np.argmax(index.iloc[: trough_pos + 1].to_numpy()))
    peak_val = index.iloc[peak_pos]
    recovery = None
    after = index.iloc[trough_pos:]
    rec = after[after >= peak_val]
    if len(rec):
        recovery = rec.index[0]
    iso = lambda d: d.isoformat() if d is not None else None
    return {"value": value, "peak": iso(index.index[peak_pos]), "trough": iso(index.index[trough_pos]), "recovery": iso(recovery)}


def sharpe(index: pd.Series, rf: float) -> float | None:
    """(Annualized mean return - risk-free) / annualized volatility."""
    r = daily_returns(index)
    if len(r) < 2:
        return None
    ppy = periods_per_year(index)
    vol = r.std(ddof=1) * math.sqrt(ppy)
    if vol == 0:
        return None
    return float((r.mean() * ppy - rf) / vol)


def beta_corr(index: pd.Series, bench: pd.Series) -> tuple[float | None, float | None]:
    a = daily_returns(index)
    b = daily_returns(bench.reindex(index.index))
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < 3:
        return None, None
    x, y = df.iloc[:, 1].to_numpy(), df.iloc[:, 0].to_numpy()
    var = np.var(x, ddof=1)
    if var == 0:
        return None, None
    beta = float(np.cov(y, x, ddof=1)[0, 1] / var)
    corr = float(np.corrcoef(y, x)[0, 1])
    return beta, corr


def calendar_year_returns(index: pd.Series) -> dict[int, float]:
    """Return per calendar year, from the last value of the prior year (or the first value)."""
    out: dict[int, float] = {}
    if len(index) < 2:
        return out
    years = sorted({d.year for d in index.index})
    for y in years:
        prior = index[[d.year < y for d in index.index]]
        cur = index[[d.year == y for d in index.index]]
        start = prior.iloc[-1] if len(prior) else cur.iloc[0]
        if start > 0 and len(cur):
            out[y] = float(cur.iloc[-1] / start - 1.0)
    return out


def monthly_returns(index: pd.Series) -> dict[str, float]:
    out: dict[str, float] = {}
    if len(index) < 2:
        return out
    s = index.copy()
    s.index = pd.to_datetime(s.index)
    month_end = s.groupby([s.index.year, s.index.month]).last()
    prev = None
    first_val = s.iloc[0]
    for (y, m), v in month_end.items():
        start = prev if prev is not None else first_val
        if start > 0:
            out[f"{y:04d}-{m:02d}"] = float(v / start - 1.0)
        prev = v
    return out


def stats(index: pd.Series, rf: float, bench: pd.Series | None = None) -> dict:
    years = calendar_year_returns(index)
    beta, corr = beta_corr(index, bench) if bench is not None else (None, None)
    best = max(years.items(), key=lambda kv: kv[1]) if years else None
    worst = min(years.items(), key=lambda kv: kv[1]) if years else None
    return {
        "cagr": cagr(index),
        "volatility": volatility(index),
        "max_drawdown": max_drawdown(index),
        "sharpe": sharpe(index, rf),
        "beta": beta,
        "correlation": corr,
        "best_year": {"year": best[0], "value": best[1]} if best else None,
        "worst_year": {"year": worst[0], "value": worst[1]} if worst else None,
        "total_return": float(index.iloc[-1] / index.iloc[0] - 1.0) if len(index) > 1 and index.iloc[0] > 0 else None,
    }
