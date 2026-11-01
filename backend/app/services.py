"""Glue between the database/cache and the pure engine. Builds API-ready payloads."""
from __future__ import annotations

import bisect
import datetime as dt
import math
import threading

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from .engine import risk
from .engine.returns import base_index, end_index, range_start, range_xirr, rebased, shadow, twr
from .engine.series import MarketData, PortfolioSeries, Tx, align, build_series, convert, fx_factor, holding_lots, split_factor_after, total_return_in_base
from .market.cache import FX_TICKER, NoPriceData, currency_for, load_history, load_splits
from .models import Settings, SymbolMeta, Transaction, TxType

MAX_POINTS = 900

_rev = 0
_rev_lock = threading.Lock()
_built: dict[tuple, tuple] = {}


def bump_revision() -> None:
    global _rev
    with _rev_lock:
        _rev += 1
        _built.clear()


def get_settings(session: Session) -> Settings:
    s = session.get(Settings, 1)
    if s is None:
        s = Settings()
        session.add(s)
        session.commit()
        session.refresh(s)
    return s


def market_data(session: Session, ticker: str) -> MarketData:
    df = load_history(session, ticker)
    return MarketData(df, currency_for(session, ticker), load_splits(session, ticker))


def fx_series(session: Session) -> pd.Series:
    return load_history(session, FX_TICKER)["close"]


def to_tx(t: Transaction) -> Tx:
    return Tx(t.ticker.upper(), t.type.value if hasattr(t.type, "value") else t.type, t.date, t.quantity, t.price,
              t.currency, t.fees, t.id, t.note)


def all_txs(session: Session) -> list[Tx]:
    return [to_tx(t) for t in session.exec(select(Transaction).order_by(Transaction.date, Transaction.id)).all()]


