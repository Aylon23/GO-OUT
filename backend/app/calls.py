"""Recommendations ("calls") and the scorecard: how good were my calls compared with the S&P 500?

- A BUY call is measured from the close on its date until today, or until a later SELL call on
  the same ticker closes it. Return is total return (dividends included), in the base currency.
- A SELL call is measured after the date: if the stock then trailed the S&P 500, selling was right.
- Edge = call return minus SPY over exactly the same days. A BUY call "worked" when edge > 0;
  a SELL call "worked" when the stock did worse than SPY afterwards.
"""
from __future__ import annotations

import datetime as dt
import statistics

from sqlmodel import Session, select

from .engine.series import align, total_return_in_base
from .market.cache import NoPriceData, price_on
from .models import Portfolio, Recommendation, Transaction
from .services import _f, fx_series, get_settings, market_data

REF = "SPY"


def create_call(session: Session, ticker: str, action: str, date: dt.date, thesis: str = "", target: float | None = None) -> Recommendation:
    ticker, action = ticker.upper().strip(), action.upper()
    if action not in ("BUY", "SELL"):
        raise ValueError("A call is BUY or SELL.")
    p = price_on(session, ticker, date)  # latest close on or before the date; raises if no prices
    # the call keeps the date it was made; its price is the latest close available then
    rec = Recommendation(ticker=ticker, action=action, date=date, price=p["close"], thesis=thesis.strip(), target=target)
    session.add(rec)
    if action == "SELL":  # selling closes any open BUY call on the same ticker
        for open_buy in session.exec(select(Recommendation).where(Recommendation.ticker == ticker, Recommendation.action == "BUY",
                                                                    Recommendation.closed_date == None)).all():  # noqa: E711
            if open_buy.date <= rec.date:
                open_buy.closed_date = rec.date
                session.add(open_buy)
    session.commit()
    session.refresh(rec)
    return rec


def _window_return(session: Session, ticker: str, start: dt.date, end: dt.date | None, base: str, fx) -> tuple[float | None, float | None, dt.date | None]:
    """Total return of `ticker` and of SPY from the close on `start` to the close on `end` (or the latest)."""
    md, ref = market_data(session, ticker), market_data(session, REF)
    last = min(md.frame.index.max(), ref.frame.index.max())
    end = min(end, last) if end else last
    cal = sorted({d for d in list(md.frame.index) + list(ref.frame.index) if start <= d <= end} | {start})
    if len(cal) < 2:
        return 0.0, 0.0, end
    a = total_return_in_base(md, fx, cal, base)
    b = total_return_in_base(ref, fx, cal, base)
    r = float(a.iloc[-1] / a.iloc[0] - 1) if a.iloc[0] > 0 else None
    s = float(b.iloc[-1] / b.iloc[0] - 1) if b.iloc[0] > 0 else None
    return r, s, end


def call_rows(session: Session) -> list[dict]:
    settings = get_settings(session)
    base = settings.base_currency
    fx = fx_series(session)
    names = {p.id: p.name for p in session.exec(select(Portfolio)).all()}
    rows = []
    for rec in session.exec(select(Recommendation).order_by(Recommendation.date.desc(), Recommendation.id.desc())).all():
        followers = session.exec(select(Transaction).where(Transaction.rec_id == rec.id)).all()
        row = {"id": rec.id, "ticker": rec.ticker, "action": rec.action, "date": rec.date.isoformat(), "price": _f(rec.price),
               "thesis": rec.thesis, "target": _f(rec.target), "closed_date": rec.closed_date.isoformat() if rec.closed_date else None,
               "portfolios": sorted({names.get(t.portfolio_id, "?") for t in followers}),
               "return": None, "spy": None, "edge": None, "worked": None, "days": None, "error": None}
        try:
            r, s, end = _window_return(session, rec.ticker, rec.date, rec.closed_date if rec.action == "BUY" else None, base, fx)
        except NoPriceData as exc:
            row["error"] = str(exc)
            rows.append(row)
            continue
        row["days"] = (end - rec.date).days if end else None
        row["return"], row["spy"] = _f(r), _f(s)
        if r is not None and s is not None:
            # BUY: beat the market while held. SELL: the stock did worse than the market after you sold.
            edge = (r - s) if rec.action == "BUY" else (s - r)
            row["edge"] = _f(edge)
            row["worked"] = edge > 0
        if rec.target and rec.action == "BUY":
            last = float(align(market_data(session, rec.ticker).frame["close"], [dt.date.today()]).iloc[0])
            row["to_target"] = _f(rec.target / last - 1) if last else None
        rows.append(row)
    return rows


def scorecard(rows: list[dict]) -> dict:
    def summarize(rs: list[dict]) -> dict:
        scored = [r for r in rs if r["edge"] is not None and (r["days"] or 0) >= 1]
        edges = [r["edge"] for r in scored]
        out = {"count": len(rs), "scored": len(scored), "worked": sum(1 for r in scored if r["worked"]),
               "hit_rate": _f(sum(1 for r in scored if r["worked"]) / len(scored)) if scored else None,
               "avg_edge": _f(statistics.fmean(edges)) if edges else None,
               "median_edge": _f(statistics.median(edges)) if edges else None,
               "best": None, "worst": None}
        if scored:
            best = max(scored, key=lambda r: r["edge"])
            worst = min(scored, key=lambda r: r["edge"])
            out["best"] = {"id": best["id"], "ticker": best["ticker"], "edge": best["edge"]}
            out["worst"] = {"id": worst["id"], "ticker": worst["ticker"], "edge": worst["edge"]}
        return out

    buys = [r for r in rows if r["action"] == "BUY"]
    held = [r["days"] for r in buys if r["closed_date"] and r["days"] is not None]
    return {"all": summarize(rows), "buy": summarize(buys), "sell": summarize([r for r in rows if r["action"] == "SELL"]),
            "avg_days_held": _f(statistics.fmean(held)) if held else None}


def calls_payload(session: Session) -> dict:
    rows = call_rows(session)
    return {"base": get_settings(session).base_currency, "reference": REF, "rows": rows, "scorecard": scorecard(rows)}
