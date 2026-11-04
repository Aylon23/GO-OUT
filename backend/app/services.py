"""Glue between the database/cache and the pure engine. Builds API-ready payloads."""
from __future__ import annotations

import bisect
import datetime as dt
import math
import logging
import threading

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from .engine import risk
from .engine.returns import base_index, end_index, range_start, range_xirr, rebased, shadow, twr
from .engine.series import MarketData, PortfolioSeries, Tx, align, build_series, convert, fx_factor, holding_lots, split_factor_after, total_return_in_base
from .market.cache import FX_TICKER, NoPriceData, currency_for, data_version, load_history, load_splits, refresh_latest
from .models import Settings, SymbolMeta, Transaction, TxType

MAX_POINTS = 900
log = logging.getLogger(__name__)

_rev = 0
_rev_lock = threading.Lock()
_built: dict[tuple, tuple] = {}


def bump_revision() -> None:
    global _rev
    with _rev_lock:
        _rev += 1
        _built.clear()


def backfill_aliases(session: Session) -> None:
    """Once: give portfolios that have no nicknames a Hebrew/English one (editable in Settings)."""
    from .market.names import auto_aliases
    from .models import Portfolio

    st = get_settings(session)
    if st.aliases_backfilled:
        return
    for p in session.exec(select(Portfolio)).all():
        if not (p.aliases or "").strip():
            p.aliases = ", ".join(auto_aliases(p.name))
            session.add(p)
    st.aliases_backfilled = True
    session.add(st)
    session.commit()


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


def fx_history(session: Session) -> pd.Series:
    """Real daily USD/ILS. Only for converting an amount the user typed on a past date into shares."""
    return load_history(session, FX_TICKER)["close"]


def fx_series(session: Session) -> pd.Series:
    """USD/ILS for the app's calculations: today's rate on every date. Stock performance only, so no
    return, gain, comparison, risk figure or scenario ever includes a currency move; amounts in the
    other currency are converted at today's rate just to show them."""
    from .engine.series import neutral_fx

    return neutral_fx(fx_history(session))


def refresh_prices(session: Session) -> None:
    """Latest prices for everything the family holds, the benchmarks and USD/ILS, in one batched
    request (skipped if done in the last minute). Runs when a page loads."""
    held = session.exec(select(Transaction.ticker).distinct()).all()
    refresh_latest(session, [t for t in held if t] + get_settings(session).benchmark_list() + [FX_TICKER])


def to_tx(t: Transaction) -> Tx:
    return Tx(t.ticker.upper(), t.type.value if hasattr(t.type, "value") else t.type, t.date, t.quantity, t.price,
              t.currency, t.fees, t.id, t.note, t.cost_price)


def all_txs(session: Session, pid: int = 1) -> list[Tx]:
    q = select(Transaction).where(Transaction.portfolio_id == pid).order_by(Transaction.date, Transaction.id)
    return [to_tx(t) for t in session.exec(q).all()]


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


def _fallback_market(session: Session, ticker: str, txs: list) -> MarketData:
    """Market data for a ticker whose prices could not be fetched: cached prices if any, otherwise
    the last trade price held flat. Used so totals keep the last known value."""
    try:
        df = load_history(session, ticker, fetch=False)
    except NoPriceData:
        df = None
    if df is None or df.empty:
        trades = [x for x in txs if x.ticker == ticker and x.type in ("BUY", "SELL")]
        last = max(trades, key=lambda x: x.date) if trades else None
        px = float(last.price) if last else 0.0
        day = min(x.date for x in trades) if trades else dt.date.today()
        df = pd.DataFrame({"close": [px], "adj_close": [px], "dividend": [0.0]}, index=[day])
    return MarketData(df, currency_for(session, ticker), load_splits(session, ticker))


