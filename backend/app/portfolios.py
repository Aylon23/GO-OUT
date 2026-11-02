"""Several portfolios: CRUD, importing a holdings list, the look-back simulation of current
holdings, and the all-portfolios comparison."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re

import pandas as pd
from sqlmodel import Session, delete, select

from .engine import risk
from .engine.returns import base_index, range_start, rebased
from .engine.series import align, fx_factor, total_return_in_base
from .market.cache import NoPriceData, currency_for, guess_currency, price_on
from .models import Portfolio, Transaction, TxType
from .services import Context, _clean, _f, _sample_positions, cost_summary, market_data, open_positions

# ------------------------------------------------------------------ CRUD


def list_portfolios(session: Session) -> list[Portfolio]:
    return list(session.exec(select(Portfolio).order_by(Portfolio.id)).all())


def portfolio_dict(p: Portfolio, n_tx: int = 0) -> dict:
    return {"id": p.id, "name": p.name, "note": p.note, "transactions": n_tx}


def delete_portfolio(session: Session, pid: int) -> None:
    session.exec(delete(Transaction).where(Transaction.portfolio_id == pid))
    p = session.get(Portfolio, pid)
    if p:
        session.delete(p)
    session.commit()


# ------------------------------------------------------------------ positions


# ------------------------------------------------------------------ look-back simulation


def lookback(session: Session, ctx: Context, code: str, start: str | None = None, end: str | None = None) -> dict:
    """How today's holdings, held unchanged (same number of shares, dividends reinvested),
    would have done over the range, next to the benchmarks. A simulation, not real results."""
    positions = open_positions(ctx)
    if not positions:
        return {"empty": True}
    ctx.load_market()
    mds = {t: ctx.market[t] for t in positions}
    frames = [m.frame for m in mds.values()] + [m.frame for m in ctx.bench_md.values()]
    last = min(f.index.max() for f in frames)
    first_common = max(m.frame.index.min() for m in mds.values())
    if code.upper() == "CUSTOM" and start:
        req = dt.date.fromisoformat(start)
    else:
        req = range_start(code if code.upper() != "CUSTOM" else "MAX", last, first_common)
    clipped = req < first_common
    s0 = max(req, first_common)
    e0 = dt.date.fromisoformat(end) if end else last
    cal = sorted({d for f in frames for d in f.index if d <= e0})
    b = base_index(cal, s0)
    cal = cal[b:]
    if len(cal) < 2:
        raise ValueError("Not enough price history for this range.")
    value = pd.Series(0.0, index=cal)
    for t, q in positions.items():
        value = value + q * total_return_in_base(mds[t], ctx.fx, cal, ctx.base)
    # scale so the end value equals today's market value of these shares (adj_close is back-adjusted)
    now_value = sum(q * float(align(mds[t].frame["close"], [cal[-1]]).iloc[0]) * float(fx_factor(mds[t].currency, ctx.base, align(ctx.fx, [cal[-1]])).iloc[0])
                    for t, q in positions.items())
    if value.iloc[-1] > 0:
        value = value * (now_value / value.iloc[-1])
    e = len(cal) - 1
    perf = {"portfolio": rebased(value, 0, e)}
    stats_idx = {"portfolio": value}
    for k, md in ctx.bench_md.items():
        p = total_return_in_base(md, ctx.fx, cal, ctx.base)
        perf[k] = rebased(p, 0, e)
        stats_idx[k] = p
    dd = {k: risk.drawdown_series(v / 100.0) * 100.0 for k, v in perf.items()}
    pos = _sample_positions(0, e)
    spy = stats_idx.get("SPY")
    return {
        "empty": False,
        "base": ctx.base,
        "range": {"code": code.upper(), "start": cal[0].isoformat(), "end": cal[-1].isoformat()},
        "clipped": clipped,
        "first_common": first_common.isoformat(),
        "holdings": len(positions),
        "summary": {
            "return": _f(perf["portfolio"].iloc[-1] / 100 - 1),
            "benchmarks": [{"symbol": k, "twr": _f(perf[k].iloc[-1] / 100 - 1)} for k in ctx.bench_md],
            "start_value": _f(value.iloc[0]),
            "end_value": _f(value.iloc[-1]),
        },
        "years": _year_rows(stats_idx),
        "monthly": {k: _f(v) for k, v in risk.monthly_returns(value).items()},
        "columns": list(stats_idx),
        "stats": _clean({k: risk.stats(v, ctx.settings.risk_free, spy) for k, v in stats_idx.items()}),
        "series": {
            "dates": [cal[i].isoformat() for i in pos],
            "value": [_f(value.iloc[i]) for i in pos],
            "invested": [None for _ in pos],
            "perf": {k: [_f(v.iloc[i]) for i in pos] for k, v in perf.items()},
            "drawdown": {k: [_f(v.iloc[i]) for i in pos] for k, v in dd.items()},
        },
    }


def _year_rows(series: dict[str, pd.Series]) -> list[dict]:
    years = {k: risk.calendar_year_returns(v) for k, v in series.items()}
    all_years = sorted({y for d in years.values() for y in d})
    return [{"year": y, **{k: _f(years[k].get(y)) for k in years}} for y in all_years]


def lookback_payload(session: Session, code: str, start: str | None, end: str | None, pid: int) -> dict:
    ctx = Context(session, pid)
    out = lookback(session, ctx, code, start, end)
    if not out.get("empty"):
        out["cost"] = _clean(cost_summary(ctx))
    return out


# ------------------------------------------------------------------ all portfolios


def overview_payload(session: Session, code: str = "1Y") -> dict:
    rows = []
    chart_series: dict[str, pd.Series] = {}
    bench_series: dict[str, pd.Series] = {}
    bench_ret: dict[str, float | None] = {}
    base = None
    for p in list_portfolios(session):
        ctx = Context(session, p.id)
        base = ctx.base
        row = {"id": p.id, "name": p.name, "note": p.note, "empty": ctx.series is None}
        if ctx.series is None:
            rows.append(row)
            continue
        s = ctx.series
        e = len(s.dates) - 1
        cost = cost_summary(ctx)
        first_trade = min(t.date for t in ctx.txs)
        row.update({
            "value": _f(s.value.iloc[e]),
            "cash": _f(s.cash.iloc[e]),
            "cost_gain": _f(cost["gain"]),
            "cost_gain_pct": _f(cost["gain_pct"]),
            "today_pct": _f(s.ret.iloc[e]) if e > 0 else None,
            "tracking_since": first_trade.isoformat(),
            "tracking_return": _f(s.index().iloc[e] / s.index().iloc[0] - 1),
            "holdings": len(open_positions(ctx)),
        })
        try:
            lb = lookback(session, ctx, code)
        except (ValueError, NoPriceData) as exc:
            lb = {"empty": True, "error": str(exc)}
        if not lb.get("empty"):
            row["lookback_return"] = lb["summary"]["return"]
            row["lookback_start"] = lb["range"]["start"]
            row["lookback_clipped"] = lb["clipped"]
            dates = [dt.date.fromisoformat(d) for d in lb["series"]["dates"]]
            chart_series[f"p{p.id}"] = pd.Series(lb["series"]["perf"]["portfolio"], index=dates)
            for bm in lb["summary"]["benchmarks"]:
                bench_ret.setdefault(bm["symbol"], bm["twr"])
            if not bench_series:
                for k in ctx.bench_md:
                    bench_series[k] = pd.Series(lb["series"]["perf"][k], index=dates)
        rows.append(row)
    # chart on the union calendar; each portfolio is rebased to 100 at its own start
    all_dates = sorted({d for s in list(chart_series.values()) + list(bench_series.values()) for d in s.index})
    perf = {}
    for k, ser in {**chart_series, **bench_series}.items():
        aligned = ser.reindex(all_dates).ffill()
        perf[k] = [_f(v) for v in aligned]
    total = sum(r.get("value") or 0 for r in rows)
    return {
        "base": base or "ILS",
        "range": code.upper(),
        "total_value": _f(total),
        "rows": rows,
        "benchmarks": [{"symbol": k, "twr": v} for k, v in bench_ret.items()],
        "chart": {"dates": [d.isoformat() for d in all_dates], "perf": perf,
                  "labels": {f"p{r['id']}": r["name"] for r in rows}},
    }


# ------------------------------------------------------------------ holdings import

ALIASES = {
    "ticker": ["ticker", "symbol", "סימול", "סימבול", "נייר", "שם נייר", "מניה", "name"],
    "quantity": ["quantity", "qty", "shares", "כמות", "יחידות", "כמות יחידות"],
    "avg_price": ["avg_price", "avg price", "average price", "avg cost", "avg_cost", "average cost", "cost", "price", "שער ממוצע", "שער קנייה", "מחיר ממוצע",
                  "שער עלות", "מחיר קנייה", "עלות ממוצעת"],
    "currency": ["currency", "מטבע"],
}


def _header_map(headers: list[str]) -> dict[str, int]:
    norm = [re.sub(r"\s+", " ", (h or "").strip().lower()) for h in headers]
    out = {}
    for key, names in ALIASES.items():
        for i, h in enumerate(norm):
            if h in names:
                out[key] = i
                break
    return out


def read_table(filename: str, data: bytes) -> list[list[str]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [["" if c is None else str(c) for c in row] for row in ws.iter_rows(values_only=True)]
    text = data.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text))]


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").replace("₪", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_holdings(session: Session, rows: list[list[str]], as_of: dt.date) -> list[dict]:
    """Validate a holdings list: ticker, quantity, average price, optional currency.
    Tel Aviv prices given in agorot (about 100x the current price) are converted to shekels."""
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []
    hm = _header_map(rows[0])
    missing = [k for k in ("ticker", "quantity", "avg_price") if k not in hm]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing) + ". Use ticker, quantity, avg_price, currency.")
    out = []
    for n, r in enumerate(rows[1:], start=2):
        get = lambda k: r[hm[k]] if k in hm and hm[k] < len(r) else ""
        ticker = get("ticker").strip().upper()
        res: dict = {"line": n, "ticker": ticker, "errors": {}, "notes": []}
        qty, avg = _num(get("quantity")), _num(get("avg_price"))
        if not ticker:
            res["errors"]["ticker"] = "Missing ticker."
        if qty is None or qty <= 0:
            res["errors"]["quantity"] = "Enter a quantity above 0."
        if avg is None or avg <= 0:
            res["errors"]["avg_price"] = "Enter an average price above 0."
        cur = (get("currency") or "").strip().upper().replace("ILA", "ILS").replace("NIS", "ILS").replace("₪", "ILS").replace("$", "USD")
        if ticker and not res["errors"]:
            try:
                p = price_on(session, ticker, as_of)
            except (NoPriceData, ValueError) as exc:
                res["errors"]["ticker"] = str(exc)
            else:
                cur = cur if cur in ("USD", "ILS") else p["currency"] or guess_currency(ticker)
                if ticker.endswith(".TA") and avg / p["close"] > 20:
                    avg = avg / 100
                    res["notes"].append("Average price looked like agorot, converted to shekels.")
                res.update({"quantity": qty, "avg_price": avg, "currency": cur, "price": p["close"], "price_date": p["date"],
                            "gain_pct": p["close"] / avg - 1})
        out.append(res)
    return out


def import_holdings(session: Session, pid: int, rows: list[dict], as_of: dt.date) -> int:
    """Each valid row becomes a BUY at that day's close, with the average price kept as cost basis."""
    n = 0
    for r in rows:
        if r["errors"]:
            continue
        session.add(Transaction(portfolio_id=pid, ticker=r["ticker"], type=TxType.BUY, date=dt.date.fromisoformat(r["price_date"]),
                                quantity=r["quantity"], price=r["price"], currency=r["currency"],
                                cost_price=r["avg_price"], note="Imported holding (average cost)"))
        n += 1
    session.commit()
    return n
