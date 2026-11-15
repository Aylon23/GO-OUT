"""A short weekly market summary in plain Hebrew, for people who do not follow the markets.

- The numbers (how much each index, the dollar, oil, gold and the US 10-year yield moved this week) are
  computed here from the price data. They are shown in a table the model never writes.
- One model (the panel's first seat, the best value) explains, with a web search, what happened and what
  moved the markets, in simple words. It may only quote numbers given to it; every number it writes is
  checked against them and a sentence with a number that does not match is dropped.
- It sends nothing about the portfolios: no tickers held, no names, no amounts.
- Made once per week, kept, and reused by every card, so a week costs one call (cents).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

from sqlmodel import Session, select

from . import openrouter
from .market.cache import NoPriceData, load_history
from .models import MarketWeek

log = logging.getLogger("market_week")

# what a family member recognises: Israel first, then the US, then the things in the news
INSTRUMENTS = [
    ("^TA125.TA", "ת\"א 125", "מדד המניות הגדולות בבורסה בתל אביב", "index"),
    ("^GSPC", "S&P 500", "500 החברות הגדולות בארה\"ב", "index"),
    ("^NDX", "נאסד\"ק 100", "חברות הטכנולוגיה הגדולות בארה\"ב", "index"),
    ("ILS=X", "דולר-שקל", "כמה שקלים עולה דולר", "rate"),
    ("BZ=F", "נפט ברנט", "מחיר חבית נפט, בדולרים", "usd"),
    ("GC=F", "זהב", "מחיר אונקיית זהב, בדולרים", "usd"),
    ("^TNX", "ריבית אג\"ח ארה\"ב ל-10 שנים", "התשואה על אגרת חוב של ממשלת ארה\"ב", "yield"),
]
MAX_TOKENS = 4000

SCHEMA = {"type": "object", "additionalProperties": True, "required": ["headline", "what_happened", "drivers", "next_week"],
          "properties": {"headline": {"type": "string"}, "what_happened": {"type": "string"},
                         "drivers": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
                             "type": "object", "additionalProperties": True, "required": ["title", "text"],
                             "properties": {"title": {"type": "string"}, "text": {"type": "string"}}}},
                         "next_week": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 10}}}

GUIDANCE = (
    "You write a short weekly market summary in Hebrew for family members who are not investors. Plain words, "
    "short sentences, no jargon; when a term is unavoidable, explain it in a few words (for example: 'the Fed, the "
    "US central bank'). Search the web for what actually happened this week. "
    "`headline`: one sentence, the week in a nutshell. `what_happened`: two or three sentences. `drivers`: the two "
    "to four things that moved the markets most, each a short `title` and two sentences on what happened and why it "
    "mattered. `next_week`: one or two sentences on what is scheduled next week that could move markets. "
    "Numbers: use only the moves and levels in FACTS, rounded as given. Do not write any other number, percentage or "
    "price. Never mention FACTS, 'the app', 'the data' or where a number comes from: the reader just reads the week. "
    "If what you read in the news disagrees with a number in FACTS, leave that number out of the sentence. "
    "Dates in words. No advice, no predictions of prices, no 'you should'. "
    "Put the pages you used in `sources`.")


def week_of(end: dt.date) -> tuple[dt.date, dt.date]:
    return end - dt.timedelta(days=6), end


def facts(session: Session, start: dt.date, end: dt.date) -> list[dict]:
    """Each instrument's close before the week and at its end, and the move."""
    out = []
    for sym, name, what, kind in INSTRUMENTS:
        try:
            c = load_history(session, sym)["close"]
        except NoPriceData:
            continue
        before, last = c[c.index < start], c[c.index <= end]
        if before.empty or last.empty or last.index[-1] < start:
            continue
        a, b = float(before.iloc[-1]), float(last.iloc[-1])
        row = {"symbol": sym, "name": name, "what": what, "kind": kind, "level": round(b, 2)}
        if kind == "yield":
            row["change_pts"] = round(b - a, 2)
        else:
            row["change_pct"] = round((b / a - 1) * 100, 1) if a else None
        out.append(row)
    return out


_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%|אחוז)?")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _allowed(fs: list[dict]) -> tuple[set[float], list[float]]:
    pcts, levels = set(), []
    for f in fs:
        if f.get("change_pct") is not None:
            pcts.add(round(abs(f["change_pct"]), 1))
        if f.get("change_pts") is not None:
            pcts.add(round(abs(f["change_pts"]), 2))
            levels.append(round(abs(f["change_pts"]) * 100))  # basis points
        levels.append(f["level"])
    return pcts, levels


# numbers that are part of a name, not data: "S&P 500", "נאסד"ק 100", "ת"א 125", "אג"ח ל-10 שנים", "Dow 30"
_NAMES = re.compile(r"(S&P|נאסד[\"״]?ק|Nasdaq|NASDAQ|ת[\"״]א|TA|דאו|Dow|Russell|ראסל|Stoxx|DAX|FTSE|Nikkei|ניקיי)[\s-]*\d+"
                    r"|\d+[\s-]*(שנים|שנה|years?|year|חודשים|ימים|שבועות)", re.I)