class Context:
    """Everything needed to answer questions about the real portfolio for one request."""

    def __init__(self, session: Session, pid: int = 1):
        self.session = session
        self.pid = pid
        self.settings = get_settings(session)
        self.base = self.settings.base_currency
        self.txs = all_txs(session, pid)
        self.warnings: list[str] = []
        self.bench_md: dict[str, MarketData] = {}
        self.series: PortfolioSeries | None = None
        self.fx: pd.Series | None = None
        self.market: dict[str, MarketData] = {}
        self.missing: dict[str, str] = {}
        if not self.txs:
            return  # empty portfolio: no market data needed
        refresh_prices(session)
        self.load_market()
        key = (_rev, data_version(), pid, self.base, self.settings.auto_dividends, tuple(self.bench_md), dt.date.today())
        hit = _built.get(key)
        if hit:
            self.series, self.market, self.missing = hit
        else:
            self.market = {}
            self.missing: dict[str, str] = {}
            for t in sorted({t.ticker for t in self.txs if t.ticker}):
                try:
                    self.market[t] = market_data(session, t)
                except NoPriceData as exc:
                    # Never turn a failed price fetch into a loss: keep the last known price.
                    log.warning("No prices for %s in portfolio %s: %s", t, pid, exc)
                    self.missing[t] = str(exc)
                    self.market[t] = _fallback_market(session, t, self.txs)
            self.series = build_series(
                self.txs, self.market, self.fx, self.base, self.settings.auto_dividends,
                extra_frames=[m.frame for m in self.bench_md.values()],
            )
            if len(_built) > 50:  # older price versions are never asked for again
                _built.clear()
            _built[key] = (self.series, self.market, self.missing)
        for t in self.missing:
            self.warnings.append(f"No current price for {t}. Showing its last known value.")

    def price_status(self, t: str) -> tuple[str, str | None]:
        """("ok" | "stale" | "missing", date of the last price)."""
        md = self.market[t]
        last = md.frame.index.max() if len(md.frame) else None
        if t in self.missing:
            return "missing", last.isoformat() if last is not None else None
        if last is not None and self.series is not None and (self.series.dates[-1] - last).days > 5:
            return "stale", last.isoformat()
        return "ok", last.isoformat() if last is not None else None

    def load_market(self) -> None:
        """FX and benchmark history. Benchmarks that fail become warnings, not errors."""
        if self.fx is not None:
            return
        self.fx = fx_series(self.session)
        for b in self.settings.benchmark_list():
            try:
                self.bench_md[b] = market_data(self.session, b)
            except NoPriceData as exc:
                self.warnings.append(str(exc))

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
        # Never start before the first trade: the portfolio and the benchmarks are measured over
        # exactly the same days (from the close of the first trading day).
        first_trade = min(t.date for t in self.txs)
        b = max(b, base_index(cal, first_trade))
        return min(b, e), e


def open_positions(ctx: Context) -> dict[str, float]:
    """Shares held today per ticker (current split basis)."""
    if ctx.series is None:
        return {}
    last = ctx.series.holding_qty.iloc[-1]
    return {t: float(q) for t, q in last.items() if q > 1e-9}


def position_gain(ctx: Context, t: str) -> dict:
    """THE definition of a position's gain, used on every screen: (price now - average cost) x shares,
    in the security's own currency, converted to the base currency at today's rate. Purchase-date
    exchange rates are deliberately left out (they are unknown for imported positions)."""
    s = ctx.series
    md = ctx.market[t]
    lots = holding_lots(ctx.txs, t, md.splits, ctx.fx, ctx.base)
    q = lots["quantity"]
    price = float(s.close[t].iloc[-1])
    rate = float(fx_factor(md.currency, ctx.base, align(ctx.fx, [s.dates[-1]])).iloc[0])
    cost_native = lots["avg_cost"] * q
    gain_native = price * q - cost_native if q > 0 else 0.0
    return {"quantity": q, "avg_cost": lots["avg_cost"], "price": price, "currency": md.currency,
            "value_native": price * q, "cost_native": cost_native, "gain_native": gain_native,
            "gain_pct": gain_native / cost_native if cost_native > 0 else None,
            "gain_base": gain_native * rate, "cost_base_today": cost_native * rate, "realized_base": lots["realized_base"]}


