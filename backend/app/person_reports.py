"""Weekly and monthly reports per person, and a family summary, as a phone-sized image card and a short PDF.

The numbers come from the same functions as the app's screens for the same date range (portfolio and
holdings payloads with a custom range): stock performance only, amounts in the display currency. Reports are made
every Sunday (the week before) and on the 1st (the month before) while the app runs; missed ones are made on
the next start. They go to me only: listed in the app and sent to my Telegram chat. Never to anyone else.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path

from sqlmodel import Session, select

from . import db, logos, render, services
from .models import PersonReport, Portfolio, Transaction

log = logging.getLogger(__name__)
CATCH_UP_WEEKS = 4
CATCH_UP_MONTHS = 2


def reports_dir() -> Path:
    d = db.DB_PATH.parent / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ periods

def week_period(sunday: dt.date) -> tuple[dt.date, dt.date]:
    """The report made on `sunday` covers the 7 days before it (Sunday to Saturday)."""
    return sunday - dt.timedelta(days=7), sunday - dt.timedelta(days=1)


def month_period(first: dt.date) -> tuple[dt.date, dt.date]:
    end = first - dt.timedelta(days=1)
    return end.replace(day=1), end


def due(today: dt.date, last_weekly: str, last_monthly: str) -> list[tuple[str, dt.date, dt.date]]:
    """Report periods that should exist by today and don't yet (oldest first), with catch-up limits."""
    out = []
    sunday = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    sundays = [sunday - dt.timedelta(weeks=k) for k in range(CATCH_UP_WEEKS)]
    for s in reversed(sundays):
        start, end = week_period(s)
        if not last_weekly or end > dt.date.fromisoformat(last_weekly):
            out.append(("weekly", start, end))
    first = today.replace(day=1)
    firsts = [first]
    for _ in range(CATCH_UP_MONTHS - 1):
        firsts.append((firsts[-1] - dt.timedelta(days=1)).replace(day=1))
    for f in reversed(firsts):
        start, end = month_period(f)
        if not last_monthly or end > dt.date.fromisoformat(last_monthly):
            out.append(("monthly", start, end))
    return out


# ------------------------------------------------------------------ the data

def person_data(session: Session, pid: int, start: dt.date, end: dt.date, top_scenario: dict | None = None) -> dict | None:
    from .calls import call_rows
    from .services import Context, holdings_payload, portfolio_payload

    p = session.get(Portfolio, pid)
    pp = portfolio_payload(session, "CUSTOM", start.isoformat(), end.isoformat(), pid)
    if pp.get("empty"):
        return None
    hp = holdings_payload(session, "CUSTOM", start.isoformat(), end.isoformat(), pid)
    base = pp["base"]
    fx = services.fx_series(session)
    fx_end = float(fx[fx.index <= end].iloc[-1]) if len(fx[fx.index <= end]) else float(fx.iloc[-1])
    to_amt = 1.0  # amounts in the display currency (today's rate only converts how they're shown)
    s = pp["summary"]
    bench = {b["symbol"]: b["twr"] for b in s["benchmarks"]}
    rows = [r for r in hp["rows"] if r.get("contribution") is not None and not r["closed"]]
    rows.sort(key=lambda r: r["contribution"])
    start_value = s["start_value"] or 0.0
    top = [{"symbol": r["ticker"], "name": r["name"], "return": r["range_return"], "contribution": r["contribution"],
            "change_amt": r["contribution"] * start_value * to_amt} for r in reversed(rows[-3:]) if r["contribution"] > 0]
    bottom = [{"symbol": r["ticker"], "name": r["name"], "return": r["range_return"], "contribution": r["contribution"],
               "change_amt": r["contribution"] * start_value * to_amt} for r in rows[:3] if r["contribution"] < 0]
    txs = session.exec(select(Transaction).where(Transaction.portfolio_id == pid, Transaction.date >= start, Transaction.date <= end)
                       .order_by(Transaction.date)).all()
    trades = [{"date": t.date.isoformat(), "type": t.type.value if hasattr(t.type, "value") else t.type, "ticker": t.ticker,
               "quantity": t.quantity, "price": t.price, "currency": t.currency} for t in txs if (t.type.value if hasattr(t.type, "value") else t.type) in ("BUY", "SELL")]
    def conv(amount: float, ccy: str) -> float:
        return amount if ccy == base else (amount * fx_end if ccy == "USD" else amount / fx_end)

    divs = sum(conv(t.price or 0, t.currency) for t in txs if (t.type.value if hasattr(t.type, "value") else t.type) == "DIVIDEND")
    # dividends paid automatically from the price feed (when that setting is on): shares held on the day x amount
    import bisect

    ctx = Context(session, pid)
    auto = 0.0
    if ctx.settings.auto_dividends and ctx.series is not None:
        logged = {t.ticker for t in ctx.txs if t.type == "DIVIDEND"}
        dates = ctx.series.dates
        q = ctx.series.holding_qty
        for t, md in ctx.market.items():
            if t in logged or t not in q.columns:
                continue
            d = md.frame["dividend"]
            for day, amt in d[(d.index >= start) & (d.index <= end) & (d > 0)].items():
                i = bisect.bisect_right(dates, day) - 1
                if i >= 0:
                    auto += conv(float(amt) * float(q[t].iloc[i]), md.currency)
    recs = [r for r in call_rows(session) if r["closed_date"] is None and p.name in r["portfolios"]]
    change = s["range_gain"]
    twr = s["twr"]
    lead = top[0] if top else (bottom[0] if bottom else None)
    driver = None
    if lead is not None:
        driver = {"symbol": lead["symbol"], "contribution": lead["contribution"], "sp500": bench.get("SPY")}
    out = {"id": pid, "name": p.name, "start": start.isoformat(), "end": end.isoformat(), "base": base, "to_amt": to_amt,
           "value_amt": s["value"] * to_amt, "change_amt": change * to_amt if change is not None else None, "return": twr,
           "sp500": bench.get("SPY"), "nasdaq": bench.get("QQQ"), "top": top, "bottom": bottom,
           "dividends_amt": divs + auto, "trades": trades, "recommendations": [{"ticker": r["ticker"], "action": r["action"], "return": r["return"],
                                                                                  "edge": r["edge"], "worked": r["worked"]} for r in recs],
           "driver": driver, "range": pp["range"]}
    if top_scenario:
        out["if"] = top_scenario.get(str(pid))
    return out


