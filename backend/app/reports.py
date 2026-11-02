"""Short periodic summaries, per portfolio or for all of them. Hebrew text, ready to paste into
WhatsApp or to send from the Telegram bot."""
from __future__ import annotations

import datetime as dt

from sqlmodel import Session, select

from .engine.returns import base_index, twr
from .models import Portfolio, Recommendation
from .services import Context, cost_summary, get_settings, realized_summary

PERIODS = {"week": 7, "month": 30, "year": 365}
WORD = {"week": "השבוע", "month": "החודש", "year": "השנה"}


def _money(v: float, base: str) -> str:
    sym = "$" if base == "USD" else "₪"
    sign = "-" if v < 0 else ""
    return f"{sign}{sym}{abs(v):,.0f}"


def _pct(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{'+' if v > 0 else ''}{v * 100:.1f}%"


def portfolio_report(session: Session, pid: int, period: str = "week") -> dict:
    ctx = Context(session, pid)
    p = session.get(Portfolio, pid)
    name = p.name if p else "?"
    if ctx.series is None:
        return {"portfolio": name, "empty": True, "text": f"{name}: התיק ריק."}
    s = ctx.series
    e = len(s.dates) - 1
    first = min(t.date for t in ctx.txs)
    start = s.dates[e] - dt.timedelta(days=PERIODS.get(period, 7))
    b = max(base_index(s.dates, start), base_index(s.dates, first))
    ret = twr(s.ret, b, e)
    spy_px = ctx.bench_prices().get("SPY")
    spy = float(spy_px.iloc[e] / spy_px.iloc[b] - 1) if spy_px is not None and spy_px.iloc[b] > 0 else None
    cost = cost_summary(ctx)
    realized = realized_summary(ctx)
    change = float(s.value.iloc[e] - s.value.iloc[b] - s.flow.iloc[b + 1 : e + 1].sum())
    contrib = s.contribution.iloc[b + 1 : e + 1].sum().sort_values()
    movers = [(t, float(v)) for t, v in contrib.items() if abs(v) > 1e-6]
    since = "" if s.dates[b] <= start else f" (מאז {s.dates[b].strftime('%d/%m')})"
    lines = [f"📊 {name} · {WORD.get(period, period)}{since}",
             f"שווי: {_money(float(s.value.iloc[e]), ctx.base)} ({_money(change, ctx.base) if change < 0 else '+' + _money(change, ctx.base)})",
             f"תשואה: {_pct(ret)} · S&P 500: {_pct(spy)}"]
    if spy is not None:
        diff = (ret - spy) * 100
        lines.append(("✅ לפני המדד ב-" if diff >= 0 else "🔻 אחרי המדד ב-") + f"{abs(diff):.1f} נקודות")
    lines.append(f"רווח מול שער ממוצע: {_money(cost['gain'], ctx.base)} ({_pct(cost['gain_pct'])})")
    if abs(realized["total"]) > 0.5:
        lines.append(f"רווח ממומש: {_money(realized['total'], ctx.base)}")
    if movers:
        best, worst = movers[-1], movers[0]
        if best[1] > 0:
            lines.append(f"תרם הכי הרבה: {best[0]} ({best[1] * 100:+.1f} נק')")
        if worst[1] < 0:
            lines.append(f"פגע הכי הרבה: {worst[0]} ({worst[1] * 100:+.1f} נק')")
    return {"portfolio": name, "empty": False, "return": ret, "spy": spy, "value": float(s.value.iloc[e]), "text": "\n".join(lines)}


def all_report(session: Session, period: str = "week") -> dict:
    parts, rows = [], []
    for p in session.exec(select(Portfolio).order_by(Portfolio.id)).all():
        r = portfolio_report(session, p.id, period)
        if r["empty"]:
            continue
        rows.append(r)
    if not rows:
        return {"text": "אין עדיין תיקים עם אחזקות."}
    base = get_settings(session).base_currency
    total = sum(r["value"] for r in rows)
    spy = next((r["spy"] for r in rows if r["spy"] is not None), None)
    beat = sum(1 for r in rows if r["spy"] is not None and r["return"] >= r["spy"])
    parts.append(f"📈 סיכום {WORD.get(period, period)} · {len(rows)} תיקים · {_money(total, base)}")
    parts.append(f"S&P 500: {_pct(spy)} · {beat} מתוך {len(rows)} לפני המדד")
    parts.append("")
    for r in sorted(rows, key=lambda r: -(r["return"] or 0)):
        mark = "✅" if r["spy"] is not None and r["return"] >= r["spy"] else "🔻"
        parts.append(f"{mark} {r['portfolio']}: {_pct(r['return'])}")
    since = dt.date.today() - dt.timedelta(days=PERIODS.get(period, 7))
    calls = session.exec(select(Recommendation).where(Recommendation.date >= since)).all()
    if calls:
        parts.append("")
        parts.append("המלצות בתקופה: " + ", ".join(f"{'קנייה' if c.action == 'BUY' else 'מכירה'} {c.ticker}" for c in calls))
    return {"text": "\n".join(parts)}
