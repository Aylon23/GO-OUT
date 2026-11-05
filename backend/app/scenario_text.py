"""Turn a sentence ("war with Iran", "הנפט יורד 20%", "Apple misses earnings") into a short list
of shocks, using local word rules (free, offline). Sizes are filled in later from history."""
from __future__ import annotations

import re

from .scenario_library import THEMES

# keywords -> factor symbol. `invert` means the word names a yield/rate: rates up = bond prices down.
FACTOR_WORDS: list[tuple[list[str], str, bool]] = [
    (["נפט", "ברנט", "oil", "crude", "brent", "wti"], "USO", False),
    (["תשואות", "תשואה", "yields", "yield"], "TLT", True),
    (["ריבית", "ריביות", "interest rate", "interest rates", "rates", "the fed", "fed"], "TLT", True),
    (["אג\"ח", "אג״ח", "אגח", "איגרות", "אגרות", "bonds", "bond", "treasuries", "treasury"], "TLT", False),
    (["אג\"ח קונצרני", "high yield", "junk", "credit", "קונצרני"], "HYG", False),
    (["זהב", "gold"], "GLD", False),
    (["ביטקוין", "קריפטו", "bitcoin", "crypto", "btc"], "BTC-USD", False),
    (["שקל", "shekel", "ils"], "ILS=X", True),  # "the shekel strengthens" = USD/ILS falls
    (["דולר", "dollar", "usd"], "UUP", False),
    (["נאסד\"ק", "נאסד״ק", "נאסדק", "nasdaq"], "QQQ", False),
    (["הייטק", "טכנולוגיה", "tech stocks", "tech", "technology"], "XLK", False),
    (["שבבים", "שבב", "chips", "chip", "semiconductors", "semis"], "SOXX", False),
    (["מניות קטנות", "small caps", "small-caps", "russell"], "IWM", False),
    (["בנקים", "פיננסים", "banks", "financials"], "XLF", False),
    (["אנרגיה", "energy"], "XLE", False),
    (["ביטחוניות", "ביטחון", "defense", "defence"], "ITA", False),
    (["בריאות", "פארמה", "healthcare", "pharma"], "XLV", False),
    (["נדל\"ן", "נדל״ן", "נדלן", "real estate", "reits"], "XLRE", False),
    (["תשתיות", "utilities"], "XLU", False),
    (["תל אביב", "ת\"א", "ת״א", "בורסה בתל אביב", "השוק הישראלי", "מניות ישראל", "israeli stocks", "tase", "tel aviv"], "EIS", False),
    (["vix", "תנודתיות", "volatility", "מדד הפחד", "fear index"], "^VIX", False),
    (["s&p", "סנופי", "אס אנד פי", "השוק", "וול סטריט", "wall street", "the market", "stock market", "stocks", "המניות", "מניות"], "SPY", False),
]

UP = ["עולה", "עולים", "עולות", "עלה", "עלו", "יעלה", "יעלו", "תעלה", "יעלו", "מזנק", "מזנקת", "זינוק", "קופץ", "קופצת", "מתחזק", "מתחזקת", "יתחזק",
      "מעלה", "העלאת", "העלאה", "גבוה", "יקר", "מתייקר",
      "rise", "rises", "rising", "rose", "up", "jump", "jumps", "surge", "surges", "spike", "spikes", "soar", "soars", "rally", "rallies",
      "gain", "gains", "climb", "climbs", "strengthen", "strengthens", "stronger", "higher", "hike", "hikes", "raise", "raises", "beat", "beats", "boom"]
DOWN = ["יורד", "יורדת", "יורדים", "יורדות", "ירד", "ירדה", "ירדו", "ירידה", "ירידת", "ירידות", "תרד", "ירדו", "צונח", "צונחת", "צניחה", "נופל", "נופלת", "נפילה",
        "קורס", "קורסת", "קריסה", "נחלש", "נחלשת", "ייחלש", "מוריד", "הורדת", "הורדה", "זול", "מתרסק", "נמוך",
        "fall", "falls", "falling", "fell", "drop", "drops", "dropping", "dropped", "down", "decline", "declines", "plunge", "plunges", "crash", "crashes",
        "slide", "slides", "sink", "sinks", "tank", "tanks", "tumble", "tumbles", "weaken", "weakens", "weaker", "lower", "cut", "cuts", "slump"]
# company news
GOOD = ["טובים", "טוב", "מעולים", "מעולה", "חזקים", "חזק", "מפתיעים", "הפתעה חיובית", "beat", "beats", "strong", "great", "good", "blowout", "record"]
BAD = ["לא טוב", "לא טובים", "לא טובות", "רעים", "רע", "גרועים", "גרוע", "חלשים", "חלש", "מאכזבים", "מאכזב", "אכזבה", "נכשל", "נכשלת",
       "miss", "misses", "missed", "bad", "weak", "disappoint", "disappoints", "disappointing", "poor", "terrible", "warning", "lawsuit", "scandal", "fraud"]
