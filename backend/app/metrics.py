"""Beta and valuation (P/E) for portfolios next to any stocks or indices the user picks.

- Beta is measured from daily total returns against SPY, on SPY's trading days, over the
  selected range. For a portfolio that means today's holdings, same shares throughout.
- Rolling beta uses a 6-month (126 trading day) window.
- Portfolio P/E is the weighted harmonic mean: total value / total earnings. Holdings with no
  P/E or negative earnings are left out and reported, so coverage is visible.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
from sqlmodel import Session

from .engine.returns import base_index, range_start
from .engine.series import align, total_return_in_base
from .market.cache import NoPriceData
from .market.names import clean_name
from .market.provider import ProviderError, get_provider
from .models import Fundamentals, Portfolio, SymbolMeta
from .services import Context, _f, _sample_positions, fx_series, get_settings, market_data, open_positions

REF = "SPY"
WINDOW = 126
TTL = dt.timedelta(hours=24)
# Yahoo has no P/E for indices; use the ETF that tracks them.
INDEX_PROXY = {"^GSPC": "SPY", "^SPX": "SPY", "^NDX": "QQQ", "^IXIC": "QQQ", "^DJI": "DIA", "^RUT": "IWM"}


def _pos(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def fundamentals(session: Session, ticker: str) -> Fundamentals:
    ticker = ticker.upper()
    now = dt.datetime.now(dt.timezone.utc)
    row = session.get(Fundamentals, ticker)
    if row is not None:
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=dt.timezone.utc)
        if now - fetched < TTL:
            return row
    try:
        info = get_provider().info(INDEX_PROXY.get(ticker, ticker))
    except ProviderError:
        return row or Fundamentals(ticker=ticker, fetched_at=now)
    # trailingAnnualDividendYield and yield (funds) are fractions; dividendYield is in percent
    dy = _pos(info.get("trailingAnnualDividendYield")) or _pos(info.get("yield"))
    if dy is None and _pos(info.get("dividendYield")) is not None:
        dy = _pos(info.get("dividendYield")) / 100
    row = row or Fundamentals(ticker=ticker, fetched_at=now)
    row.trailing_pe = _pos(info.get("trailingPE"))
    row.forward_pe = _pos(info.get("forwardPE"))
    row.beta = _pos(info.get("beta") or info.get("beta3Year"))
    row.dividend_yield = dy
    row.fetched_at = now
    session.merge(row)
    session.commit()
    return row


def weighted_valuation(session: Session, weights: dict[str, float]) -> dict:
    """Harmonic-weighted P/E and forward P/E, weighted dividend yield, with coverage."""
    out = {}
    funds = {t: fundamentals(session, t) for t in weights}
    for field in ("trailing_pe", "forward_pe"):
        num = den = covered = 0.0
        missing = []
        for t, w in weights.items():
            pe = getattr(funds[t], field)
            if pe and pe > 0:
                covered += w
                den += w / pe
            else:
                missing.append(t)
        out[field] = (covered / den) if den > 0 else None
        out[f"{field}_coverage"] = covered
        out[f"{field}_missing"] = missing
    dys = [(w, funds[t].dividend_yield) for t, w in weights.items()]
    out["dividend_yield"] = sum(w * (d or 0.0) for w, d in dys) if any(d is not None for _, d in dys) else None
    return out


def _returns_on(cal: list[dt.date], price: pd.Series) -> pd.Series:
    return price.reindex(cal).pct_change()


def _beta(rx: pd.Series, rm: pd.Series) -> float | None:
    df = pd.concat([rx, rm], axis=1).dropna()
    if len(df) < 20:
        return None
    x, m = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
    v = np.var(m, ddof=1)
    return float(np.cov(x, m, ddof=1)[0, 1] / v) if v > 0 else None


def _rolling_beta(rx: pd.Series, rm: pd.Series) -> pd.Series:
    cov = rx.rolling(WINDOW, min_periods=WINDOW).cov(rm)
    var = rm.rolling(WINDOW, min_periods=WINDOW).var()
    return cov / var


def metrics_payload(session: Session, pids: list[int], symbols: list[str], code: str = "1Y") -> dict:
    settings = get_settings(session)
    base = settings.base_currency
    fx = fx_series(session)
    ref = market_data(session, REF)
    last = ref.frame.index.max()
    start = range_start(code if code.upper() != "CUSTOM" else "1Y", last, ref.frame.index.min())
    warm = start - dt.timedelta(days=int(WINDOW * 1.6))  # history before the range for the rolling window
    cal_all = [d for d in ref.frame.index if d >= warm]
    ref_tr = total_return_in_base(ref, fx, cal_all, base)
    rm = _returns_on(cal_all, ref_tr)
    in_range = [d for d in cal_all if d >= start]
    b = base_index(cal_all, start)

    rows, rolling, warnings = [], {}, []

    def add(key: str, label: str, kind: str, price: pd.Series, val: dict, note: str = ""):
        rx = _returns_on(cal_all, price)
        beta = _beta(rx.iloc[b + 1 :], rm.iloc[b + 1 :])
        rb = _rolling_beta(rx, rm).iloc[b:]
        rolling[key] = rb
        rows.append({"key": key, "label": label, "kind": kind, "beta": _f(beta), "note": note,
                     **{k: (_f(v) if isinstance(v, (int, float)) or v is None else v) for k, v in val.items()}})

    for pid in pids:
        p = session.get(Portfolio, pid)
        if not p:
            continue
        ctx = Context(session, pid)
        positions = open_positions(ctx)
        if not positions:
            continue
        ctx.load_market()
        value = pd.Series(0.0, index=cal_all)
        weights = {}
        for t, q in positions.items():
            md = ctx.market[t]
            tr = total_return_in_base(md, fx, cal_all, base)
            # before a holding's first trading day, treat it as flat (no return) rather than missing
            tr = tr.where([d >= md.frame.index.min() for d in cal_all]).bfill()
            value = value + q * tr
            weights[t] = float(ctx.series.holding_value[t].iloc[-1])
        total = sum(weights.values())
        weights = {t: w / total for t, w in weights.items()} if total else weights
        add(f"p{pid}", p.name, "portfolio", value, weighted_valuation(session, weights))

    for sym in symbols:
        sym = sym.upper()
        try:
            md = market_data(session, sym)
        except NoPriceData as exc:
            warnings.append(str(exc))
            continue
        price = total_return_in_base(md, fx, cal_all, base)
        price = price.where([d >= md.frame.index.min() for d in cal_all])
        f = fundamentals(session, sym)
        meta = session.get(SymbolMeta, sym)
        note = f"P/E via {INDEX_PROXY[sym]}" if sym in INDEX_PROXY else ""
        add(sym, sym, "symbol", price, {"trailing_pe": f.trailing_pe, "forward_pe": f.forward_pe,
                                         "dividend_yield": f.dividend_yield, "name": clean_name(meta.name if meta else sym, sym)}, note)

    pos = _sample_positions(0, len(in_range) - 1)
    return {
        "base": base,
        "reference": REF,
        "window_days": WINDOW,
        "range": {"code": code.upper(), "start": in_range[0].isoformat() if in_range else None, "end": last.isoformat()},
        "rows": rows,
        "warnings": warnings,
        "rolling": {"dates": [in_range[i].isoformat() for i in pos],
                    "series": {k: [_f(v.iloc[i]) if i < len(v) else None for i in pos] for k, v in rolling.items()}},
    }