def day_change(ctx: Context) -> dict:
    """Today's move, like a broker app: each holding's last close vs the close before it (its own
    trading days, so a Tel Aviv holiday is not a zero), times shares held, converted at today's
    rate. The portfolio's figure is the sum, so it always matches the holdings table."""
    out: dict[str, dict] = {}
    total = 0.0
    s = ctx.series
    if s is None:
        return {"total": 0.0, "pct": None, "by_ticker": out, "date": None}
    end = s.dates[-1]
    rate_today = lambda ccy: float(fx_factor(ccy, ctx.base, align(ctx.fx, [end])).iloc[0])
    latest = None
    for t, q in open_positions(ctx).items():
        md = ctx.market[t]
        f = md.frame
        if t in ctx.missing or len(f) < 2 or (end - f.index[-1]).days > 4:
            out[t] = {"change": 0.0, "change_native": 0.0, "pct": None, "date": None, "price_change": None}
            continue
        last, prev = float(f["close"].iloc[-1]), float(f["close"].iloc[-2])
        # a buy made today at today's price has no "today" gain from the previous close
        bought_today = sum(x.quantity for x in ctx.txs if x.ticker == t and x.type == "BUY" and x.date >= f.index[-1])
        q_prev = max(q - bought_today, 0.0)
        native = (last - prev) * q_prev
        base = native * rate_today(md.currency)
        out[t] = {"change": base, "change_native": native, "pct": last / prev - 1 if prev else None,
                  "date": f.index[-1].isoformat(), "price_change": last - prev}
        total += base
        latest = max(latest, f.index[-1]) if latest else f.index[-1]
    prev_value = float(s.value.iloc[-1]) - total
    return {"total": total, "pct": total / prev_value if prev_value > 0 else None, "by_ticker": out,
            "date": latest.isoformat() if latest else None}


def cost_summary(ctx: Context) -> dict:
    """Open positions' gain vs average cost, summed with position_gain (the one definition)."""
    if ctx.series is None:
        return {"positions_value": 0.0, "cost": 0.0, "gain": 0.0, "gain_pct": None}
    value = cost = gain = 0.0
    for t in open_positions(ctx):
        g = position_gain(ctx, t)
        value += float(ctx.series.holding_value[t].iloc[-1])
        cost += g["cost_base_today"]
        gain += g["gain_base"]
    return {"positions_value": value, "cost": cost, "gain": gain, "gain_pct": gain / cost if cost > 0 else None}


def realized_summary(ctx: Context) -> dict:
    """Profit already taken: sales recorded here (average-cost method) plus realized lines imported
    from a spreadsheet. Returns the total and a per-ticker breakdown, in the base currency."""
    from .models import RealizedEntry

    per: dict[str, float] = {}
    if ctx.series is not None:
        for t in ctx.series.holding_value.columns:
            r = holding_lots(ctx.txs, t, ctx.market[t].splits, ctx.fx, ctx.base)["realized_base"]
            if abs(r) > 1e-9:
                per[t] = per.get(t, 0.0) + r
    entries = ctx.session.exec(select(RealizedEntry).where(RealizedEntry.portfolio_id == ctx.pid,
                                                           RealizedEntry.archived_batch.is_(None))).all()
    if entries:
        if ctx.fx is None:
            ctx.fx = fx_series(ctx.session)
        for e in entries:
            day = e.date or dt.date.today()
            rate = float(align(ctx.fx, [day]).iloc[0])
            amt = convert(e.amount, e.currency, ctx.base, rate)
            per[e.ticker or "Other"] = per.get(e.ticker or "Other", 0.0) + amt
    return {"total": sum(per.values()), "by_ticker": per}


def empty_payload(ctx: Context) -> dict:
    return {"empty": True, "base": ctx.base, "benchmarks": list(ctx.bench_md), "warnings": ctx.warnings}