def top_scenario_effects(session: Session) -> dict | None:
    """The book's most likely scenario and its effect on each person (median, display currency)."""
    from . import actions, book, impact

    entries = [e for e in book.entries_of(book.current(session)) if float(e["probability"]) > 0]
    if not entries:
        return None
    top = max(entries, key=lambda e: float(e["probability"]))
    specs = {s["id"]: s for s in actions.saved_specs(session)}
    s = specs.get(top["scenario_id"])
    if not s:
        return None
    res = impact.run(session, s["spec"], n=3000, analogs=False)
    if res.get("empty"):
        return None
    out = {"title": s["title"], "probability": float(top["probability"])}
    for b in res["blended"]:
        if b["id"] is not None:
            out[str(b["id"])] = {"title": s["title"], "probability": float(top["probability"]), "median": b["median"], "median_amt": b["median_amt"]}
    return out


# ------------------------------------------------------------------ text

HE = {"weekly": "דוח שבועי", "monthly": "דוח חודשי", "value": "שווי התיק", "change": "שינוי בתקופה", "vs": "מול",
      "best": "הכי טובות", "worst": "הכי חלשות", "divs": "דיבידנדים שהתקבלו", "trades": "פעולות בתקופה", "recs": "המלצות פתוחות",
      "if": "אם", "none": "אין", "family": "סיכום משפחתי", "advice": "זה לא ייעוץ השקעות", "people": "כל התיקים"}
EN = {"weekly": "Weekly report", "monthly": "Monthly report", "value": "Portfolio value", "change": "Change in the period", "vs": "vs",
      "best": "Best", "worst": "Weakest", "divs": "Dividends received", "trades": "Trades in the period", "recs": "Open recommendations",
      "if": "If", "none": "None", "family": "Family summary", "advice": "Not investment advice", "people": "All portfolios"}


def _amt(v, currency: str = "USD"):
    sym = "$" if currency == "USD" else "₪"
    return "–" if v is None else f"{'+' if v > 0.5 else '−' if v < -0.5 else ''}{sym}{abs(v):,.0f}"


def _pct(v, signed=True):
    return "–" if v is None else f"{('+' if v > 0 else '−' if v < 0 else '') if signed else ''}{abs(v) * 100:.1f}%"


def driver_line(d: dict, he: bool) -> str:
    dr = d.get("driver")
    if not dr:
        return ""
    if he:
        return (f"מה הזיז את התקופה: {dr['symbol']} ({_pct(dr['contribution'])} לתיק)" +
                (f", כשה-S&P 500 {'עלה' if (dr['sp500'] or 0) >= 0 else 'ירד'} {_pct(abs(dr['sp500'] or 0), False)}." if dr.get("sp500") is not None else "."))
    return (f"What drove the period: {dr['symbol']} ({_pct(dr['contribution'])} to the portfolio)" +
            (f", while the S&P 500 {'rose' if (dr['sp500'] or 0) >= 0 else 'fell'} {_pct(abs(dr['sp500'] or 0), False)}." if dr.get("sp500") is not None else "."))


