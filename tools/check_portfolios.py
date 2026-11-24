"""Check each portfolio's return "since tracking began" against a separate, simple calculation.

Read-only: it works on a temporary copy of the database, so nothing you have is changed.

    .venv\\Scripts\\python tools\\check_portfolios.py

For every portfolio it prints what the app shows and what this script gets on its own from the trades and
the closing prices (time-weighted, stock performance only, dividends included, currency moves left out),
and marks a gap above 0.3 points.

It reads the trades and prices straight from the database file and does its own day-by-day arithmetic,
without the app's calculation code, so a mistake there shows up as a gap here.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("PORTFOLIO_DB", ROOT / "data" / "portfolio.db"))
GAP = 0.3  # percentage points


def app_numbers(db: Path) -> dict[int, dict]:
    os.environ["PORTFOLIO_DB"] = str(db)
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlmodel import Session

    from app import db as appdb
    from app.portfolios import overview_payload

    with Session(appdb.get_engine()) as s:
        o = overview_payload(s, "MAX")
    return {r["id"]: r for r in o["rows"] if not r.get("empty")}


def own_numbers(db: Path) -> dict[int, dict]:
    con = sqlite3.connect(db)
    base = (con.execute("SELECT base_currency FROM settings LIMIT 1").fetchone() or ["ILS"])[0]
    fx_row = con.execute("SELECT close FROM pricecache WHERE ticker='ILS=X' ORDER BY date DESC LIMIT 1").fetchone()
    ils_per_usd = fx_row[0] if fx_row else None

    def rate(ccy: str) -> float | None:  # one fixed rate: currency moves are left out
        if ccy == base:
            return 1.0
        if ils_per_usd is None:
            return None
        if ccy == "USD" and base == "ILS":
            return ils_per_usd
        if ccy == "ILS" and base == "USD":
            return 1 / ils_per_usd
        return None

    splits = defaultdict(list)
    for t, d, r in con.execute("SELECT ticker, date, ratio FROM splitevent"):
        splits[t].append((d, r))
    out = {}
    pids = [p for (p,) in con.execute("SELECT DISTINCT portfolio_id FROM \"transaction\"")]
    for pid in pids:
        txs = con.execute("SELECT date, type, ticker, quantity, price, currency, fees FROM \"transaction\" WHERE portfolio_id=? ORDER BY date, id",
                          (pid,)).fetchall()
        kind = lambda t: str(t[1]).upper().split(".")[-1]
        trades = [t for t in txs if kind(t) in ("BUY", "SELL")]
        if not trades:
            continue
        has_cash = any(kind(t) in ("DEPOSIT", "WITHDRAWAL") for t in txs)
        recorded_div = {t[2].upper() for t in txs if kind(t) == "DIVIDEND"}
        tickers = sorted({t[2].upper() for t in trades})
        px, div, ccy, skipped = {}, {}, {}, []
        for tk in tickers:
            rows = con.execute("SELECT date, close, dividend FROM pricecache WHERE ticker=? ORDER BY date", (tk,)).fetchall()
            c = next(t[5] for t in trades if t[2].upper() == tk)
            if not rows or rate(c) is None:
                skipped.append(tk)
                continue
            px[tk] = {d: cl for d, cl, _ in rows}
            div[tk] = {d: dv for d, _, dv in rows if dv}
            ccy[tk] = c
        first = min(t[0] for t in trades)
        days = sorted({d for tk in px for d in px[tk] if d >= min(t[0] for t in txs)})
        order = {"DEPOSIT": 0, "WITHDRAWAL": 1, "DIVIDEND": 2, "SELL": 3, "BUY": 4, "FEE": 5}
        by_day = defaultdict(list)
        for t in sorted(txs, key=lambda t: (t[0], order.get(kind(t), 9))):
            i = next((k for k, d in enumerate(days) if d >= t[0]), None)  # a trade on a closed day counts on the next open one
            if i is not None:
                by_day[days[i]].append(t)
        qty, last_px, cash, prev_v, growth = defaultdict(float), {}, 0.0, 0.0, 1.0
        conv = lambda amount, c: amount * (rate(c) or 0.0)
        for d in days:
            for tk in px:
                if d in px[tk]:
                    last_px[tk] = px[tk][d]
            f = 0.0
            for tk in px:  # automatic dividends on shares held before today's trades
                if tk not in recorded_div and div[tk].get(d) and qty[tk] > 0:
                    cash += conv(qty[tk] * div[tk][d], ccy[tk])
            for date, typ, tk, q, price, c, fees in by_day.get(d, []):
                k, tk = str(typ).upper().split(".")[-1], (tk or "").upper()
                if k == "DEPOSIT":
                    cash += conv(price, c); f += conv(price, c)
                elif k == "WITHDRAWAL":
                    cash -= conv(price, c); f -= conv(price, c)
                elif k == "DIVIDEND":
                    cash += conv(price - fees, c)
                elif k == "FEE":
                    cash -= conv(price + fees, c)
                elif k in ("BUY", "SELL") and tk in px:
                    sf = 1.0
                    for sd, r in splits.get(tk, []):
                        if sd > date:
                            sf *= r
                    gross, fee = conv(q * price, c), conv(fees, c)
                    if k == "BUY":
                        qty[tk] += q * sf; cash -= gross + fee
                    else:
                        qty[tk] -= q * sf; cash += gross - fee
            if cash < -1e-9:
                f += -cash  # money that must have come in to pay for the buys
                cash = 0.0
            v = sum(qty[tk] * last_px.get(tk, 0.0) * rate(ccy[tk]) for tk in px) + cash
            denom = prev_v if prev_v > 1e-9 else f
            if d > first and denom > 1e-9:  # measured from the close of the first trade day
                growth *= 1 + (v - f - prev_v) / denom
            prev_v = v
        out[pid] = {"return": growth - 1, "has_cash": has_cash, "skipped": skipped, "days": len(days)}
    return out


def main() -> None:
    if not SRC.exists():
        sys.exit(f"No database at {SRC}")
    tmp = Path(tempfile.mkdtemp()) / "copy.db"
    shutil.copy2(SRC, tmp)
    app = app_numbers(tmp)
    own = own_numbers(tmp)
    print(f"{'portfolio':<16}{'app':>9}{'check':>9}{'gap':>8}")
    bad = 0
    for pid, r in app.items():
        a, o = r.get("since_return"), own.get(pid)
        if a is None or o is None:
            print(f"{r['name']:<16}{'-':>9}{'-':>9}   not enough data")
            continue
        gap = (a - o["return"]) * 100
        note = []
        if abs(gap) > GAP:
            bad += 1
            note.append("<-- differs")
        if o["skipped"]:
            note.append("left out: " + ", ".join(o["skipped"]))
        print(f"{r['name']:<16}{a * 100:>8.2f}%{o['return'] * 100:>8.2f}%{gap:>+7.2f}   {'; '.join(note)}")
    print("\nAll match." if not bad else f"\n{bad} portfolio(s) differ by more than {GAP} points: send this output.")


if __name__ == "__main__":
    main()