_BIG = re.compile(r"\s*(מיליארד|מיליון|billion|million|bn|trillion|טריליון)", re.I)


def unknown_numbers(text: str, fs: list[dict]) -> list[str]:
    """Numbers in the text that are not in FACTS. Day-of-month and years are fine, and so are numbers that
    belong to a name (S&P 500, a 10-year bond)."""
    pcts, levels = _allowed(fs)
    bad = []
    text = _NAMES.sub(" ", text or "")
    for m in _NUM.finditer(text):
        raw, pct = m.group(1), m.group(2)
        v = float(raw.replace(",", ""))
        if _BIG.match(text, m.end()):  # "5 billion": a company figure the app cannot confirm
            bad.append(m.group(0))
        elif pct:
            if not any(abs(v - p) <= 0.051 for p in pcts) and not any(abs(v - f["level"]) <= 0.051 for f in fs if f["kind"] == "yield"):
                bad.append(m.group(0))
        elif 1900 <= v <= 2100 or v <= 31:
            continue
        elif not any(abs(v - lv) <= max(0.015 * abs(lv), 0.051) for lv in levels):
            bad.append(m.group(0))
    return bad


def checked(text: str, fs: list[dict], dropped: list[str]) -> str:
    """The text without any sentence that quotes a number FACTS does not have."""
    keep = []
    for sentence in _SENT.split((text or "").strip()):
        if unknown_numbers(sentence, fs):
            dropped.append(sentence)
        else:
            keep.append(sentence)
    return " ".join(keep).strip()


def get(session: Session, end: dt.date) -> MarketWeek | None:
    return session.exec(select(MarketWeek).where(MarketWeek.week_end == end)).first()


def make(session: Session, end: dt.date, force: bool = False) -> MarketWeek:
    """This week's summary: kept after the first time. Without a key, the budget or a working model, the
    numbers are kept and the text says it could not be written."""
    from .panel import unlink

    old = get(session, end)
    if old is not None and old.status == "done" and not force:
        return old
    start, end = week_of(end)
    fs = facts(session, start, end)
    row = old or MarketWeek(week_end=end)
    row.facts = json.dumps(fs, ensure_ascii=False)
    seat = next((s for s in openrouter.panel_seats(session) if s["model"]), None)
    try:
        if not openrouter.api_key():
            raise openrouter.NoKey("No OpenRouter key.")
        if seat is None:
            raise openrouter.AiError("No model found on OpenRouter.")
        msg = {"week": f"{start.isoformat()} to {end.isoformat()}", "facts": fs,
               "note": "change_pct is the week's move in percent; change_pts is the yield's move in percentage points; level is the last close."}
        ans, call = openrouter.chat_json(session, "market_week", [seat["model"]], [{"role": "user", "content": json.dumps(msg, ensure_ascii=False)}],
                                         SCHEMA, "weekly_market_summary", max_tokens=MAX_TOKENS, step_key=f"market:{end.isoformat()}",
                                         paid=True, web=True, guidance=GUIDANCE, timeout=180.0, effort="medium")
        links: list[str] = []
        dropped: list[str] = []
        clean = lambda t: checked(unlink(str(t or ""), links), fs, dropped)
        drivers = [{"title": unlink(str(d.get("title") or ""), links)[:80], "text": clean(d.get("text"))[:400]} for d in ans.get("drivers") or []]
        text = {"headline": clean(ans.get("headline"))[:200], "what_happened": clean(ans.get("what_happened"))[:600],
                "drivers": [d for d in drivers if d["title"] and d["text"]][:4], "next_week": re.sub(r"^\s*(בשבוע הבא|next week)\s*[:,]?\s*", "", clean(ans.get("next_week")), flags=re.I)[:400],
                "sources": list(dict.fromkeys([*[str(x)[:200] for x in ans.get("sources") or []], *links]))[:10]}
        row.text, row.dropped, row.cost, row.model = json.dumps(text, ensure_ascii=False), json.dumps(dropped, ensure_ascii=False), call.cost or 0.0, call.model_used or seat["model"]
        row.status, row.note = ("done", "") if text["headline"] or text["what_happened"] else ("failed", "Every sentence quoted a number the app could not confirm.")
    except Exception as exc:
        row.status, row.note = "failed", str(exc)[:400]
        log.info("Weekly market summary not written: %s", exc)
    row.created_at = dt.datetime.now(dt.timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def payload(row: MarketWeek | None) -> dict | None:
    if row is None:
        return None
    return {"week_end": row.week_end.isoformat(), "status": row.status, "note": row.note, "facts": json.loads(row.facts or "[]"),
            "text": json.loads(row.text or "{}"), "dropped": json.loads(row.dropped or "[]"), "cost": round(row.cost or 0.0, 4),
            "model": row.model, "created_at": row.created_at.isoformat()}
