"""Turn a short sentence into a draft transaction, in Hebrew or English. Local rules, no AI service.

    "קניתי 5 מניות של QQQ"            -> BUY 5 QQQ at today's close, current portfolio
    "אבא קנה SPY ב-1000 דולר"          -> BUY $1,000 of SPY in the portfolio named "אבא"
    "sold 3 AAPL at 190 yesterday"     -> SELL 3 AAPL at $190, yesterday
    "הפקדתי 5000 שקל לתיק של אמא"       -> DEPOSIT ₪5,000 into "אמא"

The draft is only a suggestion: the app shows it and the user confirms before anything is saved.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlmodel import Session, select

from .engine.series import align, convert
from .market.cache import NoPriceData, currency_for, price_on
from .market.provider import ProviderError, get_provider
from .market.search import normalize_quote, rank
from .models import Portfolio, TxType

VERBS = {
    TxType.BUY: ["קניתי", "קנינו", "קנה", "קנתה", "קנו", "לקנות", "תקנה", "קנייה", "קניה", "buy", "bought", "purchase", "purchased", "add"],
    TxType.SELL: ["מכרתי", "מכרנו", "מכר", "מכרה", "מכרו", "למכור", "תמכור", "מכירה", "sell", "sold"],
    TxType.DEPOSIT: ["הפקדתי", "הפקדנו", "הפקיד", "הפקידה", "הפקדה", "להפקיד", "deposit", "deposited"],
    TxType.WITHDRAWAL: ["משכתי", "משכנו", "משך", "משכה", "משיכה", "למשוך", "withdraw", "withdrew", "withdrawal"],
    TxType.DIVIDEND: ["דיבידנד", "דיבידנדים", "dividend", "dividends"],
}
# Hebrew company names people type, mapped to Yahoo tickers.
HEBREW_NAMES = {
    "אפל": "AAPL", "אפפל": "AAPL", "מיקרוסופט": "MSFT", "אנבידיה": "NVDA", "אנווידיה": "NVDA", "נבידיה": "NVDA", "אנוידיה": "NVDA",
    "גוגל": "GOOGL", "אלפבית": "GOOGL", "אמזון": "AMZN", "טסלה": "TSLA", "מטא": "META", "פייסבוק": "META", "נטפליקס": "NFLX",
    "אינטל": "INTC", "טבע": "TEVA.TA", "לאומי": "LUMI.TA", "פועלים": "POLI.TA", "הפועלים": "POLI.TA", "דיסקונט": "DSCT.TA",
    "מזרחי": "MZTF.TA", "נייס": "NICE.TA", "אלביט": "ESLT.TA", "בזק": "BEZQ.TA", "אייסיאל": "ICL.TA", "כיל": "ICL.TA",
    "נאסדק": "QQQ", "נאסד\"ק": "QQQ", "אסנדפי": "SPY",
}
CURRENCY_WORDS = {"$": "USD", "דולר": "USD", "דולרים": "USD", "usd": "USD", "dollars": "USD", "dollar": "USD",
                  "₪": "ILS", "שקל": "ILS", "שקלים": "ILS", "ש\"ח": "ILS", "ש״ח": "ILS", "שח": "ILS", "ils": "ILS", "nis": "ILS"}
SHARE_WORDS = {"מניות", "מניה", "יחידות", "יחידה", "shares", "share", "units", "unit", "sh"}
PRICE_WORDS = {"at", "@", "במחיר", "בשער", "מחיר", "שער", "price", "for"}
STOP = {"של", "את", "לתיק", "בתיק", "תיק", "ל", "ב", "the", "of", "in", "to", "for", "a", "an", "my", "i", "we", "today", "yesterday",
        "היום", "אתמול", "עוד", "and", "ו", "on", "portfolio", "into", "worth", "about", "כ", "בערך", "ב-", "ל-"}
TICKER_RE = re.compile(r"^\^?[A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z]{1,3})?$")
NUM = r"\d[\d,]*(?:\.\d+)?"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_date(text: str, today: dt.date) -> tuple[dt.date | None, str]:
    """Find a date; return it and the text without it."""
    pats = [
        (r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lambda m: dt.date(int(m[1]), int(m[2]), int(m[3]))),
        (r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", lambda m: dt.date(int(m[3]), int(m[2]), int(m[1]))),
        (r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b", lambda m: dt.date(2000 + int(m[3]), int(m[2]), int(m[1]))),
        (r"\b(\d{1,2})/(\d{1,2})\b", lambda m: dt.date(today.year, int(m[2]), int(m[1]))),
    ]
    for pat, make in pats:
        m = re.search(pat, text)
        if m:
            try:
                return make(m), text[: m.start()] + " " + text[m.end():]
            except ValueError:
                pass
    low = text.lower()
    if "שלשום" in low:
        return today - dt.timedelta(days=2), text.replace("שלשום", " ")
    if "אתמול" in low or "yesterday" in low:
        return today - dt.timedelta(days=1), re.sub(r"אתמול|yesterday", " ", text, flags=re.I)
    return None, text


def _strip_prefix(word: str) -> str:
    """Hebrew one-letter prefixes (ו, ב, ל, ה, ש, מ) glued to a word."""
    w = word.strip("-")
    while len(w) > 2 and w[0] in "ובלהשמ" and re.match(r"^[֐-׿]", w):
        w = w[1:].lstrip("-")
    return w


def parse(session: Session, text: str, default_pid: int, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    original = text.strip()
    t = " " + re.sub(r"\s+", " ", original) + " "
    out: dict = {"text": original, "missing": [], "warnings": []}

    # 1. portfolio by name (longest name first, so "אבא שלי" wins over "אבא")
    portfolios = sorted(session.exec(select(Portfolio)).all(), key=lambda p: -len(p.name))
    pid = default_pid
    for p in portfolios:
        name = re.escape(p.name.strip())
        m = re.search(rf"(?<![\w֐-׿])(?:של |לתיק של |בתיק של |לתיק |בתיק |[ולבהש])?{name}(?![\w֐-׿])", t, flags=re.I)
        if m:
            pid = p.id
            t = t[: m.start()] + " " + t[m.end():]
            break
    out["portfolio_id"] = pid
    port = session.get(Portfolio, pid)
    out["portfolio_name"] = port.name if port else ""

    # 2. type
    words = re.findall(r"[^\s]+", t)
    typ = None
    for w in words:
        lw = _strip_prefix(w.lower().strip(".,!?"))
        for k, vs in VERBS.items():
            if lw in vs or w.lower().strip(".,!?") in vs:
                typ = typ or k
    out["type"] = (typ or TxType.BUY).value
    if typ is None:
        out["warnings"].append("No action word found, assumed a purchase.")

    # 3. date
    date, t = _parse_date(t, today)
    out["date"] = (date or today).isoformat()

    # 4. money amounts with a currency ("1000 דולר", "$1,000", "₪5000", 'ש"ח')
    amount = amount_ccy = None
    cur_alt = "|".join(re.escape(k) for k in sorted(CURRENCY_WORDS, key=len, reverse=True))
    m = re.search(rf"(?:(?P<pre>[$₪])\s?(?P<n1>{NUM}))|(?:(?P<n2>{NUM})\s?(?P<post>{cur_alt})(?![\w֐-׿]))", t, flags=re.I)
    if m:
        amount = _num(m["n1"] or m["n2"])
        amount_ccy = CURRENCY_WORDS[(m["pre"] or m["post"]).lower()]
        t = t[: m.start()] + " " + t[m.end():]

    # 5. per-share price after a price word ("at 190", "במחיר 190", "ב-190")
    price = None
    pm = re.search(rf"(?:(?<![\w֐-׿])(?:at|@|במחיר|בשער|מחיר|שער|price)\s*(?P<a>{NUM}))|(?:(?<![\w֐-׿])ב-?\s?(?P<b>{NUM}))", t, flags=re.I)

    # 6. quantity: a number next to a share word, or the first remaining bare number
    qty = None
    qm = re.search(rf"(?P<n>{NUM})\s*(?:{'|'.join(SHARE_WORDS)})(?![\w֐-׿])", t, flags=re.I)
    if qm:
        qty = _num(qm["n"])
        t = t[: qm.start()] + " " + t[qm.end():]
        pm = re.search(rf"(?:(?<![\w֐-׿])(?:at|@|במחיר|בשער|מחיר|שער|price)\s*(?P<a>{NUM}))|(?:(?<![\w֐-׿])ב-?\s?(?P<b>{NUM}))", t, flags=re.I)
    if pm:
        price = _num(pm["a"] or pm["b"])
        t = t[: pm.start()] + " " + t[pm.end():]
    if qty is None:
        nm = re.search(rf"(?<![\w.]){NUM}(?![\w.])", t)
        if nm:
            n = _num(nm.group(0))
            if out["type"] in ("DEPOSIT", "WITHDRAWAL", "DIVIDEND") and amount is None:
                amount = n
            else:
                qty = n
            t = t[: nm.start()] + " " + t[nm.end():]

    # 7. ticker: an uppercase-looking token, a known Hebrew name, or a Yahoo search on what is left
    ticker = None
    leftovers = []
    for w in re.findall(r"[^\s,]+", t):
        raw = re.sub(r"^[\u0590-\u05FF]{1,2}-", "", w.strip(".,!?:;()\"'"))  # "מ-AAPL" -> "AAPL"
        if not raw:
            continue
        lw = raw.lower()
        bare = _strip_prefix(lw)
        if lw in STOP or bare in STOP or lw in SHARE_WORDS or any(lw in v or bare in v for v in VERBS.values()):
            continue
        if bare in HEBREW_NAMES or lw in HEBREW_NAMES:
            ticker = ticker or HEBREW_NAMES.get(bare) or HEBREW_NAMES[lw]
            continue
        # tickers are written in capitals (or have a .TA / ^ marker); lowercase words get searched
        if TICKER_RE.match(raw.upper()) and re.match(r"^[\^A-Za-z]", raw) and (raw == raw.upper() or "." in raw or raw.startswith("^")):
            ticker = ticker or raw.upper()
            continue
        leftovers.append(raw)
    if not ticker and leftovers and out["type"] not in ("DEPOSIT", "WITHDRAWAL"):
        q = " ".join(leftovers)
        try:
            items = [n for n in (normalize_quote(x) for x in get_provider().search(q, 5)) if n]
            if items:
                ticker = rank(items, q)[0]["symbol"]
                out["warnings"].append(f"Matched “{q}” to {ticker}. Check it is the right one.")
        except ProviderError:
            pass

    # 8. fill in the rest from market data
    typ_s = out["type"]
    if typ_s in ("BUY", "SELL", "DIVIDEND"):
        if not ticker:
            out["missing"].append("ticker")
        out["ticker"] = ticker or ""
    else:
        out["ticker"] = ""

    if typ_s in ("BUY", "SELL") and ticker:
        try:
            p = price_on(session, ticker, date or today)
        except NoPriceData as exc:
            out["missing"].append("ticker")
            out["warnings"].append(str(exc))
            p = None
        except ValueError as exc:
            out["warnings"].append(str(exc))
            p = None
        cur = (p or {}).get("currency") or currency_for(session, ticker)
        out["currency"] = cur
        if p:
            out["date"] = p["date"]
        if price is None and p:
            price = p["close"]
            out["price_source"] = "close"
        else:
            out["price_source"] = "you"
        if qty is None and amount is not None and price:
            amt = amount
            if amount_ccy and amount_ccy != cur:
                from .services import fx_series

                rate = float(align(fx_series(session), [dt.date.fromisoformat(out["date"])]).iloc[0])
                amt = convert(amount, amount_ccy, cur, rate)
                out["warnings"].append(f"Converted {amount:,.0f} {amount_ccy} at that day's rate.")
            qty = round(amt / price, 6)
        if qty is None:
            out["missing"].append("quantity")
        out["quantity"] = qty
        out["price"] = price
    else:
        out["quantity"] = 0
        out["price"] = amount
        out["currency"] = amount_ccy or (currency_for(session, ticker) if ticker else "ILS")
        if amount is None:
            out["missing"].append("amount")
    return out