def portfolio_payload(session: Session, code: str = "1Y", start: str | None = None, end: str | None = None, pid: int = 1) -> dict:
    ctx = Context(session, pid)
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
    today = day_change(ctx)
    today_change, today_pct = today["total"], today["pct"]

    pick = lambda ser: [_f(ser.iloc[i - b]) for i in pos]
    # Was the range asked for longer than the real history? Then the page shows the look-back.
    first_trade = min(t.date for t in ctx.txs)
    req = dt.date.fromisoformat(start) if code.upper() == "CUSTOM" and start else range_start(
        code if code.upper() != "CUSTOM" else "MAX", cal[e], first_trade)
    cost = cost_summary(ctx)
    realized = realized_summary(ctx)
    has_cost_basis = any(t.cost_price for t in ctx.txs)
    payload = {
        "empty": False,
        "base": ctx.base,
        "benchmarks": list(bench),
        "warnings": ctx.warnings,
        "range": {"code": code.upper(), "start": cal[b].isoformat(), "end": cal[e].isoformat(), "first_date": cal[0].isoformat(),
                  "tracking_since": first_trade.isoformat(), "short_history": req < first_trade},
        "summary": {
            "cost_gain": _f(cost["gain"]),
            "cost_gain_pct": _f(cost["gain_pct"]),
            "has_cost_basis": has_cost_basis,
            "realized": _f(realized["total"]),
            "value": _f(actual_end),
            "invested": _f(s.invested.iloc[e]),
            "total_gain": _f(actual_end - s.invested.iloc[e]),
            "range_gain": _f(range_gain),
            "today_change": _f(today_change),
            "today_pct": _f(today_pct),
            "today_date": today["date"],
            "twr": _f(port_twr),
            # an annual rate from a few days of history is noise; wait for a month
            "xirr": _f(range_xirr(s.value, s.flow, b, e)) if (cal[e] - cal[b]).days >= 30 else None,
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
        from .market.names import clean_name

        m.name = clean_name(m.name or info.get("longName") or info.get("shortName") or t, t)
        qt = (info.get("quoteType") or "").upper()
        m.type = m.type or {"EQUITY": "Stock", "ETF": "ETF", "INDEX": "Index", "MUTUALFUND": "Fund"}.get(qt, "")
        m.sector = info.get("sector") or (m.sector if m.sector not in ("ETF", "Fund") else "")
        m.country = info.get("country") or m.country
        if not m.currency:
            c = info.get("currency") or ""
            m.currency = "ILS" if c in ("ILA", "ILS") else c if c in ("USD",) else currency_for(session, t)
        m.info_fetched = bool(info)
        session.add(m)
    session.commit()


def holding_return(ctx: Context, t: str, b: int, e: int) -> float | None:
    """A holding's own total return (price + dividends, in its own currency) over the part of
    (b, e] during which it was held. None when there is no usable price or no full day held."""
    if t in ctx.missing:
        return None
    s = ctx.series
    q = s.holding_qty[t].to_numpy()
    held = [i for i in range(b, e + 1) if q[i] > 1e-9]
    if not held:
        return None
    start, end = held[0], held[-1]
    if end < e and q[e] <= 1e-9:
        end = min(end + 1, e)  # sold during the range: include the day of the sale
    if end <= start:
        return None
    tr = align(ctx.market[t].frame["adj_close"], s.dates)
    a0, a1 = float(tr.iloc[start]), float(tr.iloc[end])
    return a1 / a0 - 1 if a0 > 0 else None


def sector_weights(session: Session, t: str, weight: float, meta) -> dict[str, float]:
    """Spread a holding's weight over sectors. Funds are looked through; never "ETF"."""
    from .market.funds import fund_sectors

    kind = (meta.type if meta else "") or ""
    if kind in ("ETF", "Fund"):
        weights, category = fund_sectors(session, t)
        if weights:
            return {k: v * weight for k, v in weights.items()}
        return {category or "Unclassified": weight}
    return {(meta.sector if meta and meta.sector and meta.sector not in ("ETF", "Fund") else "Unclassified"): weight}


def holdings_payload(session: Session, code: str = "1Y", start: str | None = None, end: str | None = None, pid: int = 1) -> dict:
    from .market.names import clean_name

    ctx = Context(session, pid)
    if ctx.series is None:
        return {"empty": True, "base": ctx.base, "rows": []}
    s = ctx.series
    b, e = ctx.window(code, start, end)
    total = float(s.value.iloc[e])
    tickers = list(s.holding_value.columns)
    ensure_meta_info(session, tickers)
    realized = realized_summary(ctx)
    today = day_change(ctx)
    rows = []
    for t in tickers:
        g = position_gain(ctx, t)
        dc = today["by_ticker"].get(t, {})
        val = float(s.holding_value[t].iloc[e])
        contrib = float(s.contribution[t].iloc[b + 1 : e + 1].sum())
        held_in_range = bool((s.holding_qty[t].iloc[b : e + 1] > 1e-9).any())
        if g["quantity"] <= 0 and not held_in_range:
            continue
        m = session.get(SymbolMeta, t)
        status, price_date = ctx.price_status(t)
        opening = any(x.ticker == t and x.cost_price for x in ctx.txs)
        rows.append({
            "ticker": t,
            "name": clean_name(m.name if m else t, t),
            "type": m.type if m else "",
            "sector": (m.sector if m and m.sector not in ("", "ETF", "Fund") else ""),
            "currency": g["currency"],
            "quantity": _f(g["quantity"]),
            "avg_cost": _f(g["avg_cost"]),
            "price": _f(g["price"]) if status != "missing" else None,
            "price_status": status,
            "price_date": price_date,
            "value": _f(val),
            "weight": _f(val / total if total else 0),
            # gain vs average cost: in the security's currency, and the same amount in the base currency
            "unrealized_native": _f(g["gain_native"]),
            "unrealized": _f(g["gain_base"]),
            "unrealized_pct": _f(g["gain_pct"]),
            "opening": opening,
            "range_return": _f(holding_return(ctx, t, b, e)),
            "contribution": _f(contrib) if status != "missing" else None,
            "closed": g["quantity"] <= 0,
            "realized": _f(realized["by_ticker"].get(t)),
            "today": _f(dc.get("change")),
            "today_native": _f(dc.get("change_native")),
            "today_pct": _f(dc.get("pct")),
        })
    open_rows = [r for r in rows if not r["closed"]]
    ranked = sorted([r for r in rows if r["range_return"] is not None], key=lambda r: r["range_return"])
    sectors: dict[str, float] = {}
    for r in open_rows:
        for k, v in sector_weights(session, r["ticker"], r["weight"] or 0, session.get(SymbolMeta, r["ticker"])).items():
            sectors[k] = sectors.get(k, 0) + v
    cash_w = float(s.cash.iloc[e]) / total if total else 0
    if cash_w > 0.0005:
        sectors["Cash"] = cash_w
    first_trade = min(x.date for x in ctx.txs)
    return {
        "empty": False,
        "base": ctx.base,
        "range": {"code": code.upper(), "start": s.dates[b].isoformat(), "end": s.dates[e].isoformat(), "tracking_since": first_trade.isoformat()},
        "lookback_basis": False,
        "warnings": ctx.warnings,
        "realized": _f(realized["total"]),
        "realized_other": [{"ticker": k, "amount": _f(v)} for k, v in realized["by_ticker"].items() if k not in tickers],
        "total": _f(total),
        "today": {"change": _f(today["total"]), "pct": _f(today["pct"]), "date": today["date"]},
        "cash": _f(s.cash.iloc[e]),
        "rows": sorted(rows, key=lambda r: -(r["value"] or 0)),
        "best": [r["ticker"] for r in reversed(ranked[-3:]) if (r["range_return"] or 0) > 0],
        "worst": [r["ticker"] for r in ranked[:3] if (r["range_return"] or 0) < 0],
        "sectors": [{"sector": k, "weight": _f(v)} for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])],
        "concentrated": [r["ticker"] for r in open_rows if (r["weight"] or 0) > 0.25],
    }