def period_label(kind: str, start: str, end: str, he: bool) -> str:
    return f"{(HE if he else EN)[kind]} · {start} – {end}"


# ------------------------------------------------------------------ drawing

def _logo(sym: str) -> Path | None:
    from .db import get_engine

    try:
        with Session(get_engine()) as s:
            got = logos.get_logo(s, sym)
        return got[0] if got else None
    except Exception:
        return None


def card(d: dict, kind: str, lang: str) -> bytes:
    CCY = d["base"]
    he = lang == "he"
    T = HE if he else EN
    c = render.Canvas(1080, 1350, rtl=he, margin=64)
    c.d.rectangle([0, 0, 1080, 14], fill=render.ACCENT)
    c.y = 54
    c.text(d["name"], 56, bold=True, gap=0)
    c.text(period_label(kind, d["start"], d["end"], he), 26, render.MUTED, gap=18)
    c.text(T["value"], 26, render.MUTED, gap=0)
    c.text(_amt(d["value_amt"], CCY).lstrip("+"), 64, bold=True, gap=0)
    c.text(f"{_amt(d['change_amt'], CCY)}  ({_pct(d['return'])})", 40, render.sign_color(d["change_amt"]), bold=True, gap=10)
    c.pair(f"{T['vs']} S&P 500", _pct(d["sp500"]), 28, render.MUTED, render.sign_color(d["sp500"]))
    c.pair(f"{T['vs']} Nasdaq 100", _pct(d["nasdaq"]), 28, render.MUTED, render.sign_color(d["nasdaq"]))
    c.y += 6
    c.rule()
    for title, rows in ((T["best"], d["top"]), (T["worst"], d["bottom"])):
        if not rows:
            continue
        c.text(title, 26, render.MUTED, gap=2)
        for r in rows[:3]:
            y = c.y
            x_logo = 1080 - 64 - 40 if he else 64
            c.logo(_logo(r["symbol"]), r["symbol"][:1], x_logo, y, 40)
            f = render.font(30)
            name = r["symbol"]
            val = f"{_pct(r['return'])}  {_amt(r['change_amt'], CCY)}"
            if he:
                render.draw(c.d, (1080 - 64 - 52 - render.measure(f, name), y + 2), name, f, render.TEXT)
                render.draw(c.d, (64, y + 2), val, f, render.sign_color(r["change_amt"]))
            else:
                render.draw(c.d, (64 + 52, y + 2), name, f, render.TEXT)
                render.draw(c.d, (1080 - 64 - render.measure(f, val), y + 2), val, f, render.sign_color(r["change_amt"]))
            c.y += 52
        c.y += 8
    c.rule()
    c.pair(T["divs"], _amt(d["dividends_amt"], CCY) if d["dividends_amt"] else T["none"], 28)
    c.pair(T["trades"], str(len(d["trades"])) if d["trades"] else T["none"], 28)
    if d["recommendations"]:
        c.pair(T["recs"], ", ".join(r["ticker"] for r in d["recommendations"][:4]), 28)
    line = driver_line(d, he)
    if line:
        c.y += 8
        c.text(line, 28, render.TEXT, gap=6)
    if d.get("if"):
        x = d["if"]
        c.text((f"אם {x['title']} ({_pct(x['probability'], False)} לפי הספר שלי): {_amt(x['median_amt'], CCY)} ({_pct(x['median'])})" if he
                else f"If {x['title']} ({_pct(x['probability'], False)} in my book): {_amt(x['median_amt'], CCY)} ({_pct(x['median'])})"), 26, render.MUTED, gap=4)
    c.y = 1350 - 70
    c.text(f"Benchmark · {T['advice']}", 22, render.FAINT)
    return c.png()