EARNINGS = ["דוחות", "דוח", "ידווחו", "ידווח", "מדווחת", "רבעון", "earnings", "reports", "report", "results", "quarter", "guidance"]

_NUM = re.compile(r"([+-−]?\d+(?:[.,]\d+)?)\s*(%|אחוז|אחוזים|percent|pct|נקודות בסיס|נ\"ב|bp|bps|basis points)?")
_HEB = re.compile(r"[֐-׿]")


def _find(text: str, word: str) -> list[int]:
    """Positions of a keyword. English words match whole words; Hebrew words may carry a
    one-letter prefix (ה, ו, ב, ל, ש, מ, כ) so they match inside a word."""
    if _HEB.search(word):
        return [m.start() for m in re.finditer(re.escape(word), text)]
    return [m.start() for m in re.finditer(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text)]


def _has_any(text: str, words: list[str]) -> bool:
    return any(_find(text, w) for w in words)


def _direction(window: str) -> int:
    """+1, -1 or 0 from the verbs in a stretch of text. A negated good word ("לא טוב") is bad."""
    bad = _has_any(window, ["לא טוב", "לא טובים", "לא טובות", "not good", "no good"])
    if bad:
        return -1
    up = sum(len(_find(window, w)) for w in UP)
    down = sum(len(_find(window, w)) for w in DOWN)
    return 1 if up > down else -1 if down > up else 0


def _number(window: str) -> tuple[float | None, str | None]:
    for m in _NUM.finditer(window):
        raw, unit = m.group(1).replace("−", "-").replace(",", "."), m.group(2)
        try:
            v = float(raw)
        except ValueError:
            continue
        if unit in ("נקודות בסיס", "נ\"ב", "bp", "bps", "basis points"):
            return v / 10000, "bp"
        if unit is None and (raw[0] not in "+-" or abs(v) > 100):
            continue  # a bare number ("5 years") is not a move; "-20" or "20%" is
        return v / 100, "pct"
    return None, None


def _windows(text: str, hits: list[tuple[int, int]]) -> list[tuple[str, str]]:
    """For each keyword hit (start, end): a little text before it (for verbs that come first)
    and the text from it up to the next hit (numbers are read only from here)."""
    out = []
    hits = sorted(hits)
    for i, (a, b) in enumerate(hits):
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        prev_end = hits[i - 1][1] if i > 0 else 0
        out.append((text[max(prev_end, a - 14):a], text[a:nxt]))
    return out


def parse_rules(text: str, companies: dict[str, str]) -> dict:
    """Shocks and themes from the words alone. `companies` maps lowercase names (Hebrew or
    English) and tickers to symbols. Returned moves are None when the text gives no number:
    the caller fills in a typical size from history."""
    t = " " + text.lower().replace("’", "'").replace("“", "\"").replace("”", "\"") + " "
    themes = []
    for th in THEMES:
        if all(_has_any(t, group) for group in th["any"]):
            themes.append(th["id"])
    if "iran_war" in themes:
        themes = [x for x in themes if x not in ("war", "israel_war")]
    if "israel_war" in themes and "war" in themes:
        themes.remove("war")
    if "ai_bust" in themes and not (_direction(t) < 0 or _has_any(t, ["בועה", "bubble", "deepseek", "דיפסיק"])):
        themes.remove("ai_bust")

    # factor keyword hits, longest keyword first so "אג\"ח קונצרני" beats "אג\"ח"
    hits: list[tuple[int, int, str, bool]] = []
    taken: list[tuple[int, int]] = []
    flat = sorted(((w, sym, inv) for words, sym, inv in FACTOR_WORDS for w in words), key=lambda x: -len(x[0]))
    for w, sym, inv in flat:
        for p in _find(t, w):
            if any(a <= p < b or a < p + len(w) <= b for a, b in taken):
                continue
            taken.append((p, p + len(w)))
            hits.append((p, p + len(w), sym, inv))
    # companies
    for name, sym in sorted(companies.items(), key=lambda x: -len(x[0])):
        for p in _find(t, name.lower()):
            if any(a <= p < b for a, b in taken):
                continue
            taken.append((p, p + len(name)))
            hits.append((p, p + len(name), sym, None))
    hits.sort()
    windows = _windows(t, [(a, b) for a, b, *_ in hits])

    shocks: dict[str, dict] = {}
    notes: list[str] = []
    currency_named = False
    carry_inv = False  # "yields of bonds rise": the rate word with no verb hands its meaning on
    for i, ((a, b, sym, inv), (before, after)) in enumerate(zip(hits, windows)):
        word = t[a:b].strip()
        win = before + after
        if inv is None:  # a company
            d = _direction(win)
            good, bad = _has_any(win, GOOD), _has_any(win, BAD)
            if bad or (d < 0 and not good):
                d = -1
            elif good or d > 0:
                d = 1
            n, unit = _number(after)
            kind = "earnings" if _has_any(win, EARNINGS) else "news"
            if n is not None and d == 0:
                d = 1 if n > 0 else -1
            if d == 0:
                if kind != "earnings":
                    notes.append(f"Not sure which way {sym} moves; assumed down. Edit the shock if not.")
                d = -1
            move = abs(n) * d if n is not None else None
            shocks[sym] = {"symbol": sym, "kind": "stock", "direction": d, "move": move, "word": word, "event": kind}
            continue
        if sym in ("ILS=X", "UUP"):
            currency_named = True  # currency moves are left out: stock performance only
            continue
        if carry_inv and sym == "TLT":
            inv = True
        carry_inv = False
        d = _direction(win)
        n, unit = _number(after)
        if n is not None and d == 0:
            d = 1 if n > 0 else -1
        if d == 0:
            nxt = hits[i + 1] if i + 1 < len(hits) else None
            if inv and nxt is not None and nxt[2] == sym:
                carry_inv = True
            continue  # a factor named without a direction ("oil and gold") is context, not a shock
        if inv:
            d = -d  # rates/yields up -> long bonds down; the shekel stronger -> USD/ILS down
        if sym == "TLT" and inv:
            if n is not None and (unit == "bp" or abs(n) <= 0.03):
                n = 16 * abs(n) * d  # ~16 years of duration: +1% in long yields ~ -16% on long bonds
                notes.append(f"A {abs(n) / 16:.2%} move in long yields is about {abs(n):.0%} on long bonds.")
                shocks[sym] = {"symbol": sym, "kind": "factor", "direction": d, "move": n, "word": word}
                continue
            if n is not None:
                n = -n  # "yields rise 10%" is unusual; treat the number as the bond move the other way
        move = abs(n) * d if n is not None else None
        prev = shocks.get(sym)
        if prev and prev.get("move") is not None and move is None:
            continue
        shocks[sym] = {"symbol": sym, "kind": "factor", "direction": d, "move": move, "word": word}
    if currency_named:
        notes.append("Currency moves are left out: the app measures stock performance only, in each stock's own currency.")
    return {"themes": themes, "shocks": list(shocks.values()), "notes": notes}


