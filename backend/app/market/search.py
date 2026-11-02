"""Symbol search on top of Yahoo's free search endpoint (yf.Search), with ranking, a 24h
in-memory cache and a local fallback when Yahoo is unreachable."""
from __future__ import annotations

import datetime as dt
import re
import threading
import time

from sqlmodel import Session, col, or_, select

from ..models import RecentSymbol, SymbolMeta
from .cache import guess_currency
from .names import clean_name
from .provider import ProviderError, get_provider

TYPE_MAP = {"EQUITY": "Stock", "ETF": "ETF", "INDEX": "Index", "MUTUALFUND": "Fund"}
US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NAS", "NYS", "PNK", "OQB", "OQX", "SNP", "DJI", "NIM", "CXI", "WCB"}
EXCHANGE_LABEL = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NIM": "NASDAQ",
    "NYQ": "NYSE", "NYS": "NYSE", "ASE": "NYSE American", "PCX": "NYSE Arca", "BTS": "Cboe",
    "TLV": "Tel Aviv", "SNP": "S&P", "DJI": "Dow Jones", "CXI": "Cboe", "PNK": "OTC",
}
POPULAR = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TEVA.TA"]
TTL = 24 * 3600

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def exchange_label(code: str, disp: str = "") -> str:
    return EXCHANGE_LABEL.get(code, disp or code)


def tier(item: dict) -> int:
    """0 = US primary listing, 1 = Tel Aviv, 2 = anything else."""
    if item["symbol"].endswith(".TA"):
        return 1
    if item.get("exchange_code") in US_EXCHANGES:
        return 0
    return 2


def normalize_quote(q: dict) -> dict | None:
    qt = (q.get("quoteType") or "").upper()
    if qt not in TYPE_MAP:
        return None  # drops options, futures, crypto, currencies
    sym = (q.get("symbol") or "").upper()
    if not sym or "=" in sym:
        return None
    code = q.get("exchange") or ""
    if sym.endswith(".TA"):
        currency = "ILS"
    elif code in US_EXCHANGES:
        currency = "USD"
    else:
        currency = ""
    return {
        "symbol": sym,
        "name": clean_name(q.get("longname") or q.get("shortname") or sym, sym),
        "exchange": exchange_label(code, q.get("exchDisp", "")),
        "exchange_code": code,
        "type": TYPE_MAP[qt],
        "currency": currency,
    }


def rank(items: list[dict], query: str) -> list[dict]:
    qu = query.strip().upper()
    order = {id(it): i for i, it in enumerate(items)}

    def key(it):
        sym = it["symbol"]
        exact = 0 if sym == qu else 1
        return (exact, tier(it), order[id(it)])

    seen, out = set(), []
    for it in sorted(items, key=key):
        if it["symbol"] in seen:
            continue
        seen.add(it["symbol"])
        out.append(it)
    return out


TICKER_RE = re.compile(r"^\^?[A-Za-z0-9][A-Za-z0-9.\-]{0,14}$")


def local_search(session: Session, query: str, limit: int = 10) -> list[dict]:
    q = query.strip()
    like = f"%{q}%"
    rows = session.exec(
        select(SymbolMeta).where(or_(col(SymbolMeta.ticker).ilike(like), col(SymbolMeta.name).ilike(like))).limit(limit)
    ).all()
    items = [{"symbol": r.ticker, "name": clean_name(r.name or r.ticker, r.ticker), "exchange": r.exchange, "exchange_code": "",
              "type": r.type or "Stock", "currency": r.currency or guess_currency(r.ticker)} for r in rows]
    return rank(items, q)


def search(session: Session, query: str, limit: int = 10) -> dict:
    q = query.strip()
    if not q:
        return {"results": [], "source": "none"}
    key = q.lower()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return {"results": hit[1], "source": "yahoo"}
    try:
        raw = get_provider().search(q, max_results=limit)
    except ProviderError:
        results = local_search(session, q, limit)
        raw_ok = TICKER_RE.match(q) is not None
        if raw_ok and not any(r["symbol"] == q.upper() for r in results):
            results.append({"symbol": q.upper(), "name": "Use this ticker as typed", "exchange": "", "exchange_code": "",
                            "type": "", "currency": guess_currency(q), "raw": True})
        return {"results": results, "source": "local", "error": "Yahoo search is unreachable. Showing symbols you already use."}
    items = [n for n in (normalize_quote(x) for x in raw) if n]
    results = rank(items, q)[:limit]
    with _lock:
        _cache[key] = (now, results)
    return {"results": results, "source": "yahoo"}


