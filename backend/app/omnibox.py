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


def parse(session: Session, text: str, default_pid: int | None = None, today: dt.date | None = None,
          force_pids: list[int] | None = None, choose: dict | None = None) -> dict:
    """`default_pid` is ignored on purpose: a trade never lands in a portfolio the text did not name.
    `force_pids` is the user's pick from the portfolio picker; `choose` resolves "Did you mean…"
    ({"ticker": "AVGO", "quantity": 5})."""
    today = today or dt.date.today()
    choose = choose or {}
    original = text.strip()
    t = " " + re.sub(r"\s+", " ", original) + " "
    out: dict = {"text": original, "missing": [], "warnings": [], "choices": {}}

    # 1. portfolios by name, any number of them ("קרן, שיר ואבא"), or everyone ("כולם")
    portfolios = sorted(session.exec(select(Portfolio)).all(), key=lambda p: -len(p.name))
    matched: list[Portfolio] = []
    # every name and nickname, longest first, so "קרן ועידו" wins over "קרן"
    names = sorted(((n.strip(), p) for p in portfolios for n in [p.name, *(p.aliases or "").split(",")] if n.strip()),
                   key=lambda x: -len(x[0]))
    for n, p in names:
        if p in matched:
            continue
        m = re.search(rf"(?<![\w\u0590-\u05FF])(?:של |לתיק של |בתיק של |לתיק |בתיק |[ולבהש])?{re.escape(n)}(?![\w\u0590-\u05FF])", t, flags=re.I)
        if m:
            matched.append(p)
            t = t[: m.start()] + " " + t[m.end():]
    everyone = re.search(r"(?<![\w\u0590-\u05FF])(?:כולם|לכולם|כל התיקים|בכל התיקים|everyone|all portfolios|all of them)(?![\w\u0590-\u05FF])", t, flags=re.I)
    if everyone:
        t = t[: everyone.start()] + " " + t[everyone.end():]
    out["everyone"] = bool(everyone)
    out["portfolio_matched"] = bool(matched) or bool(everyone) or bool(force_pids)
    if force_pids:
        targets = sorted((p for p in portfolios if p.id in set(force_pids)), key=lambda p: p.id)
    elif everyone:
        targets = sorted(portfolios, key=lambda p: p.id)
    elif matched:
        targets = sorted(matched, key=lambda p: p.id)
    else:
        targets = []  # no name matched: ask, never guess
        out["missing"].append("portfolio")
    out["portfolio_id"] = targets[0].id if targets else None
    out["portfolio_name"] = targets[0].name if targets else ""
    out["_targets"] = [(p.id, p.name) for p in targets]
    sell_all = re.search(r"(?<![\w\u0590-\u05FF])(?:הכל|את הכל|all|everything)(?![\w\u0590-\u05FF])", t, flags=re.I)
    if sell_all:
        t = t[: sell_all.start()] + " " + t[sell_all.end():]

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
    out["date"] = (date or today).isoformat()  # what the user said, today by default

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

    # 6. quantity: numbers next to a share word, then any bare number left. More than one
    #    different candidate is a question for the user, not a guess.
    qty = None
    qty_cands: list[float] = []
    share_re = rf"(?P<n>{NUM})\s*(?:{'|'.join(SHARE_WORDS)})(?![\w\u0590-\u05FF])"
    for qm in list(re.finditer(share_re, t, flags=re.I)):
        qty_cands.append(_num(qm["n"]))
    t = re.sub(share_re, " ", t, flags=re.I)
    if qty_cands:
        pm = re.search(rf"(?:(?<![\w\u0590-\u05FF])(?:at|@|במחיר|בשער|מחיר|שער|price)\s*(?P<a>{NUM}))|(?:(?<![\w\u0590-\u05FF])ב-?\s?(?P<b>{NUM}))", t, flags=re.I)
    if pm:
        price = _num(pm["a"] or pm["b"])
        t = t[: pm.start()] + " " + t[pm.end():]
    for nm in list(re.finditer(rf"(?<![\w.]){NUM}(?![\w.])", t)):
        n = _num(nm.group(0))
        if out["type"] in ("DEPOSIT", "WITHDRAWAL", "DIVIDEND") and amount is None:
            amount = n
        else:
            qty_cands.append(n)
    t = re.sub(rf"(?<![\w.]){NUM}(?![\w.])", " ", t)
    distinct_q = list(dict.fromkeys(qty_cands))
    if choose.get("quantity") is not None:
        qty = float(choose["quantity"])
    elif len(distinct_q) == 1:
        qty = distinct_q[0]
    elif len(distinct_q) > 1:
        out["choices"]["quantity"] = distinct_q

    # 7. ticker: an uppercase-looking token, a known Hebrew name, or a Yahoo search on what is left
    ticker = None
    tickers: list[str] = []
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
            tickers.append(HEBREW_NAMES.get(bare) or HEBREW_NAMES[lw])
            continue
        # tickers are written in capitals (or have a .TA / ^ marker); lowercase words get searched
        if TICKER_RE.match(raw.upper()) and re.match(r"^[\^A-Za-z]", raw) and (raw == raw.upper() or "." in raw or raw.startswith("^")):
            tickers.append(raw.upper())
            continue
        leftovers.append(raw)
    tickers = list(dict.fromkeys(tickers))
    if choose.get("ticker"):
        ticker = str(choose["ticker"]).upper()
    elif len(tickers) == 1:
        ticker = tickers[0]
    elif len(tickers) > 1:
        out["choices"]["ticker"] = tickers
    if not ticker and not tickers and leftovers and out["type"] not in ("DEPOSIT", "WITHDRAWAL"):
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
        if not ticker and "ticker" not in out["choices"]:
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
            out["price_date"] = p["date"]  # the close used; the trade keeps the date the user gave
        if price is None and p:
            price = p["close"]
            out["price_source"] = "close"
        else:
            out["price_source"] = "you"
        if qty is None and amount is not None and price:
            amt = amount
            if amount_ccy and amount_ccy != cur:
                from .services import fx_series

                rate = float(align(fx_series(session), [dt.date.fromisoformat(out.get("price_date") or out["date"])]).iloc[0])
                amt = convert(amount, amount_ccy, cur, rate)
                out["warnings"].append(f"Converted {amount:,.0f} {amount_ccy} at that day's rate.")
            qty = round(amt / price, 6)
        if qty is None and "quantity" not in out["choices"] and not (typ_s == "SELL" and (sell_all or out["everyone"])):
            out["missing"].append("quantity")
        out["quantity"] = qty
        out["price"] = price
    elif typ_s in ("BUY", "SELL"):  # ticker still undecided ("Did you mean…"): keep what we know
        out["quantity"] = qty
        out["price"] = price
        out["currency"] = amount_ccy or "USD"
    else:
        out["quantity"] = 0
        out["price"] = amount
        out["currency"] = amount_ccy or (currency_for(session, ticker) if ticker else "ILS")
        if amount is None:
            out["missing"].append("amount")
    if out["choices"]:
        out["missing"].append("choice")
    out["targets"] = _targets(session, out, out.pop("_targets"), bool(sell_all))
    if out["targets"] and all(x["skip"] for x in out["targets"]) and "quantity" not in out["missing"]:
        out["missing"].append("position")
    return out


