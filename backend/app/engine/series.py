"""Daily portfolio series in the base currency.

Pure functions: no database, no network. Callers pass in market data.

Model
- One unified calendar = union of trading days of every series involved (US, TASE, FX, benchmarks).
  Every price series is forward-filled onto it (rule 4).
- Quantities are converted to today's split basis: qty * (product of splits after the trade date),
  because cached closes are split-adjusted (rule 3).
- Cash is tracked in the base currency. Each cash movement is converted at THAT day's FX (rule 2).
- External flows = deposits - withdrawals, plus an implicit deposit whenever buys would push cash
  below zero (so people who only log buys still get correct returns).
- Daily return treats the day's flow as arriving at the close (prices are entered at the close):
      r_t = (V_t - F_t - V_{t-1}) / V_{t-1}      (or / F_t when V_{t-1} is zero)
  and chaining those gives the time-weighted return (rule 5).
- Dividends: recorded DIVIDEND transactions add cash. For tickers with no recorded dividends,
  cash dividends are added automatically from the price feed (per-share dividend * shares held
  before the ex-date), so total return always includes dividends.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, Protocol

import numpy as np
import pandas as pd


class TxLike(Protocol):
    ticker: str
    type: str
    date: dt.date
    quantity: float
    price: float
    currency: str
    fees: float


@dataclass
class Tx:
    ticker: str
    type: str
    date: dt.date
    quantity: float = 0.0
    price: float = 0.0
    currency: str = "USD"
    fees: float = 0.0
    id: int | None = None
    note: str = ""
    cost_price: float | None = None  # average cost for imported positions (gain/loss only)


@dataclass
class MarketData:
    frame: pd.DataFrame  # index date; columns close, adj_close, dividend (major units, current split basis)
    currency: str  # USD or ILS
    splits: list[tuple[dt.date, float]] = field(default_factory=list)


@dataclass
class PortfolioSeries:
    dates: list[dt.date]
    value: pd.Series  # total value in base currency
    cash: pd.Series
    flow: pd.Series  # external flow each day (+ in, - out)
    invested: pd.Series  # cumulative external flows
    ret: pd.Series  # daily TWR return
    holding_value: pd.DataFrame  # per ticker, base currency
    holding_qty: pd.DataFrame  # per ticker, current split basis
    contribution: pd.DataFrame  # per ticker, daily contribution to r_t
    fx: pd.Series  # ILS per USD on the calendar
    close: pd.DataFrame  # forward-filled closes (native currency) per ticker on the calendar
    base: str

    def index(self) -> pd.Series:
        return (1.0 + self.ret).cumprod()


def _type(t) -> str:
    return t.value if hasattr(t, "value") else str(t)


def convert(amount: float, from_ccy: str, to_ccy: str, ils_per_usd: float) -> float:
    if from_ccy == to_ccy:
        return amount
    if from_ccy == "USD" and to_ccy == "ILS":
        return amount * ils_per_usd
    if from_ccy == "ILS" and to_ccy == "USD":
        return amount / ils_per_usd
    raise ValueError(f"Unsupported currency pair {from_ccy}->{to_ccy}")


def fx_factor(ccy: str, base: str, fx: pd.Series) -> pd.Series:
    """Series that turns an amount in `ccy` into the base currency, per day."""
    if ccy == base:
        return pd.Series(1.0, index=fx.index)
    if ccy == "USD" and base == "ILS":
        return fx
    if ccy == "ILS" and base == "USD":
        return 1.0 / fx
    raise ValueError(f"Unsupported currency pair {ccy}->{base}")


def split_factor_after(splits: list[tuple[dt.date, float]], date: dt.date) -> float:
    f = 1.0
    for d, r in splits:
        if d > date:
            f *= r
    return f


def build_calendar(frames: Iterable[pd.DataFrame | pd.Series], start: dt.date, end: dt.date | None = None) -> list[dt.date]:
    """Union of trading days, starting one trading day before `start` so the first flow has a base."""
    days: set[dt.date] = set()
    for f in frames:
        days.update(f.index)
    allday = sorted(days)
    if not allday:
        return [start]
    if end is not None:
        allday = [d for d in allday if d <= end]
    before = [d for d in allday if d < start]
    first = before[-1] if before else start
    cal = [d for d in allday if d >= first]
    if not cal or cal[0] != first:
        cal.insert(0, first)
    return cal


def align(series: pd.Series, calendar: list[dt.date]) -> pd.Series:
    """Forward-fill a series onto the calendar (rule 4). Leading gaps are back-filled."""
    s = series[~series.index.duplicated(keep="last")].sort_index()
    idx = pd.Index(sorted(set(s.index) | set(calendar)))
    return s.reindex(idx).ffill().bfill().reindex(calendar)


def _map_to_calendar(d: dt.date, calendar: list[dt.date]) -> int:
    """Index of the first calendar day on or after `d` (a weekend trade lands on the next session)."""
    import bisect

    i = bisect.bisect_left(calendar, d)
    return min(i, len(calendar) - 1)


TYPE_ORDER = {"DEPOSIT": 0, "WITHDRAWAL": 1, "DIVIDEND": 2, "SELL": 3, "BUY": 4}


def neutral_fx(fx: pd.Series) -> pd.Series:
    """Currency-neutral rate: the latest USD/ILS on every date. Amounts are converted only to show them
    in one currency; no return, gain or comparison ever includes an exchange-rate move."""
    fx = fx.dropna()
    if fx.empty:
        return fx
    return pd.Series(float(fx.iloc[-1]), index=fx.index)


def build_series(
    txs: list[TxLike],
    market: dict[str, MarketData],
    fx: pd.Series,
    base: str = "ILS",
    auto_dividends: bool = True,
    extra_frames: Iterable[pd.DataFrame | pd.Series] = (),
    end: dt.date | None = None,
) -> PortfolioSeries:
    if not txs:
        raise ValueError("No transactions")
    txs = sorted(txs, key=lambda t: (t.date, TYPE_ORDER[_type(t.type)]))
    first = txs[0].date
    tickers = sorted({t.ticker.upper() for t in txs if t.ticker and _type(t.type) in ("BUY", "SELL", "DIVIDEND")})
    frames = [market[t].frame for t in tickers] + list(extra_frames)
    calendar = build_calendar(frames or [fx], first, end)
    n = len(calendar)
    fx_c = align(neutral_fx(fx), calendar)  # stock performance only: one rate for every day

    close = pd.DataFrame(index=calendar)
    div_ps = pd.DataFrame(index=calendar)
    to_base = pd.DataFrame(index=calendar)
    for t in tickers:
        md = market[t]
        close[t] = align(md.frame["close"], calendar)
        d = md.frame["dividend"]
        d = d[d > 0]
        # dividends on days that are not in the calendar (should not happen) roll to next session
        dv = pd.Series(0.0, index=calendar)
        for day, amt in d.items():
            if calendar[0] < day <= calendar[-1]:
                dv.iloc[_map_to_calendar(day, calendar)] += amt
        div_ps[t] = dv
        to_base[t] = fx_factor(md.currency, base, fx_c).values

    recorded_div = {t.ticker.upper() for t in txs if _type(t.type) == "DIVIDEND"}
    by_day: dict[int, list[TxLike]] = {}
    for t in txs:
        by_day.setdefault(_map_to_calendar(t.date, calendar), []).append(t)

    qty = {t: 0.0 for t in tickers}
    cash = 0.0
    value = np.zeros(n)
    cash_arr = np.zeros(n)
    flow = np.zeros(n)
    ret = np.zeros(n)
    hv = np.zeros((n, len(tickers)))
    hq = np.zeros((n, len(tickers)))
    contrib = np.zeros((n, len(tickers)))
    prev_v = 0.0
    prev_hv = np.zeros(len(tickers))
    col = {t: i for i, t in enumerate(tickers)}
    close_np = close.to_numpy() if tickers else np.zeros((n, 0))
    tob_np = to_base.to_numpy() if tickers else np.zeros((n, 0))
    div_np = div_ps.to_numpy() if tickers else np.zeros((n, 0))
    fxv = fx_c.to_numpy()

    for i in range(n):
        f = 0.0
        tick_cash = np.zeros(len(tickers))  # cash paid into (-) / received from (+) each ticker today
        # automatic cash dividends on shares held before today's trades
        if auto_dividends:
            for t in tickers:
                j = col[t]
                if t not in recorded_div and div_np[i, j] > 0 and qty[t] > 0:
                    amt = qty[t] * div_np[i, j] * tob_np[i, j]
                    cash += amt
                    tick_cash[j] += amt
        for t in by_day.get(i, []):
            typ = _type(t.type)
            rate = fxv[i]
            if typ == "DEPOSIT":
                amt = convert(t.price, t.currency, base, rate)
                cash += amt
                f += amt
            elif typ == "WITHDRAWAL":
                amt = convert(t.price, t.currency, base, rate)
                cash -= amt
                f -= amt
            elif typ == "DIVIDEND":
                amt = convert(t.price - t.fees, t.currency, base, rate)
                cash += amt
                tick_cash[col[t.ticker.upper()]] += amt
            elif typ in ("BUY", "SELL"):
                tk = t.ticker.upper()
                factor = split_factor_after(market[tk].splits, t.date)
                gross = convert(t.quantity * t.price, t.currency, base, rate)
                fees = convert(t.fees, t.currency, base, rate)
                if typ == "BUY":
                    qty[tk] += t.quantity * factor
                    cash -= gross + fees
                    tick_cash[col[tk]] -= gross + fees
                else:
                    qty[tk] -= t.quantity * factor
                    if abs(qty[tk]) < 1e-9:
                        qty[tk] = 0.0
                    cash += gross - fees
                    tick_cash[col[tk]] += gross - fees
        if cash < -1e-9:
            f += -cash  # implicit deposit to fund buys
            cash = 0.0
        q = np.array([qty[t] for t in tickers])
        hv_i = q * close_np[i] * tob_np[i] if tickers else np.zeros(0)
        v = float(hv_i.sum()) + cash
        # End-of-day flow convention (trades are entered at the close). On the very first day,
        # or after the account was emptied, the flow itself is the base.
        denom = prev_v if prev_v > 1e-9 else f
        if denom > 1e-9:
            ret[i] = (v - f - prev_v) / denom
            contrib[i] = (hv_i - prev_hv + tick_cash) / denom
        value[i] = v
        cash_arr[i] = cash
        flow[i] = f
        hv[i] = hv_i
        hq[i] = q
        prev_v = v
        prev_hv = hv_i

    idx = pd.Index(calendar)
    return PortfolioSeries(
        dates=calendar,
        value=pd.Series(value, index=idx),
        cash=pd.Series(cash_arr, index=idx),
        flow=pd.Series(flow, index=idx),
        invested=pd.Series(np.cumsum(flow), index=idx),
        ret=pd.Series(ret, index=idx),
        holding_value=pd.DataFrame(hv, index=idx, columns=tickers),
        holding_qty=pd.DataFrame(hq, index=idx, columns=tickers),
        contribution=pd.DataFrame(contrib, index=idx, columns=tickers),
        fx=fx_c,
        close=close,
        base=base,
    )


def total_return_in_base(md: MarketData, fx: pd.Series, calendar: list[dt.date], base: str) -> pd.Series:
    """Benchmark total-return price (adj_close) on the calendar, in its own currency's terms (converted at one
    fixed rate only to show it in the base currency, so its returns are its local returns)."""
    fx_c = align(neutral_fx(fx), calendar)
    p = align(md.frame["adj_close"], calendar)
    return p * fx_factor(md.currency, base, fx_c)


def holding_lots(txs: list[TxLike], ticker: str, splits: list[tuple[dt.date, float]], fx: pd.Series, base: str) -> dict:
    """Average-cost position for one ticker: quantity (current split basis), avg cost (native, current basis),
    cost basis in base currency (converted at today's rate: currency moves are left out)."""
    fx = neutral_fx(fx)
    qty = 0.0
    cost_native = 0.0
    cost_base = 0.0
    realized_base = 0.0
    for t in sorted(txs, key=lambda x: (x.date, TYPE_ORDER[_type(x.type)])):
        if t.ticker.upper() != ticker or _type(t.type) not in ("BUY", "SELL"):
            continue
        factor = split_factor_after(splits, t.date)
        q = t.quantity * factor
        rate = float(align(fx, [t.date]).iloc[0])
        if _type(t.type) == "BUY":
            unit_cost = getattr(t, "cost_price", None) or t.price
            qty += q
            cost_native += t.quantity * unit_cost + t.fees
            cost_base += convert(t.quantity * unit_cost + t.fees, t.currency, base, rate)
        elif qty > 0:
            frac = min(q / qty, 1.0)
            proceeds = convert(t.quantity * t.price - t.fees, t.currency, base, rate)
            realized_base += proceeds - cost_base * frac
            cost_native *= 1 - frac
            cost_base *= 1 - frac
            qty -= q
            if qty < 1e-9:
                qty = 0.0
    return {
        "quantity": qty,
        "avg_cost": cost_native / qty if qty > 0 else 0.0,
        "cost_base": cost_base,
        "realized_base": realized_base,
    }