def _f(x):
    """JSON-safe float."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _sample_positions(b: int, e: int) -> list[int]:
    n = e - b + 1
    if n <= MAX_POINTS:
        return list(range(b, e + 1))
    step = n / MAX_POINTS
    pos = sorted({b + int(i * step) for i in range(MAX_POINTS)} | {e})
    return pos


class Context:
    """Everything needed to answer questions about the real portfolio for one request."""

    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings(session)
        self.base = self.settings.base_currency
        self.txs = all_txs(session)
        self.warnings: list[str] = []
        self.fx = fx_series(session)
        self.bench_md: dict[str, MarketData] = {}
        for b in self.settings.benchmark_list():
            try:
                self.bench_md[b] = market_data(session, b)
            except NoPriceData as exc:
                self.warnings.append(str(exc))
        self.series: PortfolioSeries | None = None
        if self.txs:
            key = (_rev, self.base, self.settings.auto_dividends, tuple(self.bench_md), dt.date.today())
            hit = _built.get(key)
            if hit:
                self.series, self.market = hit
            else:
                self.market = {}
                for t in sorted({t.ticker for t in self.txs if t.ticker}):
                    self.market[t] = market_data(session, t)
                self.series = build_series(
                    self.txs, self.market, self.fx, self.base, self.settings.auto_dividends,
                    extra_frames=[m.frame for m in self.bench_md.values()],
                )
                _built[key] = (self.series, self.market)

    def bench_prices(self) -> dict[str, pd.Series]:
        cal = self.series.dates
        return {b: total_return_in_base(md, self.fx, cal, self.base) for b, md in self.bench_md.items()}

    def window(self, code: str, start: str | None = None, end: str | None = None) -> tuple[int, int]:
        cal = self.series.dates
        e = end_index(cal, dt.date.fromisoformat(end)) if end else len(cal) - 1
        if code.upper() == "CUSTOM" and start:
            s = dt.date.fromisoformat(start)
        else:
            s = range_start(code if code.upper() != "CUSTOM" else "MAX", cal[e], cal[0])
        b = base_index(cal, s)
        return min(b, e), e


def empty_payload(ctx: Context) -> dict:
    return {"empty": True, "base": ctx.base, "benchmarks": list(ctx.bench_md), "warnings": ctx.warnings}


def portfolio_payload(session: Session, code: str = "1Y", start: str | None = None, end: str | None = None) -> dict:
    ctx = Context(session)
    if ctx.series is None:
        return empty_payload(ctx)
    s = ctx.series
    b, e = ctx.window(code, start, end)
    cal = s.dates
    bench = ctx.bench_prices()
    idx = s.index()
    pos = _sample_positions(b, e)

    perf = {"portfolio": rebased(idx, b, e)}
    for k, p in bench.items():
        perf[k] = rebased(p, b, e)
    dd = {k: risk.drawdown_series(v / 100.0) * 100.0 for k, v in perf.items()}

    sh_rows = []
    actual_end = float(s.value.iloc[e])
    for k, p in bench.items():
        sv = shadow(s.value, s.flow, p, b, e)
        val = float(sv.iloc[-1])
        sh_rows.append({"symbol": k, "value": _f(val), "diff": _f(actual_end - val), "diff_pct": _f(actual_end / val - 1 if val else None)})

    flows_in_range = float(s.flow.iloc[b + 1 : e + 1].sum())
    range_gain = actual_end - float(s.value.iloc[b]) - flows_in_range
    port_twr = twr(s.ret, b, e)
    today_change = float(s.value.iloc[e] - s.value.iloc[e - 1] - s.flow.iloc[e]) if e > 0 else 0.0
    today_pct = float(s.ret.iloc[e]) if e > 0 else 0.0

    pick = lambda ser: [_f(ser.iloc[i - b]) for i in pos]
    payload = {
        "empty": False,
        "base": ctx.base,
        "benchmarks": list(bench),
        "warnings": ctx.warnings,
        "range": {"code": code.upper(), "start": cal[b].isoformat(), "end": cal[e].isoformat(), "first_date": cal[0].isoformat()},
        "summary": {
            "value": _f(actual_end),
            "invested": _f(s.invested.iloc[e]),
            "total_gain": _f(actual_end - s.invested.iloc[e]),
            "range_gain": _f(range_gain),
            "today_change": _f(today_change),
            "today_pct": _f(today_pct),
            "twr": _f(port_twr),
            "xirr": _f(range_xirr(s.value, s.flow, b, e)),
            "benchmarks": [{"symbol": k, "twr": _f(perf[k].iloc[-1] / 100 - 1)} for k in bench],
            "start_value": _f(s.value.iloc[b]),
        },
        "series": {
            "dates": [cal[i].isoformat() for i in pos],
            "value": [_f(s.value.iloc[i]) for i in pos],
            "invested": [_f(s.invested.iloc[i]) for i in pos],
            "perf": {k: pick(v) for k, v in perf.items()},
            "drawdown": {k: pick(v) for k, v in dd.items()},
        },
        "shadow": {"actual": _f(actual_end), "rows": sh_rows},
    }
    return payload


def ensure_meta_info(session: Session, tickers: list[str]) -> None:
    from .market.provider import ProviderError, get_provider

    for t in tickers:
        m = session.get(SymbolMeta, t) or SymbolMeta(ticker=t)
        if m.info_fetched:
            continue
        try:
            info = get_provider().info(t)
        except ProviderError:
            info = {}
        m.name = m.name or info.get("longName") or info.get("shortName") or t
        qt = (info.get("quoteType") or "").upper()
        m.type = m.type or {"EQUITY": "Stock", "ETF": "ETF", "INDEX": "Index", "MUTUALFUND": "Fund"}.get(qt, "")
        m.sector = info.get("sector") or ("ETF" if m.type in ("ETF", "Fund") else "") or m.sector
        m.country = info.get("country") or m.country
        if not m.currency:
            c = info.get("currency") or ""
            m.currency = "ILS" if c in ("ILA", "ILS") else c if c in ("USD",) else currency_for(session, t)
        m.info_fetched = bool(info)
        session.add(m)
    session.commit()


def holdings_payload(session: Session, code: str = "1Y", start: str | None = None, end: str | None = None) -> dict:
    ctx = Context(session)
    if ctx.series is None:
        return {"empty": True, "base": ctx.base, "rows": []}
    s = ctx.series
    b, e = ctx.window(code, start, end)
    total = float(s.value.iloc[e])
    tickers = list(s.holding_value.columns)
    ensure_meta_info(session, tickers)
    rows = []
    for t in tickers:
        md = ctx.market[t]
        lots = holding_lots(ctx.txs, t, md.splits, ctx.fx, ctx.base)
        val = float(s.holding_value[t].iloc[e])
        contrib = float(s.contribution[t].iloc[b + 1 : e + 1].sum())
        # range return of the position: chained daily return of that holding while held
        hv = s.holding_value[t].to_numpy()
        c = s.contribution[t].to_numpy()
        denom_prev = s.value.shift(1).fillna(0).to_numpy()
        r_prod = 1.0
        for i in range(b + 1, e + 1):
            if hv[i - 1] > 1e-9:
                r_prod *= 1 + c[i] * (denom_prev[i] / hv[i - 1])
        held_in_range = (hv[b : e + 1] > 1e-9).any() or contrib != 0
        if lots["quantity"] <= 0 and not held_in_range:
            continue
        m = session.get(SymbolMeta, t)
        price = float(s.close[t].iloc[e])
        unreal = val - lots["cost_base"] if lots["quantity"] > 0 else 0.0
        rows.append({
            "ticker": t,
            "name": m.name if m else t,
            "type": m.type if m else "",
            "sector": (m.sector if m and m.sector else "Other"),
            "currency": md.currency,
            "quantity": _f(lots["quantity"]),
            "avg_cost": _f(lots["avg_cost"]),
            "price": _f(price),
            "value": _f(val),
            "weight": _f(val / total if total else 0),
            "unrealized": _f(unreal),
            "unrealized_pct": _f(unreal / lots["cost_base"] if lots["cost_base"] > 0 and lots["quantity"] > 0 else None),
            "range_return": _f(r_prod - 1 if held_in_range else None),
            "contribution": _f(contrib),
            "closed": lots["quantity"] <= 0,
        })
    open_rows = [r for r in rows if not r["closed"]]
    ranked = sorted([r for r in rows if r["range_return"] is not None], key=lambda r: r["range_return"])
    sectors: dict[str, float] = {}
    for r in open_rows:
        sectors[r["sector"]] = sectors.get(r["sector"], 0) + (r["weight"] or 0)
    cash_w = float(s.cash.iloc[e]) / total if total else 0
    if cash_w > 0.0005:
        sectors["Cash"] = cash_w
    return {
        "empty": False,
        "base": ctx.base,
        "range": {"code": code.upper(), "start": s.dates[b].isoformat(), "end": s.dates[e].isoformat()},
        "total": _f(total),
        "cash": _f(s.cash.iloc[e]),
        "rows": sorted(rows, key=lambda r: -(r["value"] or 0)),
        "best": [r["ticker"] for r in reversed(ranked[-3:]) if (r["range_return"] or 0) > 0],
        "worst": [r["ticker"] for r in ranked[:3] if (r["range_return"] or 0) < 0],
        "sectors": [{"sector": k, "weight": _f(v)} for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])],
        "concentrated": [r["ticker"] for r in open_rows if (r["weight"] or 0) > 0.25],
    }


def holding_detail(session: Session, ticker: str, code: str = "1Y") -> dict:
    ticker = ticker.upper()
    ctx = Context(session)
    md = market_data(session, ticker)
    df = md.frame
    last = df.index[-1]
    s0 = range_start(code, last, df.index[0]) if code.upper() != "MAX" else df.index[0]
    txs = [t for t in ctx.txs if t.ticker == ticker]
    if code.upper() == "MAX" and txs:
        s0 = min(s0, txs[0].date)
    window = df[df.index >= s0]
    pos = _sample_positions(0, len(window) - 1)
    m = session.get(SymbolMeta, ticker)
    return {
        "ticker": ticker,
        "name": m.name if m else ticker,
        "currency": md.currency,
        "prices": [{"date": window.index[i].isoformat(), "close": _f(window["close"].iloc[i])} for i in pos],
        "markers": [
            {"date": t.date.isoformat(), "type": t.type,
             "price": _f(t.price / split_factor_after(md.splits, t.date)), "quantity": _f(t.quantity)}
            for t in txs if t.type in ("BUY", "SELL") and t.date >= s0
        ],
        "transactions": [tx_dict(t) for t in reversed(txs)],
    }


def tx_dict(t) -> dict:
    return {"id": t.id, "ticker": t.ticker, "type": t.type.value if hasattr(t.type, "value") else t.type,
            "date": t.date.isoformat(), "quantity": t.quantity, "price": t.price, "currency": t.currency,
            "fees": t.fees, "note": t.note}


def risk_payload(session: Session, code: str = "MAX", start: str | None = None, end: str | None = None) -> dict:
    ctx = Context(session)
    if ctx.series is None:
        return {"empty": True}
    s = ctx.series
    b, e = ctx.window(code, start, end)
    idx = s.index().iloc[b : e + 1]
    bench = {k: v.iloc[b : e + 1] for k, v in ctx.bench_prices().items()}
    rf = ctx.settings.risk_free
    spy = bench.get("SPY")
    if spy is None and bench:
        spy = next(iter(bench.values()))
    ref = "SPY" if "SPY" in bench else (next(iter(bench)) if bench else None)
    columns = {"portfolio": risk.stats(idx, rf, spy)}
    for k, v in bench.items():
        columns[k] = risk.stats(v, rf, spy)
    years = {"portfolio": risk.calendar_year_returns(idx)}
    for k, v in bench.items():
        years[k] = risk.calendar_year_returns(v)
    all_years = sorted({y for d in years.values() for y in d})
    return {
        "empty": False,
        "range": {"code": code.upper(), "start": s.dates[b].isoformat(), "end": s.dates[e].isoformat()},
        "risk_free": rf,
        "beta_reference": ref,
        "columns": list(columns),
        "stats": _clean(columns),
        "years": [{"year": y, **{k: _f(years[k].get(y)) for k in years}} for y in all_years],
        "monthly": {k: _f(v) for k, v in risk.monthly_returns(idx).items()},
    }


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        return _f(obj)
    return obj


# ------------------------------------------------------------------ what-if

def _as_traded_price(md: MarketData, d: dt.date, cal_hint: list[dt.date] | None = None) -> tuple[float, dt.date]:
    df = md.frame
    prior = df[df.index <= d]
    if prior.empty:
        raise ValueError(f"No price on {d.isoformat()}")
    day = prior.index[-1]
    return float(prior["close"].iloc[-1]) * split_factor_after(md.splits, day), day


def _fx_on(fx: pd.Series, d: dt.date) -> float:
    return float(align(fx, [d]).iloc[0])


def swap_payload(session: Session, ticker: str, replacement: str) -> dict:
    ticker, replacement = ticker.upper(), replacement.upper()
    ctx = Context(session)
    if ctx.series is None:
        raise ValueError("Add a transaction first.")
    own = [t for t in ctx.txs if t.ticker == ticker and t.type in ("BUY", "SELL")]
    if not own:
        raise ValueError(f"You have no trades in {ticker}.")
    rep = market_data(session, replacement)
    first = rep.frame.index[0]
    if own[0].date < first:
        raise ValueError(f"{replacement} only has prices from {first.isoformat()}, after your first {ticker} trade on {own[0].date.isoformat()}.")
    md_x = ctx.market[ticker]
    hypo: list[Tx] = [t for t in ctx.txs if t.ticker != ticker]
    held_x = 0.0
    held_y = 0.0
    for t in own:
        rate = _fx_on(ctx.fx, t.date)
        cash_base = convert(t.quantity * t.price, t.currency, ctx.base, rate)
        amt_y = convert(cash_base, ctx.base, rep.currency, rate)
        py, _ = _as_traded_price(rep, t.date)
        fy = split_factor_after(rep.splits, t.date)
        fees_y = t.fees * (amt_y / (t.quantity * t.price)) if t.quantity * t.price else 0
        fx_factor_x = split_factor_after(md_x.splits, t.date)
        if t.type == "BUY":
            q = amt_y / py
            held_x += t.quantity * fx_factor_x
            held_y += q * fy
            hypo.append(Tx(replacement, "BUY", t.date, q, py, rep.currency, fees_y))
        else:
            held_x -= t.quantity * fx_factor_x
            q = amt_y / py
            if held_x <= 1e-9 or q * fy > held_y:
                q = held_y / fy  # closing the position closes the replacement too
            held_y -= q * fy
            hypo.append(Tx(replacement, "SELL", t.date, q, py, rep.currency, fees_y))
    market = dict(ctx.market)
    market[replacement] = rep
    hs = build_series(hypo, market, ctx.fx, ctx.base, ctx.settings.auto_dividends,
                      extra_frames=[m.frame for m in ctx.bench_md.values()] + [m.frame for m in ctx.market.values()])
    real = ctx.series
    common = sorted(set(real.dates) & set(hs.dates))
    start = own[0].date
    common = [d for d in common if d >= common[max(bisect.bisect_left(common, start) - 1, 0)]]
    pos = _sample_positions(0, len(common) - 1)
    real_v = real.value.reindex(common).ffill()
    hypo_v = hs.value.reindex(common).ffill()
    diff = float(hypo_v.iloc[-1] - real_v.iloc[-1])
    return {
        "base": ctx.base,
        "ticker": ticker,
        "replacement": replacement,
        "real_value": _f(real_v.iloc[-1]),
        "hypo_value": _f(hypo_v.iloc[-1]),
        "diff": _f(diff),
        "diff_pct": _f(hypo_v.iloc[-1] / real_v.iloc[-1] - 1 if real_v.iloc[-1] else None),
        "since": start.isoformat(),
        "trades": len(own),
        "series": {"dates": [common[i].isoformat() for i in pos],
                   "real": [_f(real_v.iloc[i]) for i in pos], "hypo": [_f(hypo_v.iloc[i]) for i in pos]},
    }


def _tr_market(md: MarketData) -> MarketData:
    """Total-return version of a symbol (dividends reinvested), for custom portfolios."""
    f = md.frame.copy()
    f["close"] = f["adj_close"]
    f["dividend"] = 0.0
    return MarketData(f, md.currency, [])


def custom_payload(session: Session, holdings: list[dict], start: str, initial: float, monthly: float, rebalance: str) -> dict:
    ctx = Context(session)
    start_d = dt.date.fromisoformat(start)
    weights = {h["symbol"].upper(): float(h["weight"]) for h in holdings if float(h.get("weight") or 0) > 0}
    total_w = sum(weights.values())
    if not weights or total_w <= 0:
        raise ValueError("Add at least one symbol with a weight above 0.")
    weights = {k: v / total_w for k, v in weights.items()}
    market: dict[str, MarketData] = {}
    errors = []
    for sym in weights:
        try:
            md = market_data(session, sym)
        except NoPriceData as exc:
            errors.append({"symbol": sym, "message": str(exc)})
            continue
        if md.frame.index[0] > start_d:
            errors.append({"symbol": sym, "message": f"{sym} has no prices before {md.frame.index[0].isoformat()}. Pick a start date on or after that.", "first_date": md.frame.index[0].isoformat()})
        market[sym] = _tr_market(md)
    if errors:
        return {"ok": False, "errors": errors}

    fx = ctx.fx
    frames = [m.frame for m in market.values()] + [m.frame for m in ctx.bench_md.values()]
    cal_all = sorted(set().union(*[set(f.index) for f in frames]))
    cal = [d for d in cal_all if d >= start_d]
    if len(cal) < 2:
        raise ValueError("Pick a start date at least a few days in the past.")
    fx_c = align(fx, cal)
    prices = {s: align(m.frame["close"], cal) for s, m in market.items()}
    to_base = {s: fx_factor(m.currency, ctx.base, fx_c) for s, m in market.items()}

    units = {s: 0.0 for s in weights}
    txs: list[Tx] = []
    month_seen = (cal[0].year, cal[0].month)
    year_seen = cal[0].year

    def buy(i: int, amount_base: float):
        d = cal[i]
        txs.append(Tx("", "DEPOSIT", d, 0, amount_base, ctx.base))
        for s, w in weights.items():
            p = float(prices[s].iloc[i])
            native = amount_base * w / float(to_base[s].iloc[i])
            q = native / p
            units[s] += q
            txs.append(Tx(s, "BUY", d, q, p, market[s].currency))

    buy(0, initial)
    for i in range(1, len(cal)):
        d = cal[i]
        if rebalance == "yearly" and d.year != year_seen:
            year_seen = d.year
            vals = {s: units[s] * float(prices[s].iloc[i]) * float(to_base[s].iloc[i]) for s in weights}
            tot = sum(vals.values())
            for s, w in weights.items():
                target_native = tot * w / float(to_base[s].iloc[i])
                p = float(prices[s].iloc[i])
                dq = target_native / p - units[s]
                if abs(dq) * p > 1e-6:
                    txs.append(Tx(s, "SELL" if dq < 0 else "BUY", d, abs(dq), p, market[s].currency))
                    units[s] += dq
        if monthly > 0 and (d.year, d.month) != month_seen:
            month_seen = (d.year, d.month)
            buy(i, monthly)
    # sells first on rebalance days so cash funds the buys (engine orders SELL before BUY)
    cs = build_series(txs, market, fx, ctx.base, False, extra_frames=[m.frame for m in ctx.bench_md.values()])
    b = base_index(cs.dates, start_d)
    e = len(cs.dates) - 1
    bench = {k: total_return_in_base(md, fx, cs.dates, ctx.base) for k, md in ctx.bench_md.items()}
    idx_c = cs.index()
    perf = {"custom": rebased(idx_c, b, e)}
    stats_idx = {"custom": idx_c.iloc[b : e + 1]}
    if ctx.series is not None:
        real_idx = ctx.series.index().reindex(cs.dates).ffill()
        if ctx.series.dates[0] <= cs.dates[b]:
            perf["portfolio"] = rebased(real_idx, b, e)
            stats_idx["portfolio"] = real_idx.iloc[b : e + 1]
    for k, p in bench.items():
        perf[k] = rebased(p, b, e)
        stats_idx[k] = p.iloc[b : e + 1]
    spy = stats_idx.get("SPY")
    rf = ctx.settings.risk_free
    pos = _sample_positions(b, e)
    dd = {k: risk.drawdown_series(v / 100.0) * 100.0 for k, v in perf.items()}
    sh = {}
    for k, p in bench.items():
        sh[k] = _f(shadow(cs.value, cs.flow, p, b, e).iloc[-1])
    return {
        "ok": True,
        "base": ctx.base,
        "range": {"start": cs.dates[b].isoformat(), "end": cs.dates[e].isoformat()},
        "value": _f(cs.value.iloc[e]),
        "invested": _f(cs.invested.iloc[e]),
        "twr": _f(perf["custom"].iloc[-1] / 100 - 1),
        "xirr": _f(range_xirr(cs.value, cs.flow, b, e)),
        "shadow": sh,
        "columns": list(stats_idx),
        "stats": _clean({k: risk.stats(v, rf, spy) for k, v in stats_idx.items()}),
        "series": {
            "dates": [cs.dates[i].isoformat() for i in pos],
            "value": [_f(cs.value.iloc[i]) for i in pos],
            "invested": [_f(cs.invested.iloc[i]) for i in pos],
            "perf": {k: [_f(v.iloc[i - b]) for i in pos] for k, v in perf.items()},
            "drawdown": {k: [_f(v.iloc[i - b]) for i in pos] for k, v in dd.items()},
        },
    }


def symbol_chart(session: Session, symbol: str, code: str = "1Y") -> dict:
    """Quick view: symbol vs SPY total return in the base currency, rebased to 100."""
    settings = get_settings(session)
    md = market_data(session, symbol)
    spy = market_data(session, "SPY") if symbol.upper() != "SPY" else None
    fx = fx_series(session)
    frames = [md.frame] + ([spy.frame] if spy else [])
    last = md.frame.index[-1]
    s0 = range_start(code, last, md.frame.index[0])
    cal = sorted({d for f in frames for d in f.index if s0 <= d <= last})
    if len(cal) < 2:
        raise ValueError("Not enough history.")
    out = {"symbol": md and symbol.upper(), "dates": [d.isoformat() for d in cal], "series": {}}
    p = total_return_in_base(md, fx, cal, settings.base_currency)
    out["series"][symbol.upper()] = [_f(v) for v in (p / p.iloc[0] * 100)]
    if spy:
        q = total_return_in_base(spy, fx, cal, settings.base_currency)
        out["series"]["SPY"] = [_f(v) for v in (q / q.iloc[0] * 100)]
    out["price"] = _f(md.frame["close"].iloc[-1])
    out["currency"] = md.currency
    out["first_date"] = md.frame.index[0].isoformat()
    return out