def _targets(session: Session, d: dict, targets: list[tuple[int, str]], sell_all: bool) -> list[dict]:
    """One line per portfolio. Selling with no quantity ("כולם מכרו TSLA") sells each whole position;
    portfolios that do not hold it are skipped."""
    from .services import Context, open_positions

    out = []
    for pid, name in targets:
        row = {"portfolio_id": pid, "portfolio_name": name, "quantity": d.get("quantity"), "held": None, "skip": False, "reason": ""}
        if d["type"] == "SELL" and d.get("ticker"):
            try:
                held = open_positions(Context(session, pid)).get(d["ticker"], 0.0)
            except Exception:
                held = 0.0
            row["held"] = held
            if held <= 0:
                row.update(skip=True, reason="doesn't hold it")
            elif row["quantity"] is None or sell_all:
                row["quantity"] = held
            elif row["quantity"] > held + 1e-9:
                row.update(skip=True, reason=f"holds only {held:g}")
        out.append(row)
    return out


def commit(session: Session, d: dict, log_call: bool = True, thesis: str = "") -> dict:
    """Save a confirmed draft: one transaction per portfolio, and (optionally) the recommendation."""
    from .calls import create_call
    from .models import Transaction
    from .transactions import TxError, TxIn, validate

    if d.get("missing"):
        raise ValueError("The draft is incomplete: " + ", ".join(d["missing"]))
    rec_id = None
    if log_call and d["type"] in ("BUY", "SELL") and d.get("ticker"):
        rec_id = create_call(session, d["ticker"], d["type"], dt.date.fromisoformat(d["date"]), thesis or d.get("text", "")).id
    saved, failed = [], []
    for tg in d.get("targets") or [{"portfolio_id": d["portfolio_id"], "portfolio_name": d["portfolio_name"], "quantity": d.get("quantity"), "skip": False}]:
        if tg.get("skip"):
            failed.append({"portfolio": tg["portfolio_name"], "error": tg.get("reason") or "skipped"})
            continue
        trade = d["type"] in ("BUY", "SELL")
        data = TxIn(ticker=d.get("ticker") or "", type=d["type"], date=dt.date.fromisoformat(d["date"]),
                    quantity=(tg["quantity"] or 0) if trade else 0, price=d.get("price") or 0, currency=d.get("currency") or "USD",
                    fees=0, note=(d.get("text") or "")[:200])
        existing = list(session.exec(select(Transaction).where(Transaction.portfolio_id == tg["portfolio_id"])).all())
        try:
            data = validate(session, data, existing)
        except TxError as exc:
            failed.append({"portfolio": tg["portfolio_name"], "error": " ".join(exc.errors.values())})
            continue
        tx = Transaction(**data.model_dump(), portfolio_id=tg["portfolio_id"], rec_id=rec_id)
        session.add(tx)
        session.commit()
        saved.append({"portfolio": tg["portfolio_name"], "quantity": tx.quantity})
    if rec_id and not saved:  # nothing followed the call: do not keep it
        from .models import Recommendation

        session.delete(session.get(Recommendation, rec_id))
        session.commit()
        rec_id = None
    return {"saved": saved, "failed": failed, "rec_id": rec_id}