def pdf(d: dict, kind: str, lang: str) -> bytes:
    CCY = d["base"]
    he = lang == "he"
    T = HE if he else EN
    doc = render.Document(rtl=he)
    doc.text(d["name"], 44, bold=True, gap=4)
    doc.text(period_label(kind, d["start"], d["end"], he), 24, render.MUTED, gap=20)
    doc.text(f"{T['value']}: {_amt(d['value_amt'], CCY).lstrip('+')}", 30, bold=True)
    doc.text(f"{T['change']}: {_amt(d['change_amt'], CCY)} ({_pct(d['return'])})", 28, render.sign_color(d["change_amt"]))
    doc.text(f"{T['vs']} S&P 500: {_pct(d['sp500'])} · {T['vs']} Nasdaq 100: {_pct(d['nasdaq'])}", 26, render.MUTED, gap=18)
    line = driver_line(d, he)
    if line:
        doc.text(line, 26, gap=18)
    for title, rows in ((T["best"], d["top"]), (T["worst"], d["bottom"])):
        doc.text(title, 28, render.ACCENT, bold=True)
        for r in rows or []:
            doc.text(f"{r['symbol']} {r['name']}: {_pct(r['return'])}, {_amt(r['change_amt'], CCY)}", 24)
        if not rows:
            doc.text(T["none"], 24, render.MUTED)
        doc.c.y += 10
    doc.text(f"{T['divs']}: {_amt(d['dividends_amt'], CCY) if d['dividends_amt'] else T['none']}", 24)
    doc.text(T["trades"], 28, render.ACCENT, bold=True)
    for t in d["trades"] or []:
        doc.text(f"{t['date']} {t['type']} {t['quantity']:g} {t['ticker']} @ {t['price']:,.2f} {t['currency']}", 24)
    if not d["trades"]:
        doc.text(T["none"], 24, render.MUTED)
    doc.text(T["recs"], 28, render.ACCENT, bold=True)
    for r in d["recommendations"] or []:
        doc.text(f"{r['action']} {r['ticker']}: {_pct(r['return'])} ({T['vs']} S&P {_pct(r['edge'])})", 24)
    if not d["recommendations"]:
        doc.text(T["none"], 24, render.MUTED)
    if d.get("if"):
        x = d["if"]
        doc.c.y += 10
        doc.text(f"{T['if']} {x['title']}: {_amt(x['median_amt'], CCY)} ({_pct(x['median'])})", 26, render.MUTED)
    doc.c.y += 20
    doc.text(T["advice"], 20, render.FAINT)
    return doc.bytes()


def family_card(people: list[dict], insights: list[dict], kind: str, start: str, end: str, lang: str) -> bytes:
    CCY = people[0]["base"] if people else "USD"
    he = lang == "he"
    T = HE if he else EN
    c = render.Canvas(1080, 1350, rtl=he, margin=64)
    c.d.rectangle([0, 0, 1080, 14], fill=render.ACCENT)
    c.y = 54
    c.text(T["family"], 56, bold=True, gap=0)
    c.text(period_label(kind, start, end, he), 26, render.MUTED, gap=18)
    tot = sum(p["value_amt"] for p in people)
    ch = sum(p["change_amt"] or 0 for p in people)
    c.text(_amt(tot, CCY).lstrip("+"), 60, bold=True, gap=0)
    c.text(_amt(ch, CCY), 40, render.sign_color(ch), bold=True, gap=14)
    c.rule()
    for p in sorted(people, key=lambda x: -(x["return"] or 0)):
        c.pair(p["name"], f"{_pct(p['return'])}   S&P {_pct(p['sp500'])}", 30, render.TEXT, render.sign_color(p["return"]))
    c.y += 8
    c.rule()
    for i in insights[:4]:
        c.text("• " + i["text"] if not he else i["text"], 24, render.MUTED, gap=4)
    c.y = 1350 - 70
    c.text(f"Benchmark · {T['advice']}", 22, render.FAINT)
    return c.png()


def family_pdf(people: list[dict], insights: list[dict], kind: str, start: str, end: str, lang: str) -> bytes:
    CCY = people[0]["base"] if people else "USD"
    he = lang == "he"
    T = HE if he else EN
    doc = render.Document(rtl=he)
    doc.text(T["family"], 44, bold=True, gap=4)
    doc.text(period_label(kind, start, end, he), 24, render.MUTED, gap=20)
    for p in sorted(people, key=lambda x: -(x["value_amt"] or 0)):
        doc.text(f"{p['name']}: {_amt(p['value_amt'], CCY).lstrip('+')} · {_amt(p['change_amt'], CCY)} ({_pct(p['return'])}) · S&P {_pct(p['sp500'])} · Nasdaq {_pct(p['nasdaq'])}", 25)
    doc.c.y += 14
    for i in insights:
        doc.text(i["text"], 24, render.MUTED)
    doc.c.y += 20
    doc.text(T["advice"], 20, render.FAINT)
    return doc.bytes()


# ------------------------------------------------------------------ generating and sending

