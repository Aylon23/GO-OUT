"""Reading a scenario question before the AI debate: horizon, cause, and dollar moves turned into percent.

"oil falls $10 per barrel" -> oil -10 USD on WTI at today's price -> about -15%. If the horizon or the cause
can't be read from the text, the UI asks with chips (horizon: 1w/1m/3m/6m; cause: supply/demand/policy/
geopolitical) before anything is sent.
"""
from __future__ import annotations

import re

from sqlmodel import Session

HORIZONS = ["1w", "1m", "3m", "6m"]
CAUSES = ["supply", "demand", "policy", "geopolitical"]

_HORIZON_WORDS = [
    (r"\b(?:a|one|1|next)\s*-?\s*weeks?\b|\bthis week\b|שבוע", "1w"),
    (r"\b(?:two|2)\s*weeks?\b|שבועיים", "1m"),
    (r"\b(?:a|one|1|next)\s*-?\s*months?\b|\bthis month\b|(?<!\d\s)חודש(?!ים)", "1m"),
    (r"\b(?:three|3)\s*months?\b|\bquarter\b|3\s*חודשים|שלושה חודשים|רבעון", "3m"),
    (r"\b(?:six|6)\s*months?\b|\bhalf a year\b|6\s*חודשים|שישה חודשים|חצי שנה", "6m"),
]
_CAUSE_WORDS = [
    ("geopolitical", r"\bwar\b|attack|invasion|strike|missile|conflict|iran|israel|china.*taiwan|sanction|terror|מלחמה|מתקפה|תקיפה|טילים|איראן|פלישה|הסלמה"),
    ("supply", r"\bopec\b|production|output cut|supply|shortage|strait|hormuz|refiner|הפקה|היצע|מחסור|אופ\"?ק"),
    ("policy", r"\bfed\b|rate (?:hike|cut)|interest|tariff|tax|regulat|election|policy|stimulus|ריבית|מכס|מס |בחירות|רגולציה|הפד"),
    ("demand", r"recession|demand|consumer|slowdown|unemployment|jobs|earnings|מיתון|ביקוש|צריכה|אבטלה|האטה"),
]
# what a dollar move is quoted on, and the symbol whose price converts it
_DOLLAR_SUBJECTS = [
    (r"brent", "BZ=F", "Brent crude", "oil"),
    (r"\boil\b|crude|\bwti\b|barrel|נפט|חבית", "CL=F", "WTI crude", "oil"),
    (r"gold|ounce|זהב|אונקיה", "GC=F", "Gold", "gold"),
]
_DOLLARS = re.compile(r"(?:\$\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:\$|dollars?|usd|דולר))", re.I)
_DOWN = re.compile(r"fall|drop|declin|plung|down|lower|cheaper|sink|יורד|ירד|תרד|נופל|יירד|צונח|זול", re.I)
_UP = re.compile(r"rise|jump|up\b|higher|climb|surge|soar|spike|עולה|עלה|יעלה|תעלה|מזנק|קופץ", re.I)


def horizon_of(text: str) -> str | None:
    t = text.lower()
    hit = None
    for pat, h in _HORIZON_WORDS:
        if re.search(pat, t):
            hit = h
    return hit


def cause_of(text: str) -> str | None:
    t = text.lower()
    for cause, pat in _CAUSE_WORDS:
        if re.search(pat, t):
            return cause
    return None


def dollar_moves(session: Session, text: str) -> list[dict]:
    """Dollar moves in the text, converted to percent at the latest price."""
    from .market.cache import NoPriceData, load_history

    out = []
    low = text.lower()
    for m in _DOLLARS.finditer(text):
        amount = float((m.group(1) or m.group(2)).replace(",", "."))
        window = low[max(0, m.start() - 60): m.end() + 40]
        subject = next(((sym, name, factor) for pat, sym, name, factor in _DOLLAR_SUBJECTS if re.search(pat, window)), None)
        if subject is None:
            continue
        sym, name, factor = subject
        d = -1 if _DOWN.search(window) else 1 if _UP.search(window) else 0
        try:
            c = load_history(session, sym)["close"]
            price, as_of = float(c.iloc[-1]), c.index[-1].isoformat()
        except (NoPriceData, IndexError):
            out.append({"subject": name, "symbol": sym, "factor": factor, "dollars": amount * (d or 1), "price": None,
                        "as_of": None, "pct": None, "direction": d})
            continue
        out.append({"subject": name, "symbol": sym, "factor": factor, "dollars": amount * (d or 1), "price": round(price, 2),
                    "as_of": as_of, "pct": round(100 * amount / price * (d or 1), 2), "direction": d,
                    "text": f"{name} {'−' if d < 0 else '+'}${amount:g} from ${price:,.2f} (as of {as_of}) = "
                            f"{'−' if d < 0 else '+'}{100 * amount / price:.1f}%"})
    return out


def read(session: Session, text: str, horizon: str | None = None, cause: str | None = None) -> dict:
    """What the debate needs from the question, and what it still has to ask."""
    from .scenario_text import parse_rules
    from .scenarios import _companies, books

    text = (text or "").strip()
    _ports, fam = books(session, None)
    rules = parse_rules(text, _companies(session, fam.holdings))
    h = horizon if horizon in HORIZONS else horizon_of(text)
    c = cause if cause in CAUSES else cause_of(text)
    if c is None and any(t in ("iran_war", "israel_war", "war") for t in rules.get("themes", [])):
        c = "geopolitical"
    needs = {}
    if h is None:
        needs["horizon"] = HORIZONS
    if c is None:
        needs["cause"] = CAUSES
    return {"text": text, "horizon": h, "cause": c, "needs": needs, "dollar_moves": dollar_moves(session, text),
            "themes": rules.get("themes", []), "notes": rules.get("notes", [])}