def remember_selection(session: Session, item: dict) -> SymbolMeta:
    sym = item["symbol"].upper()
    meta = session.get(SymbolMeta, sym) or SymbolMeta(ticker=sym)
    for field in ("name", "exchange", "type"):
        if item.get(field):
            setattr(meta, field, item[field])
    meta.name = clean_name(meta.name, sym)
    cur = item.get("currency") or meta.currency or guess_currency(sym)
    meta.currency = cur
    session.add(meta)
    session.merge(RecentSymbol(ticker=sym, selected_at=dt.datetime.now(dt.timezone.utc)))
    session.commit()
    session.refresh(meta)
    return meta


def recent(session: Session, limit: int = 5) -> list[dict]:
    rows = session.exec(select(RecentSymbol).order_by(col(RecentSymbol.selected_at).desc()).limit(limit)).all()
    out = []
    for r in rows:
        m = session.get(SymbolMeta, r.ticker)
        out.append(meta_dict(m) if m else {"symbol": r.ticker, "name": r.ticker, "exchange": "", "type": "", "currency": guess_currency(r.ticker)})
    return out


def meta_dict(m: SymbolMeta) -> dict:
    return {"symbol": m.ticker, "name": clean_name(m.name or m.ticker, m.ticker), "exchange": m.exchange, "type": m.type,
            "currency": m.currency or guess_currency(m.ticker), "sector": m.sector, "country": m.country}


POPULAR_META = {
    "SPY": ("SPDR S&P 500 ETF Trust", "NYSE Arca", "ETF", "USD"),
    "QQQ": ("Invesco QQQ Trust", "NASDAQ", "ETF", "USD"),
    "AAPL": ("Apple Inc.", "NASDAQ", "Stock", "USD"),
    "MSFT": ("Microsoft Corporation", "NASDAQ", "Stock", "USD"),
    "NVDA": ("NVIDIA Corporation", "NASDAQ", "Stock", "USD"),
    "TEVA.TA": ("Teva Pharmaceutical Industries", "Tel Aviv", "Stock", "ILS"),
}


def popular() -> list[dict]:
    return [{"symbol": s, "name": clean_name(n, s), "exchange": e, "type": t, "currency": c} for s, (n, e, t, c) in POPULAR_META.items()]


# ---- quotes (15 min cache) ----
QUOTE_TTL = 15 * 60
_quotes: dict[str, tuple[float, dict]] = {}


def quotes(session: Session, symbols: list[str]) -> dict[str, dict]:
    symbols = [s.upper() for s in symbols if s]
    now = time.time()
    out: dict[str, dict] = {}
    missing = []
    for s in symbols:
        hit = _quotes.get(s)
        if hit and now - hit[0] < QUOTE_TTL:
            out[s] = hit[1]
        else:
            missing.append(s)
    if missing:
        try:
            closes = get_provider().recent_closes(missing)
        except ProviderError:
            closes = {}
        for s in missing:
            pts = closes.get(s) or []
            if len(pts) < 1:
                q = _quote_from_cache(session, s)
                if q:
                    out[s] = q
                continue
            div = 100.0 if s.endswith(".TA") and not s.startswith("^") else 1.0
            last = pts[-1][1] / div
            prev = pts[-2][1] / div if len(pts) > 1 else last
            q = {"symbol": s, "price": last, "change_pct": (last / prev - 1.0) if prev else 0.0,
                 "date": pts[-1][0].isoformat(), "currency": guess_currency(s)}
            m = session.get(SymbolMeta, s)
            if m and m.currency in ("USD", "ILS"):
                q["currency"] = m.currency
            _quotes[s] = (now, q)
            out[s] = q
    return out


def _quote_from_cache(session: Session, s: str) -> dict | None:
    from ..models import PriceCache

    rows = session.exec(select(PriceCache).where(PriceCache.ticker == s).order_by(col(PriceCache.date).desc()).limit(2)).all()
    if not rows:
        return None
    last = rows[0].close
    prev = rows[1].close if len(rows) > 1 else last
    return {"symbol": s, "price": last, "change_pct": last / prev - 1.0 if prev else 0.0, "date": rows[0].date.isoformat(),
            "currency": rows[0].currency, "stale": True}


def clear_caches() -> None:
    _cache.clear()
    _quotes.clear()
