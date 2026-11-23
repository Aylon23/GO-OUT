"""Compare the prices saved in the app with Yahoo's, for one ticker. Read-only: changes nothing.

    .venv\\Scripts\\python tools\\check_prices.py SPY 2026-10-30
"""
import sqlite3
import sys
from pathlib import Path

import yfinance as yf

ticker = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
start = sys.argv[2] if len(sys.argv) > 2 else "2026-10-30"
db = Path(__file__).resolve().parent.parent / "data" / "portfolio.db"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = con.execute("SELECT date, close, adj_close FROM pricecache WHERE ticker=? AND date>=? ORDER BY date", (ticker, start)).fetchall()
saved = {d: c for d, c, _ in rows}
adj = {d: a for d, _, a in rows}
cov = con.execute("SELECT * FROM pricecoverage WHERE ticker=?", (ticker,)).fetchone()
print("coverage:", cov)
live = yf.Ticker(ticker).history(start=start, auto_adjust=False)
yahoo = {d.date().isoformat(): float(c) for d, c in live["Close"].items()}
print(f"{'date':<12}{'app':>10}{'yahoo':>10}{'app adj':>10}{'adj/close':>11}")
for d in sorted(set(saved) | set(yahoo)):
    a, y = saved.get(d), yahoo.get(d)
    flag = "" if a is not None and y is not None and abs(a - y) < 0.01 else "   <-- differs"
    r = adj.get(d)
    print(f"{d:<12}{round(a, 2) if a is not None else '-':>10}{round(y, 2) if y is not None else '-':>10}{round(r, 2) if r is not None else '-':>10}{round(r / a, 4) if r and a else '-':>11}{flag}")
if saved:
    v = list(saved.values())
    print(f"\napp: {v[0]:.2f} -> {v[-1]:.2f} = {(v[-1] / v[0] - 1) * 100:+.2f}%")
if adj:
    v = list(adj.values())
    print(f"app adj (what the chart uses): {v[0]:.2f} -> {v[-1]:.2f} = {(v[-1] / v[0] - 1) * 100:+.2f}%")
if yahoo:
    v = list(yahoo.values())
    print(f"yahoo: {v[0]:.2f} -> {v[-1]:.2f} = {(v[-1] / v[0] - 1) * 100:+.2f}%")
