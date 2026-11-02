"""Yesterday's earnings: the research arrives as a file (docs/earnings-brief.schema.json); the app adds
the numbers from its own price data.

- History per company: for each of the last 8 reports, the reaction-day move (previous close to the close
  of the first session after the report), the 5- and 20-trading-day moves from the same previous close,
  and whether the first-day direction held for 20 days.
- Family impact: who holds it and the shekel P&L of the report move; related holdings (listed in the brief,
  or in the same sector) with their expected move from their weekly-return beta to the company.
- Prediction journal: the outlook's direction is scored after 5 and 20 trading days from the reaction close.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from . import services
from .market.cache import NoPriceData, load_history
from .market.names import clean_name
from .market.provider import ProviderError, get_provider
from .models import EarningsDates, EarningsItem, SymbolMeta

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "earnings-brief.schema.json"
HISTORY = 8
DATES_TTL = dt.timedelta(days=7)
BAND = {5: 0.01, 20: 0.02}  # "flat" means within this move
MAX_CANDIDATES = 8
# The largest S&P 500 companies by market value (approximate, late 2025), most valuable first.
TOP100 = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "JPM", "WMT", "LLY", "ORCL", "V", "MA", "NFLX",
          "XOM", "COST", "JNJ", "PLTR", "HD", "ABBV", "BAC", "PG", "UNH", "GE", "CVX", "KO", "CSCO", "AMD", "TMUS", "WFC", "IBM", "CRM",
          "MS", "PM", "ABT", "GS", "AXP", "INTU", "LIN", "MCD", "DIS", "RTX", "T", "MRK", "PEP", "CAT", "UBER", "NOW", "VZ", "TMO", "BKNG",
          "ISRG", "C", "SCHW", "BLK", "QCOM", "TXN", "AMGN", "BA", "SPGI", "ANET", "GEV", "ADBE", "NEE", "LOW", "PGR", "ACN", "BSX", "SYK",
          "ETN", "AMAT", "DHR", "HON", "TJX", "PFE", "GILD", "COF", "KLAC", "UNP", "LRCX", "DE", "ADP", "APH", "MU", "CMCSA", "PANW", "MDT",
          "COP", "LMT", "KKR", "ADI", "CRWD", "CB", "MO", "SBUX", "INTC", "NKE", "SO"]


class BriefError(ValueError):
    pass


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(brief: dict) -> list[str]:
    import jsonschema

    v = jsonschema.Draft202012Validator(schema())
    return [f"{'/'.join(str(p) for p in e.path) or 'brief'}: {e.message}" for e in sorted(v.iter_errors(brief), key=lambda e: list(e.path))]


# ------------------------------------------------------------------ prices and report dates

def _prices(session: Session, symbol: str) -> pd.Series | None:
    try:
        df = load_history(session, symbol)
    except NoPriceData:
        return None
    return df["adj_close"].astype(float) if df is not None and len(df) else None


def report_dates(session: Session, symbol: str) -> list[tuple[dt.date, str]]:
    symbol = symbol.upper()
    row = session.get(EarningsDates, symbol)
    now = dt.datetime.now(dt.timezone.utc)
    if row is not None:
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=dt.timezone.utc)
        if now - fetched < DATES_TTL:
            return [(dt.date.fromisoformat(d), t) for d, t in json.loads(row.dates)]
    try:
        dates = get_provider().earnings_dates(symbol)
    except (ProviderError, AttributeError) as exc:
        log.info("No earnings dates for %s: %s", symbol, exc)
        dates = []
    row = row or EarningsDates(symbol=symbol)
    row.dates = json.dumps([[d.isoformat(), t] for d, t in dates])
    row.fetched_at = now
    session.add(row)
    session.commit()
    return dates


def reaction_index(idx: list[dt.date], day: dt.date, timing: str) -> int | None:
    """Position of the first session that trades on the news: the report day itself when it came out
    before the open, the next session when after the close."""
    import bisect

    i = bisect.bisect_right(idx, day) if timing == "after_close" else bisect.bisect_left(idx, day)
    return i if 0 < i < len(idx) else None


def moves_after(p: pd.Series, day: dt.date, timing: str) -> dict | None:
    idx = list(p.index)
    i = reaction_index(idx, day, timing)
    if i is None:
        return None
    prev = float(p.iloc[i - 1])
    if prev <= 0:
        return None
    out = {"reaction_date": idx[i].isoformat(), "reaction": float(p.iloc[i]) / prev - 1}
    for n in (5, 20):
        j = i + n - 1
        out[f"d{n}"] = float(p.iloc[j]) / prev - 1 if j < len(idx) else None
    out["continued"] = (None if out["d20"] is None else bool(np.sign(out["d20"]) == np.sign(out["reaction"])))
    return out


def history(session: Session, symbol: str, before: dt.date, k: int = HISTORY) -> dict:
    p = _prices(session, symbol)
    if p is None:
        return {"events": [], "summary": None}
    past = [(d, t) for d, t in report_dates(session, symbol) if d < before]
    events = []
    for d, t in sorted(past, reverse=True):
        m = moves_after(p, d, t)
        if m is not None and m["d20"] is not None:
            events.append({"date": d.isoformat(), "timing": t, **m})
        if len(events) >= k:
            break
    if not events:
        return {"events": [], "summary": None}
    r = np.array([e["reaction"] for e in events])
    d5 = np.array([e["d5"] for e in events])
    d20 = np.array([e["d20"] for e in events])
    cont = [e["continued"] for e in events if e["continued"] is not None]
    summary = {"n": len(events), "reaction_avg": float(r.mean()), "reaction_abs_avg": float(np.abs(r).mean()),
               "reaction_min": float(r.min()), "reaction_max": float(r.max()),
               "d5_avg": float(d5.mean()), "d5_min": float(d5.min()), "d5_max": float(d5.max()),
               "d20_avg": float(d20.mean()), "d20_min": float(d20.min()), "d20_max": float(d20.max()),
               "continued_share": (sum(cont) / len(cont)) if cont else None}
    return {"events": list(reversed(events)), "summary": summary}


# ------------------------------------------------------------------ family impact

def family_impact(session: Session, symbol: str, reaction: float | None, related: list[str]) -> dict:
    """Holders' shekel P&L from the report move, and expected moves of related family holdings."""
    from .factors import holding_returns, sector_of
    from .scenarios import books

    ports, family = books(session, None)
    base = services.get_settings(session).base_currency
    fx = services.fx_series(session)
    to_ils = float(fx.iloc[-1]) if base == "USD" and len(fx) else 1.0
    holders, exposure = [], 0.0
    for b in ports:
        h = next((x for x in b.holdings if x.symbol == symbol), None)
        if h is None:
            continue
        value_ils = h.value * to_ils
        exposure += value_ils
        # today's value is after the move: the move's P&L is value x r / (1 + r)
        pnl = value_ils * reaction / (1 + reaction) if reaction is not None else None
        holders.append({"id": b.pid, "name": b.name, "value_ils": value_ils, "pnl_ils": pnl, "weight": h.value / b.value})
    related_rows = []
    if reaction is not None:
        today = dt.date.today()
        rc = holding_returns(session, symbol, today).get("usd")
        sec = sector_of(session, symbol)
        for h in family.holdings:
            if h.symbol == symbol:
                continue
            listed = h.symbol in [s.upper() for s in related]
            same_sector = sec is not None and sector_of(session, h.symbol) == sec
            if not (listed or same_sector) or rc is None:
                continue
            rh = holding_returns(session, h.symbol, today).get("usd")
            if rh is None:
                continue
            df = pd.concat([rh, rc], axis=1, join="inner").dropna().tail(104)
            if len(df) < 26 or df.iloc[:, 1].var() <= 0:
                continue
            beta = float(df.cov().iloc[0, 1] / df.iloc[:, 1].var())
            move = beta * reaction
            if abs(move) < 0.001:
                continue
            related_rows.append({"symbol": h.symbol, "name": h.name, "why": "listed in the brief" if listed else "same sector",
                                 "beta": beta, "expected_move": move, "expected_ils": h.value * to_ils * move,
                                 "holders": [b.name for b in ports if any(x.symbol == h.symbol for x in b.holdings)]})
    related_rows.sort(key=lambda r: r["expected_ils"])
    total = sum(x["pnl_ils"] for x in holders if x["pnl_ils"] is not None)
    return {"holders": holders, "exposure_ils": exposure, "pnl_ils": total if holders else None, "related": related_rows}