def generate(session: Session, kind: str, start: dt.date, end: dt.date, lang: str | None = None) -> list[PersonReport]:
    from .portfolios import list_portfolios, overview_payload

    lang = lang or services.get_settings(session).memo_language or "he"
    folder = reports_dir()
    eff = None
    try:
        eff = top_scenario_effects(session)
    except Exception as exc:
        log.info("No book scenario for reports: %s", exc)
    made, people = [], []
    for p in list_portfolios(session):
        d = person_data(session, p.id, start, end, eff)
        if d is None:
            continue
        people.append(d)
        stem = f"{kind}-{end.isoformat()}-{p.id}-{lang}"
        (folder / f"{stem}.png").write_bytes(card(d, kind, lang))
        (folder / f"{stem}.pdf").write_bytes(pdf(d, kind, lang))
        made.append(_save(session, kind, p.id, p.name, start, end, lang, stem, d))
    if people:
        insights = overview_payload(session, "1Y").get("insights", [])
        stem = f"{kind}-{end.isoformat()}-family-{lang}"
        (folder / f"{stem}.png").write_bytes(family_card(people, insights, kind, start.isoformat(), end.isoformat(), lang))
        (folder / f"{stem}.pdf").write_bytes(family_pdf(people, insights, kind, start.isoformat(), end.isoformat(), lang))
        made.append(_save(session, kind, None, "Family", start, end, lang, stem, {"people": people, "insights": insights}))
    return made


def _save(session, kind, pid, name, start, end, lang, stem, data) -> PersonReport:
    old = session.exec(select(PersonReport).where(PersonReport.kind == kind, PersonReport.portfolio_id == pid,
                                                 PersonReport.period_end == end, PersonReport.lang == lang)).first()
    r = old or PersonReport(kind=kind, portfolio_id=pid, period_start=start, period_end=end, lang=lang)
    r.name, r.png, r.pdf, r.data = name, f"{stem}.png", f"{stem}.pdf", json.dumps(services._clean(data), ensure_ascii=False)
    r.created_at = dt.datetime.now(dt.timezone.utc)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def send(session: Session, r: PersonReport, client=None) -> None:
    """Sends one report to MY Telegram chat (the paired one). Never to anyone else."""
    from .telegram_bot import TelegramClient

    st = services.get_settings(session)
    if not (st.telegram_token and st.telegram_chat_id):
        raise RuntimeError("Telegram isn't connected. Set it up in Settings first.")
    client = client or TelegramClient(st.telegram_token)
    he = r.lang == "he"
    who = r.name if r.portfolio_id is not None else ("כל המשפחה" if he else "the family")
    hint = (f"{HE[r.kind]} · {who} · {r.period_start} – {r.period_end}\nאפשר להעביר (Forward) למי שצריך." if he
            else f"{EN[r.kind]} · {who} · {r.period_start} – {r.period_end}\nForward it to whoever it's for.")
    folder = reports_dir()
    client.send_file(st.telegram_chat_id, (folder / r.png).read_bytes(), r.png, hint, photo=True)
    client.send_file(st.telegram_chat_id, (folder / r.pdf).read_bytes(), r.pdf, "")
    r.sent_at = dt.datetime.now(dt.timezone.utc)
    session.add(r)
    session.commit()


def run_due(session: Session, today: dt.date | None = None, client=None) -> list[PersonReport]:
    """Makes any reports that are due (and missed ones), then sends them to me if Telegram is set up."""
    today = today or dt.date.today()
    st = services.get_settings(session)
    made = []
    for kind, start, end in due(today, st.report_last_weekly, st.report_last_monthly):
        made += generate(session, kind, start, end)
        if kind == "weekly":
            st.report_last_weekly = end.isoformat()
        else:
            st.report_last_monthly = end.isoformat()
        session.add(st)
        session.commit()
    if made and st.report_auto_send and st.telegram_token and st.telegram_chat_id:
        for r in made:
            try:
                send(session, r, client)
            except Exception as exc:
                log.warning("Couldn't send report %s: %s", r.id, exc)
    return made


_started = False


def start_scheduler() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        import time

        while True:
            try:
                with Session(db.get_engine()) as s:
                    run_due(s)
            except Exception as exc:
                log.warning("Report scheduler: %s", exc)
            time.sleep(3600)

    threading.Thread(target=loop, name="reports", daemon=True).start()


def listing(session: Session) -> list[dict]:
    rows = session.exec(select(PersonReport).order_by(PersonReport.period_end.desc(), PersonReport.portfolio_id)).all()
    return [{"id": r.id, "kind": r.kind, "portfolio_id": r.portfolio_id, "name": r.name, "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(), "lang": r.lang, "created_at": r.created_at.isoformat(),
             "sent_at": r.sent_at.isoformat() if r.sent_at else None} for r in rows]