WEEKS_PER = {"day": 0.2, "week": 1.0, "month": 4.3, "quarter": 13.0, "year": 52.0}
_SPAN_NUM = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "nine": 9, "twelve": 12,
        "אחד": 1, "אחת": 1, "שני": 2, "שתי": 2, "שניים": 2, "שלושה": 3, "שלוש": 3, "ארבעה": 4, "ארבע": 4,
        "חמישה": 5, "חמש": 5, "שישה": 6, "שש": 6, "תשעה": 9, "תשע": 9}
_HE_UNIT = [("שבועיים", 2.0), ("חודשיים", 8.6), ("חצי שנה", 26.0), ("חצי-שנה", 26.0), ("שנתיים", 104.0)]
_HE_WORD = {"יום": "day", "ימים": "day", "שבוע": "week", "שבועות": "week", "חודש": "month", "חודשים": "month",
            "רבעון": "quarter", "רבעונים": "quarter", "שנה": "year", "שנים": "year"}


def horizon_from_text(text: str) -> float | None:
    """The time span a sentence names, in weeks: "over a month", "בחודש הקרוב", "3 חודשים", "next quarter".
    None when the sentence names none (the caller then uses past events or one week)."""
    t = " " + text.lower() + " "
    for w, weeks in _HE_UNIT:
        if w in t:
            return weeks
    m = re.search(r"(\d+(?:\.\d+)?|" + "|".join(_SPAN_NUM) + r")?\s*-?\s*(day|week|month|quarter|year)s?\b", t)
    if m and (m.group(1) or re.search(r"\b(a|an|one|next|this|over the|over a|in a|within a|per)\s+" + m.group(2), t)):
        n = float(_SPAN_NUM.get(m.group(1), m.group(1) or 1)) if m.group(1) else 1.0
        return n * WEEKS_PER[m.group(2)]
    if re.search(r"\b(next|this|over the|in the|within the)\s+(day|week|month|quarter|year)\b", t):
        u = re.search(r"\b(?:next|this|over the|in the|within the)\s+(day|week|month|quarter|year)\b", t).group(1)
        return WEEKS_PER[u]
    # Hebrew: "בחודש", "לחודש", "חודש הבא", "3 חודשים", "שלושה חודשים"; "השנה" alone is "this year" too
    for m in re.finditer(r"(?:(\d+(?:\.\d+)?|" + "|".join(k for k in _SPAN_NUM if not k.isascii()) + r")\s+)?[בלה]?(ימים|יום|שבועות|שבוע|חודשים|חודש|רבעונים|רבעון|שנים|שנה)(?=[\s.,?!]|$)", t):
        n = m.group(1)
        k = float(n) if n and n[0].isdigit() else float(_SPAN_NUM.get(n, 1)) if n else 1.0
        return k * WEEKS_PER[_HE_WORD[m.group(2)]]
    return None