# ------------------------------------------------------------------ import and journal

def import_brief(session: Session, brief: dict, today: dt.date | None = None) -> dict:
    errors = validate(brief)
    if errors:
        raise BriefError("\n".join(errors))
    day = dt.date.fromisoformat(brief["date"])
    n = 0
    for c in brief["companies"]:
        sym = c["symbol"].upper().strip()
        row = session.exec(select(EarningsItem).where(EarningsItem.date == day, EarningsItem.symbol == sym)).first() or EarningsItem(date=day, symbol=sym)
        row.data = json.dumps({**c, "symbol": sym}, ensure_ascii=False)
        o = c.get("outlook", {})
        row.confidence = o.get("confidence", "")
        row.days_direction = o.get("days_direction", "")
        row.weeks_direction = o.get("weeks_direction", "")
        row.base_close = row.base_date = None
        row.move_5d = row.move_20d = None
        row.score_5d = row.score_20d = row.score = ""
        row.imported_at = dt.datetime.now(dt.timezone.utc)
        session.add(row)
        n += 1
    session.commit()
    score_pending(session, today)
    return {"date": brief["date"], "companies": n}


def _grade(direction: str, move: float, n: int) -> str:
    band = BAND[n]
    if direction == "flat":
        return "hit" if abs(move) <= band else "mixed" if abs(move) <= 2 * band else "miss"
    sign = 1 if direction == "up" else -1
    if move * sign > band / 2:
        return "hit"
    if move * sign < -band / 2:
        return "miss"
    return "mixed"