def holding_detail(session: Session, ticker: str, code: str = "1Y", pid: int = 1) -> dict:
    from .market.names import clean_name

    ticker = ticker.upper()
    ctx = Context(session, pid)
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
        "name": clean_name(m.name if m else ticker, ticker),
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


def risk_payload(session: Session, code: str = "MAX", start: str | None = None, end: str | None = None, pid: int = 1) -> dict:
    ctx = Context(session, pid)
    if ctx.series is None:
        return {"empty": True}
    s = ctx.series
    b, e = ctx.window(code, start, end)
    idx = s.index().iloc[b : e + 1]
    first_trade = min(t.date for t in ctx.txs)
    req = dt.date.fromisoformat(start) if code.upper() == "CUSTOM" and start else range_start(
        code if code.upper() != "CUSTOM" else "MAX", s.dates[e], first_trade)
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
        "range": {"code": code.upper(), "start": s.dates[b].isoformat(), "end": s.dates[e].isoformat(),
                  "tracking_since": first_trade.isoformat(), "short_history": req < first_trade or (s.dates[e] - first_trade).days < 30},
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
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
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


def swap_payload(session: Session, ticker: str, replacement: str, pid: int = 1) -> dict:
    ticker, replacement = ticker.upper(), replacement.upper()
    ctx = Context(session, pid)
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


def custom_payload(session: Session, holdings: list[dict], start: str, initial: float, monthly: float, rebalance: str, pid: int = 1) -> dict:
    ctx = Context(session, pid)
    ctx.load_market()
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
