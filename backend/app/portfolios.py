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
    "ticker": ["ticker", "symbol", "סימול", "סימבול", "נייר", "שם נייר", "מניה", "stock"],
    "quantity": ["quantity", "qty", "shares", "amount", "units", "כמות", "יחידות", "כמות יחידות"],
    "avg_price": ["avg_price", "avg price", "average price", "avg cost", "avg_cost", "average cost", "cost rate", "cost price",
                  "cost", "price", "שער ממוצע", "שער קנייה", "מחיר ממוצע", "שער עלות", "מחיר קנייה", "עלות ממוצעת"],
    "currency": ["currency", "מטבע"],
    "portfolio": ["portfolio", "תיק", "account", "חשבון"],
}
TOTAL_WORDS = {'סה"כ', "סה״כ", "total", "sum", "סיכום", "סהכ"}


def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower())


def _header_map(headers: list[str]) -> dict[str, int]:
    norm = [_norm(h) for h in headers]
    out = {}
    for key, names in ALIASES.items():
        for i, h in enumerate(norm):
            if h in names and i not in out.values():
                out[key] = i
                break
    # a header row with an empty first column holding the tickers (Excel "Stocks" layout)
    if "ticker" not in out and norm and norm[0] == "" and {"quantity", "avg_price"} <= out.keys():
        out["ticker"] = 0
    return out


def _excel_stock_tickers(data: bytes) -> dict[str, dict]:
    """Excel's "Stocks" data type shows as #VALUE! outside Excel. The ticker, name and currency are
    kept in the file's rich-data parts; map each such cell (e.g. "A3") to them."""
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        if "xl/richData/rdrichvalue.xml" not in names or "xl/metadata.xml" not in names:
            return {}
        ns = {"r": "http://schemas.microsoft.com/office/spreadsheetml/2017/richdata",
              "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        st_root = ET.fromstring(z.read("xl/richData/rdrichvaluestructure.xml"))
        structs = [(s.get("t"), [k.get("n") for k in s.findall("r:k", ns)]) for s in st_root.findall("r:s", ns)]
        rvs = []
        for rv in ET.fromstring(z.read("xl/richData/rdrichvalue.xml")).findall("r:rv", ns):
            t, keys = structs[int(rv.get("s"))]
            rvs.append((t, dict(zip(keys, [v.text for v in rv.findall("r:v", ns)]))))
        md = ET.fromstring(z.read("xl/metadata.xml"))
        fut = [b for f in md.findall("m:futureMetadata", ns) if f.get("name") == "XLRICHVALUE" for b in f.findall("m:bk", ns)]
        rvb = [int(b.find(".//r:rvb", ns).get("i")) for b in fut]
        vmeta = [int(b.find("m:rc", ns).get("v")) for b in md.find("m:valueMetadata", ns).findall("m:bk", ns)]
        sheet = next(n for n in sorted(names) if n.startswith("xl/worksheets/sheet"))
        cells = re.findall(r'<c r="([A-Z]+[0-9]+)"[^>]*?vm="(\d+)"', z.read(sheet).decode("utf-8", "replace"))
        out = {}
        for ref, vm in cells:
            t, d = rvs[rvb[vmeta[int(vm) - 1]]]
            if t == "_linkedentity" and "%cvi" in d:
                t, d = rvs[int(d["%cvi"])]
            sym = (d.get("Ticker symbol") or "").strip()
            if sym:
                exch = d.get("Exchange abbreviation") or ""
                sym = sym.replace(" ", "-")  # "BRK B" -> "BRK-B"
                if exch in ("XTAE", "TASE") and not sym.endswith(".TA"):
                    sym += ".TA"
                out[ref] = {"ticker": sym, "name": d.get("Name") or "", "currency": d.get("Currency") or "",
                            "type": d.get("Instrument type") or ""}
        return out
    except Exception:  # unusual files: fall back to plain cell values
        return {}


def read_table(filename: str, data: bytes) -> list[list[str]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        stocks = _excel_stock_tickers(data)
        wb = load_workbook(io.BytesIO(data), read_only=False, data_only=True)
        ws = wb.worksheets[0]
        rows = []
        for row in ws.iter_rows():
            vals = []
            for c in row:
                if c.coordinate in stocks:
                    vals.append(stocks[c.coordinate]["ticker"])
                else:
                    vals.append("" if c.value is None else str(c.value))
            rows.append(vals)
        read_table.stock_meta = {v["ticker"]: v for v in stocks.values()}  # names for SymbolMeta
        return rows
    read_table.stock_meta = {}
    text = data.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text))]


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").replace("₪", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _sections(rows: list[list[str]]) -> list[tuple[str | None, dict, list[tuple[int, list[str]]]]]:
    """Split a sheet into (portfolio name, header map, data rows). A lone text cell above a header
    row names the portfolio; a sheet with one table and no title gives one unnamed section."""
    out = []
    title = None
    hm = None
    cur: list = []
    for n, r in enumerate(rows, start=1):
        cells = [str(c or "").strip() for c in r]
        filled = [c for c in cells if c]
        if not filled:
            continue
        cand = _header_map(cells)
        if {"quantity", "avg_price"} <= cand.keys() and "ticker" in cand:
            if hm is not None:
                out.append((title, hm, cur))
            hm, cur = cand, []
            if "portfolio" in cand:
                title = None
            continue
        if len(filled) == 1 and _num(filled[0]) is None and len(filled[0]) <= 40 and filled[0].lower() not in TOTAL_WORDS:
            if hm is not None:
                out.append((title, hm, cur))
                hm, cur = None, []
            title = filled[0]
            continue
        if hm is not None:
            cur.append((n, cells))
    if hm is not None:
        out.append((title, hm, cur))
    return out