def score_item(session: Session, item: EarningsItem, today: dt.date | None = None) -> None:
    """Fills the journal for one item, using only prices up to `today` and up to 20 sessions after the base."""
    today = today or dt.date.today()
    p = _prices(session, item.symbol)
    if p is None:
        return
    p = p[p.index <= today]
    data = json.loads(item.data)
    if item.base_close is None:
        i = reaction_index(list(p.index), item.date, data.get("reported", "after_close"))
        if i is None:
            return
        item.base_date, item.base_close = p.index[i], float(p.iloc[i])
    idx = list(p.index)
    if item.base_date not in idx:
        return
    i = idx.index(item.base_date)
    for n, attr, d_attr, s_attr in ((5, "move_5d", "days_direction", "score_5d"), (20, "move_20d", "weeks_direction", "score_20d")):
        if getattr(item, attr) is None and i + n < len(idx):
            setattr(item, attr, float(p.iloc[i + n]) / item.base_close - 1)
        if getattr(item, attr) is not None and getattr(item, d_attr):
            setattr(item, s_attr, _grade(getattr(item, d_attr), getattr(item, attr), n))
    scores = [s for s in (item.score_5d, item.score_20d) if s]
    if item.score_5d and item.score_20d:
        item.score = item.score_5d if item.score_5d == item.score_20d else "mixed"
    elif len(scores) == 1 and item.move_20d is None and not item.weeks_direction:
        item.score = scores[0]
    session.add(item)


def score_pending(session: Session, today: dt.date | None = None) -> None:
    for item in session.exec(select(EarningsItem).where(EarningsItem.score == "")).all():
        score_item(session, item, today)
    session.commit()


def journal(session: Session) -> dict:
    score_pending(session)
    items = session.exec(select(EarningsItem).order_by(EarningsItem.date.desc())).all()
    rows = []
    for it in items:
        d = json.loads(it.data)
        rows.append({"date": it.date.isoformat(), "symbol": it.symbol, "name": d.get("name", ""), "confidence": it.confidence,
                     "days_direction": it.days_direction, "weeks_direction": it.weeks_direction, "move_5d": it.move_5d, "move_20d": it.move_20d,
                     "score_5d": it.score_5d, "score_20d": it.score_20d, "score": it.score,
                     "scored": bool(it.days_direction or it.weeks_direction)})
    acc = {}
    for conf in ("low", "medium", "high"):
        sub = [r for r in rows if r["confidence"] == conf]
        def rate(key):
            s = [r[key] for r in sub if r[key]]
            return {"n": len(s), "hit": (sum(1 for x in s if x == "hit") / len(s)) if s else None}
        acc[conf] = {"d5": rate("score_5d"), "d20": rate("score_20d"), "overall": rate("score")}
    return {"rows": rows, "accuracy": acc, "bands": {"5": BAND[5], "20": BAND[20]}}


