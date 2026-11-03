"""Per-stock data pack for AI prompts, built from our own price data plus Yahoo fundamentals.

Built at most once a day per ticker (cached in StockPackCache). Prices, returns and distances come
from the app's price cache, each in the stock's own currency. Valuation multiples are compared with
the stock's own 5-year history (reconstructed from annual statements, so approximate) and with the
median of large peers in the same sector. Any field the data can't support is None and listed in
"missing", so the models never fill gaps from memory.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import statistics

import pandas as pd
from sqlmodel import Session

from .market.cache import NoPriceData, load_history
from .market.provider import ProviderError, get_provider
from .models import StockPackCache

log = logging.getLogger(__name__)
REPORT_LAG = dt.timedelta(days=60)  # annual figures count from about two months after the fiscal year ends

SECTOR_PEERS: dict[str, list[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE"],
    "Communication Services": ["GOOGL", "META", "NFLX", "TMUS", "DIS", "VZ", "T", "CMCSA"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG"],
    "Consumer Defensive": ["WMT", "PG", "COST", "KO", "PEP", "PM", "MDLZ", "CL"],
    "Healthcare": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "AMGN"],
    "Financial Services": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP"],
    "Industrials": ["GE", "CAT", "RTX", "HON", "UNP", "BA", "DE", "LMT"],
    "Energy": ["XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "OXY"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "EXC"],
    "Real Estate": ["PLD", "AMT", "EQIX", "WELL", "SPG", "PSA", "O", "CCI"],
    "Basic Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DD", "NUE"],
}
PACK_FIELDS = [
    "price", "range_52w", "from_high", "returns", "valuation", "growth", "margins", "roe", "debt_to_equity",
    "analysts", "revisions", "next_earnings", "dividend_yield", "beta", "factor_exposures",
]


# ---------- cache ----------

def _cached(session: Session, ticker: str, kind: str, day: str, build):
    row = session.get(StockPackCache, (ticker, kind))
    if row is not None and row.day == day:
        return json.loads(row.data)
    data = build()
    row = row or StockPackCache(ticker=ticker, kind=kind)
    row.day, row.data = day, json.dumps(data, ensure_ascii=False, default=str)
    session.add(row)
    session.commit()
    return data


def raw_fundamentals(session: Session, ticker: str, today: dt.date | None = None) -> dict:
    day = (today or dt.date.today()).isoformat()

    def build():
        p = get_provider()
        try:
            return p.fundamentals(ticker) if hasattr(p, "fundamentals") else {"info": p.info(ticker)}
        except ProviderError as exc:
            log.info("No fundamentals for %s: %s", ticker, exc)
            return {}

    return _cached(session, ticker.upper(), "raw", day, build)


def peer_info(session: Session, ticker: str, today: dt.date | None = None) -> dict:
    day = (today or dt.date.today()).isoformat()

    def build():
        try:
            return get_provider().info(ticker)
        except ProviderError:
            return {}

    return _cached(session, ticker.upper(), "info", day, build)


# ---------- helpers ----------

def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _r(x, n=4):
    return None if x is None else round(x, n)


def _series(table: dict | None, row: str) -> list[tuple[dt.date, float]]:
    """A statement row as [(date, value)], oldest first."""
    vals = (table or {}).get(row) or {}
    out = []
    for k, v in vals.items():
        try:
            out.append((dt.date.fromisoformat(k[:10]), float(v)))
        except (ValueError, TypeError):
            continue
    return sorted(out)


def _first_row(table: dict | None, rows: tuple[str, ...]) -> list[tuple[dt.date, float]]:
    for r in rows:
        s = _series(table, r)
        if s:
            return s
    return []


def _step(points: list[tuple[dt.date, float]], d: dt.date, lag: dt.timedelta = REPORT_LAG) -> float | None:
    """The latest value reported (date + lag) on or before d."""
    v = None
    for pd_, x in points:
        if pd_ + lag <= d:
            v = x
    return v


def _percentile(hist: list[float], x: float | None) -> float | None:
    hist = [h for h in hist if h is not None and h > 0]
    if x is None or x <= 0 or len(hist) < 12:
        return None
    return sum(h <= x for h in hist) / len(hist)


def _ret(p: pd.Series, end: dt.date, months: int) -> float | None:
    p = p[p.index <= end]
    if p.empty:
        return None
    start = end - dt.timedelta(days=round(months * 30.44))
    before = p[p.index <= start]
    if before.empty:
        return None
    return float(p.iloc[-1] / before.iloc[-1] - 1)


def _trend(vals: list[float | None]) -> str | None:
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None
    d = v[-1] - v[0]
    return "up" if d > 0.01 else "down" if d < -0.01 else "flat"


# ---------- the pack ----------

def build(session: Session, ticker: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    ticker = ticker.upper()
    return _cached(session, ticker, "pack", today.isoformat(), lambda: _build(session, ticker, today))


def _build(session: Session, ticker: str, today: dt.date) -> dict:
    raw = raw_fundamentals(session, ticker, today)
    info = raw.get("info") or {}
    out: dict = {"ticker": ticker, "name": info.get("longName") or info.get("shortName"), "sector": info.get("sector") or None,
                 "industry": info.get("industry") or None, "currency": "ILS" if ticker.endswith(".TA") else (info.get("currency") or "USD"),
                 "basis": "own trading currency; returns are price total returns"}
    # prices from our own cache
    try:
        h = load_history(session, ticker)
        h = h[h.index <= today]
    except NoPriceData:
        h = pd.DataFrame()
    if not h.empty:
        close, adj = h["close"], h["adj_close"]
        as_of = close.index[-1]
        price = float(close.iloc[-1])
        yr = close[close.index >= as_of - dt.timedelta(days=365)]
        hi, lo = float(yr.max()), float(yr.min())
        out["as_of"] = as_of.isoformat()
        out["price"] = _r(price, 2)
        out["range_52w"] = {"low": _r(lo, 2), "high": _r(hi, 2)}
        out["from_high"] = _r(price / hi - 1)
        try:
            spy = load_history(session, "SPY")["adj_close"]
        except NoPriceData:
            spy = None
        rets = {}
        for m in (3, 6, 12):
            r = _ret(adj, as_of, m)
            s = _ret(spy, as_of, m) if spy is not None else None
            rets[f"{m}m"] = {"stock": _r(r), "spy": _r(s), "vs_spy": _r(r - s) if r is not None and s is not None else None}
        out["returns"] = rets
    else:
        price = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
        out["price"] = _r(price, 2)
        out["as_of"] = None
    price = out.get("price")

    # valuation: now, vs own 5-year history, vs sector peers
    q_inc, a_inc, a_bal = raw.get("q_income"), raw.get("a_income"), raw.get("a_balance")
    eps_q = _series(q_inc, "Diluted EPS")
    ttm_eps = sum(v for _, v in eps_q[-4:]) if len(eps_q) >= 4 else _f(info.get("trailingEps"))
    trailing_pe = _f(info.get("trailingPE")) or (price / ttm_eps if price and ttm_eps and ttm_eps > 0 else None)
    forward_pe = _f(info.get("forwardPE"))
    ev_sales = _f(info.get("enterpriseToRevenue"))
    pe_hist, evs_hist = _history_multiples(h, a_inc, a_bal, today)
    peers = _sector_medians(session, out["sector"], ticker, today)
    out["valuation"] = {
        "trailing_pe": {"value": _r(trailing_pe, 2), "own_5y_percentile": _r(_percentile(pe_hist, trailing_pe), 2),
                        "sector_median": peers.get("trailingPE")},
        "forward_pe": {"value": _r(forward_pe, 2), "own_5y_percentile": None, "sector_median": peers.get("forwardPE"),
                       "note": "no forward-P/E history in free data"},
        "ev_sales": {"value": _r(ev_sales, 2), "own_5y_percentile": _r(_percentile(evs_hist, ev_sales), 2),
                     "sector_median": peers.get("enterpriseToRevenue")},
        "history_note": "5-year percentiles use month-end prices over annual reported figures (approximate)",
        "peers": peers.get("_peers", []),
    }

    # growth: year-over-year for the last 4 quarters, and next year's estimates
    rev_q = _first_row(q_inc, ("Total Revenue", "Operating Revenue"))
    def yoy(series):
        res = []
        for d, v in series[-4:]:
            prev = [x for pd_, x in series if 330 <= (d - pd_).days <= 400]
            res.append({"quarter_end": d.isoformat(), "yoy": _r(v / prev[-1] - 1) if prev and prev[-1] else None})
        return res
    ee, re_ = raw.get("earnings_estimate") or {}, raw.get("revenue_estimate") or {}
    out["growth"] = {"revenue_yoy_last4q": yoy(rev_q), "eps_yoy_last4q": yoy(eps_q),
                     "next_year_revenue_growth_est": _r(_f((re_.get("+1y") or {}).get("growth"))),
                     "next_year_eps_growth_est": _r(_f((ee.get("+1y") or {}).get("growth")))}

    # margin trend over the last 4 quarters
    def margin(row):
        num = dict(_first_row(q_inc, (row,)))
        return [_r(num[d] / v) if d in num and v else None for d, v in rev_q[-4:]]
    gm, om, nm = margin("Gross Profit"), margin("Operating Income"), margin("Net Income")
    out["margins"] = {"quarters": [d.isoformat() for d, _ in rev_q[-4:]], "gross": gm, "operating": om, "net": nm,
                      "trend": {"gross": _trend(gm), "operating": _trend(om), "net": _trend(nm)}}
    if not rev_q:
        out["margins"] = None

    out["roe"] = _r(_f(info.get("returnOnEquity")))
    dte = _f(info.get("debtToEquity"))
    out["debt_to_equity"] = _r(dte / 100 if dte is not None else None, 2)  # Yahoo reports it in percent
    target = _f(info.get("targetMeanPrice"))
    n_an = info.get("numberOfAnalystOpinions")
    out["analysts"] = None if not (target or info.get("recommendationKey")) else {
        "consensus": info.get("recommendationKey"), "mean_rating_1_buy_5_sell": _r(_f(info.get("recommendationMean")), 2),
        "analysts": int(n_an) if n_an else None, "mean_target": _r(target, 2),
        "upside": _r(target / price - 1) if target and price else None}
    rev = raw.get("eps_revisions") or {}
    trend = raw.get("eps_trend") or {}
    revisions = {}
    for per in ("0y", "+1y"):
        r = rev.get(per) or {}
        t = trend.get(per) or {}
        cur, ago = _f(t.get("current")), _f(t.get("30daysAgo"))
        if r or (cur and ago):
            revisions[per] = {"up_7d": r.get("upLast7days"), "up_30d": r.get("upLast30days"),
                              "down_7d": r.get("downLast7Days", r.get("downLast7days")), "down_30d": r.get("downLast30days"),
                              "estimate_change_30d": _r(cur / ago - 1) if cur and ago else None}
    out["revisions"] = revisions or None
    out["next_earnings"] = _next_earnings(session, ticker, today)
    div = _f(info.get("dividendRate"))
    out["dividend_yield"] = _r(div / price if div is not None and price else _f(info.get("trailingAnnualDividendYield")))
    out["beta"] = _r(_f(info.get("beta")), 2)
    try:
        from . import factors

        ex = factors.exposures(session, [ticker]).get(ticker, {}).get("local")
        out["factor_exposures"] = {"betas": {k: _r(v, 2) for k, v in ex["betas"].items()}, "r2": _r(ex.get("r2"), 2)} if ex else None
    except Exception as exc:
        log.info("No exposures for %s: %s", ticker, exc)
        out["factor_exposures"] = None
    out["missing"] = _missing(out)
    return out


def _history_multiples(h: pd.DataFrame, a_inc: dict | None, a_bal: dict | None, today: dt.date) -> tuple[list, list]:
    if h is None or h.empty:
        return [], []
    eps = _first_row(a_inc, ("Diluted EPS", "Basic EPS"))
    revenue = _first_row(a_inc, ("Total Revenue", "Operating Revenue"))
    shares = _first_row(a_bal, ("Ordinary Shares Number", "Share Issued"))
    debt = _first_row(a_bal, ("Total Debt",))
    cash = _first_row(a_bal, ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    close = h["close"]
    months = close[close.index >= today - dt.timedelta(days=5 * 365)]
    keys = [d.year * 100 + d.month for d in months.index]
    month_ends = months[[i == len(keys) - 1 or keys[i] != keys[i + 1] for i in range(len(keys))]]
    pe, evs = [], []
    for d, px in month_ends.items():
        e = _step(eps, d)
        if e and e > 0:
            pe.append(px / e)
        rv, sh = _step(revenue, d), _step(shares, d)
        if rv and sh:
            ev = px * sh + (_step(debt, d) or 0) - (_step(cash, d) or 0)
            evs.append(ev / rv)
    return pe, evs


def _sector_medians(session: Session, sector: str | None, ticker: str, today: dt.date) -> dict:
    peers = [p for p in SECTOR_PEERS.get(sector or "", []) if p != ticker]
    if not peers:
        return {}
    vals: dict[str, list[float]] = {"trailingPE": [], "forwardPE": [], "enterpriseToRevenue": []}
    used = []
    for p in peers:
        info = peer_info(session, p, today)
        got = False
        for k in vals:
            v = _f(info.get(k))
            if v is not None and v > 0:
                vals[k].append(v)
                got = True
        if got:
            used.append(p)
    out = {k: _r(statistics.median(v), 2) if len(v) >= 3 else None for k, v in vals.items()}
    out["_peers"] = used
    return out


def _next_earnings(session: Session, ticker: str, today: dt.date) -> dict | None:
    try:
        from .earnings import report_dates

        future = sorted(d for d, _t in report_dates(session, ticker) if d >= today)
    except Exception:
        future = []
    return {"date": future[0].isoformat(), "days_away": (future[0] - today).days} if future else None


def _missing(pack: dict) -> list[str]:
    miss = [k for k in PACK_FIELDS if pack.get(k) in (None, {}, [])]
    v = pack.get("valuation") or {}
    for k in ("trailing_pe", "forward_pe", "ev_sales"):
        for sub in ("value", "own_5y_percentile", "sector_median"):
            if (v.get(k) or {}).get(sub) is None:
                miss.append(f"valuation.{k}.{sub}")
    g = pack.get("growth") or {}
    for k in ("next_year_revenue_growth_est", "next_year_eps_growth_est"):
        if g.get(k) is None:
            miss.append(f"growth.{k}")
    for k in ("revenue_yoy_last4q", "eps_yoy_last4q"):
        if not any(q.get("yoy") is not None for q in g.get(k) or []):
            miss.append(f"growth.{k}")
    return miss