def parse_holdings(session: Session, rows: list[list[str]], as_of: dt.date) -> list[dict]:
    """Validate holdings: ticker, quantity, average price, optional currency and portfolio.
    Rows without a quantity (totals, notes, realized-gain lists) are skipped silently.
    Tel Aviv prices given in agorot (about 100x the current price) are converted to shekels."""
    sections = _sections([r for r in rows])
    if not sections:
        raise ValueError("Couldn't find the columns. Use a header row with ticker, quantity, avg_price (currency optional).")
    out = []
    price_cache: dict[str, dict | Exception] = {}
    for title, hm, data in sections:
        for n, r in data:
            get = lambda k: r[hm[k]] if k in hm and hm[k] < len(r) else ""
            ticker = get("ticker").strip().upper()
            qty, avg = _num(get("quantity")), _num(get("avg_price"))
            if not ticker or ticker.lower() in TOTAL_WORDS or ticker.startswith("#") or qty is None:
                continue
            res: dict = {"line": n, "ticker": ticker, "errors": {}, "notes": [], "portfolio": (get("portfolio").strip() or title)}
            if qty <= 0:
                res["errors"]["quantity"] = "Enter a quantity above 0."
            if avg is None or avg <= 0:
                res["errors"]["avg_price"] = "Enter an average price above 0."
            cur = (get("currency") or "").strip().upper().replace("ILA", "ILS").replace("NIS", "ILS").replace("₪", "ILS").replace("$", "USD")
            if not res["errors"]:
                if ticker not in price_cache:
                    try:
                        price_cache[ticker] = price_on(session, ticker, as_of)
                    except (NoPriceData, ValueError) as exc:
                        price_cache[ticker] = exc
                p = price_cache[ticker]
                if isinstance(p, Exception):
                    res["errors"]["ticker"] = str(p)
                else:
                    cur = cur if cur in ("USD", "ILS") else p["currency"] or guess_currency(ticker)
                    if ticker.endswith(".TA") and avg / p["close"] > 20:
                        avg = avg / 100
                        res["notes"].append("Average price looked like agorot, converted to shekels.")
                    res.update({"quantity": qty, "avg_price": avg, "currency": cur, "price": p["close"], "price_date": p["date"],
                                "gain_pct": p["close"] / avg - 1})
            out.append(res)
    return out


def import_holdings(session: Session, pid: int, rows: list[dict], as_of: dt.date, replace: bool = False) -> dict:
    """Each valid row becomes a BUY at that day's close, with the average price kept as cost basis.
    Rows naming a portfolio go there (created if needed); the rest go into `pid`.
    `replace` clears those portfolios' transactions first, for re-importing an updated file."""
    from .market.search import remember_selection

    by_name = {p.name.strip().lower(): p for p in list_portfolios(session)}
    targets: dict[int, list[dict]] = {}
    created = []
    for r in rows:
        if r["errors"]:
            continue
        name = (r.get("portfolio") or "").strip()
        if name:
            p = by_name.get(name.lower())
            if p is None:
                p = Portfolio(name=name[:60])
                session.add(p)
                session.commit()
                session.refresh(p)
                by_name[name.lower()] = p
                created.append(p.name)
            target = p.id
        else:
            target = pid
        targets.setdefault(target, []).append(r)
    meta = getattr(read_table, "stock_meta", {}) or {}
    n = 0
    for target, items in targets.items():
        if replace:
            session.exec(delete(Transaction).where(Transaction.portfolio_id == target))
        for r in items:
            m = meta.get(r["ticker"])
            if m and m.get("name"):
                remember_selection(session, {"symbol": r["ticker"], "name": m["name"].title() if m["name"].isupper() else m["name"],
                                             "type": "ETF" if m.get("type") == "ETF" else "Stock", "currency": r["currency"]})
            session.add(Transaction(portfolio_id=target, ticker=r["ticker"], type=TxType.BUY, date=dt.date.fromisoformat(r["price_date"]),
                                    quantity=r["quantity"], price=r["price"], currency=r["currency"],
                                    cost_price=r["avg_price"], note="Imported holding (average cost)"))
            n += 1
    session.commit()
    return {"imported": n, "portfolios": len(targets), "created": created}