# ------------------------------------------------------------------ the tab

def dates(session: Session) -> list[str]:
    rows = session.exec(select(EarningsItem.date).distinct().order_by(EarningsItem.date.desc())).all()
    return [d.isoformat() for d in rows]


def brief_payload(session: Session, day: dt.date | None = None) -> dict:
    ds = dates(session)
    last = last_trading_day(session).isoformat()
    if not ds:
        return {"date": None, "dates": [], "companies": [], "last_trading_day": last}
    day = day or dt.date.fromisoformat(ds[0])
    score_pending(session)
    items = session.exec(select(EarningsItem).where(EarningsItem.date == day)).all()
    cards = []
    for it in items:
        c = json.loads(it.data)
        sym = it.symbol
        services.ensure_meta_info(session, [sym])
        p = _prices(session, sym)
        today = moves_after(p, day, c.get("reported", "after_close")) if p is not None else None
        hist = history(session, sym, day)
        impact = family_impact(session, sym, today["reaction"] if today else None, c.get("related_symbols") or [])
        r = c.get("results", {})
        beat = {k: (None if r.get(k) is None or r.get(f"{k}_est") is None else ("beat" if r[k] > r[f"{k}_est"] else "miss" if r[k] < r[f"{k}_est"] else "in line"))
                for k in ("revenue", "eps")}
        percentile = None
        if today and hist["events"]:
            past = np.abs([e["reaction"] for e in hist["events"]])
            percentile = float(np.mean(past <= abs(today["reaction"])))
        cards.append({"symbol": sym, "name": c.get("name") or sym, "data": c, "beat": beat, "today": today, "history": hist,
                      "bigger_than_share": percentile, "impact": impact,
                      "journal": {"score_5d": it.score_5d, "score_20d": it.score_20d, "score": it.score, "move_5d": it.move_5d, "move_20d": it.move_20d},
                      "rank": TOP100.index(sym) if sym in TOP100 else len(TOP100)})
    cards.sort(key=lambda x: (-x["impact"]["exposure_ils"], x["rank"]))
    return {"date": day.isoformat(), "dates": ds, "companies": cards, "last_trading_day": last}


def candidates(session: Session, day: dt.date) -> list[dict]:
    """Companies reporting on `day`: family holdings first (by value), then the largest S&P 500 names. Max 8."""
    from .scenarios import books

    _, family = books(session, None)
    metas = {m.ticker: m for m in session.exec(select(SymbolMeta)).all()}
    fam = [h for h in sorted(family.holdings, key=lambda h: -h.value) if (metas.get(h.symbol) is None or metas[h.symbol].type not in ("ETF", "Fund"))]
    out, seen = [], set()
    for sym, in_family in [(h.symbol, True) for h in fam] + [(s, False) for s in TOP100]:
        if sym in seen or len(out) >= MAX_CANDIDATES:
            continue
        seen.add(sym)
        hit = next(((d, t) for d, t in report_dates(session, sym) if d == day), None)
        if hit:
            m = metas.get(sym)
            out.append({"symbol": sym, "name": clean_name(m.name, sym) if m and m.name else sym, "family": in_family, "reported": hit[1],
                        "date": day.isoformat()})
    return out


def last_trading_day(session: Session, today: dt.date | None = None) -> dt.date:
    """The last completed US session before today, from SPY's prices (falls back to the last weekday)."""
    today = today or dt.date.today()
    p = _prices(session, "SPY")
    if p is not None:
        past = [d for d in p.index if d < today]
        if past:
            return past[-1]
    d = today - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def handle_inbox(session: Session, obj: dict) -> tuple[bool, str]:
    try:
        r = import_brief(session, obj)
    except BriefError as exc:
        return False, "Errors:\n- " + str(exc).replace("\n", "\n- ")
    return True, f"Imported the earnings brief for {r['date']} ({r['companies']} companies)."


def _register() -> None:
    from . import specs

    specs.HANDLERS["earnings_brief"] = handle_inbox


_register()
