from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from .. import services
from ..db import get_session
from ..demo import load_demo
from ..market import search as search_mod
from ..market.cache import NoPriceData, ensure_history, price_on
from ..market.provider import ProviderError
from .. import portfolios as pf
from ..models import Portfolio, Transaction
from ..transactions import TxError, TxIn, export_csv, parse_csv, rows_to_tx, validate

router = APIRouter(prefix="/api")

# Which portfolio a request is about. Every portfolio-scoped endpoint takes ?portfolio=<id>.
PID = Query(1, alias="portfolio", ge=1)


def _bad(msg: str, status: int = 400, **extra):
    raise HTTPException(status_code=status, detail={"message": msg, **extra})


# ---------------------------------------------------------------- symbols

@router.get("/search")
def search(q: str = "", session: Session = Depends(get_session)):
    return search_mod.search(session, q)


@router.get("/symbols/recent")
def recent(session: Session = Depends(get_session)):
    return search_mod.recent(session)


@router.get("/symbols/popular")
def popular():
    return search_mod.popular()


class SelectIn(BaseModel):
    symbol: str
    name: str = ""
    exchange: str = ""
    type: str = ""
    currency: str = ""
    raw: bool = False


@router.post("/symbols/select")
def select_symbol(item: SelectIn, session: Session = Depends(get_session)):
    data = item.model_dump()
    if item.raw:
        try:
            ensure_history(session, item.symbol)
        except NoPriceData as exc:
            _bad(str(exc), 404)
        data["name"] = item.symbol.upper()
    meta = search_mod.remember_selection(session, data)
    if meta.currency not in ("USD", "ILS"):
        _bad(f"{meta.ticker} is not traded in USD or ILS. Only US and Tel Aviv listings are supported.")
    return search_mod.meta_dict(meta)


@router.get("/quote/{symbol}")
def quote(symbol: str, session: Session = Depends(get_session)):
    q = search_mod.quotes(session, [symbol]).get(symbol.upper())
    if not q:
        _bad(f"Yahoo didn't return a quote for {symbol.upper()}. Check the ticker or try again.", 404)
    return q


@router.get("/quotes")
def quotes(symbols: str = "", session: Session = Depends(get_session)):
    return search_mod.quotes(session, [s for s in symbols.split(",") if s][:25])


@router.get("/price/{symbol}")
def price(symbol: str, date: dt.date = Query(...), session: Session = Depends(get_session)):
    try:
        return price_on(session, symbol, date)
    except NoPriceData as exc:
        _bad(str(exc), 404)
    except ValueError as exc:
        _bad(str(exc), 422)


@router.get("/symbol/{symbol}/chart")
def symbol_chart(symbol: str, range: str = "1Y", session: Session = Depends(get_session)):
    try:
        return services.symbol_chart(session, symbol, range)
    except NoPriceData as exc:
        _bad(str(exc), 404)
    except ValueError as exc:
        _bad(str(exc), 422)


# ---------------------------------------------------------------- transactions

@router.get("/transactions")
def list_tx(pid: int = PID, session: Session = Depends(get_session)):
    rows = session.exec(select(Transaction).where(Transaction.portfolio_id == pid).order_by(Transaction.date.desc(), Transaction.id.desc())).all()
    return [services.tx_dict(t) for t in rows]


def _save(session: Session, data: TxIn, pid: int, tx: Transaction | None = None):
    if not session.get(Portfolio, pid):
        _bad("Portfolio not found.", 404)
    existing = list(session.exec(select(Transaction).where(Transaction.portfolio_id == pid)).all())
    try:
        data = validate(session, data, existing, exclude_id=tx.id if tx else None)
    except TxError as exc:
        raise HTTPException(status_code=422, detail={"message": "Check the highlighted fields.", "fields": exc.errors})
    tx = tx or Transaction(type=data.type, date=data.date, portfolio_id=pid)
    for k, v in data.model_dump().items():
        setattr(tx, k, v)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    services.bump_revision()
    return services.tx_dict(tx)


@router.post("/transactions")
def create_tx(data: TxIn, pid: int = PID, session: Session = Depends(get_session)):
    return _save(session, data, pid)


@router.put("/transactions/{tx_id}")
def update_tx(tx_id: int, data: TxIn, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if not tx:
        _bad("Transaction not found.", 404)
    return _save(session, data, tx.portfolio_id, tx)


@router.delete("/transactions/{tx_id}")
def delete_tx(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if not tx:
        _bad("Transaction not found.", 404)
    session.delete(tx)
    session.commit()
    services.bump_revision()
    return {"ok": True}


@router.post("/transactions/import")
async def import_csv(file: UploadFile = File(...), commit: bool = False, pid: int = PID, session: Session = Depends(get_session)):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    rows = parse_csv(text)
    if not rows:
        _bad("The file is empty or has no header row. See the CSV format in the README.")
    results = rows_to_tx(session, rows, pid)
    ok = [r for r in results if not r["errors"]]
    if commit:
        for r in ok:
            t = dict(r["tx"])
            t["date"] = dt.date.fromisoformat(t["date"])
            session.add(Transaction(**t, portfolio_id=pid))
        session.commit()
        services.bump_revision()
    return {"rows": results, "valid": len(ok), "invalid": len(results) - len(ok), "committed": commit}


@router.get("/transactions/export", response_class=PlainTextResponse)
def export(pid: int = PID, session: Session = Depends(get_session)):
    return PlainTextResponse(export_csv(session, pid), headers={"Content-Disposition": "attachment; filename=transactions.csv"})


# ---------------------------------------------------------------- analytics

def _wrap(fn, *args):
    try:
        return fn(*args)
    except NoPriceData as exc:
        _bad(str(exc), 502)
    except ValueError as exc:
        _bad(str(exc), 422)


@router.get("/portfolio")
def portfolio(range: str = "1Y", start: str | None = None, end: str | None = None, pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(services.portfolio_payload, session, range, start, end, pid)


@router.get("/holdings")
def holdings(range: str = "1Y", start: str | None = None, end: str | None = None, pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(services.holdings_payload, session, range, start, end, pid)


@router.get("/holdings/{ticker}")
def holding(ticker: str, range: str = "1Y", pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(services.holding_detail, session, ticker, range, pid)


@router.get("/risk")
def risk(range: str = "MAX", start: str | None = None, end: str | None = None, pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(services.risk_payload, session, range, start, end, pid)


class SwapIn(BaseModel):
    ticker: str
    replacement: str


@router.post("/whatif/swap")
def swap(body: SwapIn, pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(services.swap_payload, session, body.ticker, body.replacement, pid)


class CustomHolding(BaseModel):
    symbol: str
    weight: float


class CustomIn(BaseModel):
    holdings: list[CustomHolding]
    start: str
    initial: float = 10000
    monthly: float = 0
    rebalance: str = "none"


@router.post("/whatif/custom")
def custom(body: CustomIn, pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(services.custom_payload, session, [h.model_dump() for h in body.holdings], body.start,
                 body.initial, body.monthly, body.rebalance, pid)


# ---------------------------------------------------------------- settings & data

class SettingsIn(BaseModel):
    base_currency: str | None = None
    benchmarks: list[str] | None = None
    risk_free: float | None = None
    theme: str | None = None
    auto_dividends: bool | None = None


def _settings_dict(s):
    return {"base_currency": s.base_currency, "benchmarks": s.benchmark_list(), "risk_free": s.risk_free,
            "theme": s.theme, "auto_dividends": s.auto_dividends}


@router.get("/settings")
def get_settings(session: Session = Depends(get_session)):
    return _settings_dict(services.get_settings(session))


@router.put("/settings")
def put_settings(body: SettingsIn, session: Session = Depends(get_session)):
    s = services.get_settings(session)
    if body.base_currency is not None:
        if body.base_currency not in ("ILS", "USD"):
            _bad("Base currency must be ILS or USD.")
        s.base_currency = body.base_currency
    if body.benchmarks is not None:
        bl = [b.strip().upper() for b in body.benchmarks if b.strip()]
        for b in bl:
            try:
                ensure_history(session, b)
            except NoPriceData as exc:
                _bad(str(exc), 404)
        s.benchmarks = ",".join(dict.fromkeys(bl))
    if body.risk_free is not None:
        if not 0 <= body.risk_free < 0.5:
            _bad("Risk-free rate should be between 0% and 50%.")
        s.risk_free = body.risk_free
    if body.theme is not None:
        s.theme = body.theme if body.theme in ("light", "dark", "system") else "system"
    if body.auto_dividends is not None:
        s.auto_dividends = body.auto_dividends
    session.add(s)
    session.commit()
    services.bump_revision()
    return _settings_dict(s)


@router.post("/demo")
def demo(pid: int = PID, session: Session = Depends(get_session)):
    if session.exec(select(Transaction).where(Transaction.portfolio_id == pid)).first():
        _bad("Demo data can only be loaded into an empty portfolio.")
    try:
        n = load_demo(session, pid)
    except (NoPriceData, ValueError) as exc:
        session.rollback()
        _bad(str(exc), 502)
    services.bump_revision()
    return {"loaded": n}


@router.delete("/transactions")
def delete_all(pid: int = PID, session: Session = Depends(get_session)):
    session.exec(delete(Transaction).where(Transaction.portfolio_id == pid))
    session.commit()
    services.bump_revision()
    return {"ok": True}


# ---------------------------------------------------------------- portfolios

class PortfolioIn(BaseModel):
    name: str
    note: str = ""
    aliases: str = ""


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        _bad("Give the portfolio a name.")
    if len(name) > 60:
        _bad("Keep the name under 60 characters.")
    return name


def _clean_aliases(a: str) -> str:
    return ", ".join(dict.fromkeys(x.strip() for x in (a or "").split(",") if x.strip()))[:200]


@router.get("/portfolios")
def list_portfolios(session: Session = Depends(get_session)):
    from sqlalchemy import func

    counts = dict(session.exec(select(Transaction.portfolio_id, func.count()).group_by(Transaction.portfolio_id)).all())
    return [pf.portfolio_dict(p, counts.get(p.id, 0)) for p in pf.list_portfolios(session)]


@router.post("/portfolios")
def create_portfolio(body: PortfolioIn, session: Session = Depends(get_session)):
    p = Portfolio(name=_clean_name(body.name), note=body.note.strip(), aliases=_clean_aliases(body.aliases))
    session.add(p)
    session.commit()
    session.refresh(p)
    return pf.portfolio_dict(p)


@router.put("/portfolios/{pid}")
def update_portfolio(pid: int, body: PortfolioIn, session: Session = Depends(get_session)):
    p = session.get(Portfolio, pid)
    if not p:
        _bad("Portfolio not found.", 404)
    p.name, p.note, p.aliases = _clean_name(body.name), body.note.strip(), _clean_aliases(body.aliases)
    session.add(p)
    session.commit()
    return pf.portfolio_dict(p)


@router.delete("/portfolios/{pid}")
def delete_portfolio(pid: int, session: Session = Depends(get_session)):
    if len(pf.list_portfolios(session)) <= 1:
        _bad("You need at least one portfolio.")
    pf.delete_portfolio(session, pid)
    services.bump_revision()
    return {"ok": True}


@router.get("/overview")
def overview(range: str = "1Y", session: Session = Depends(get_session)):
    return _wrap(pf.overview_payload, session, range)


@router.get("/lookback")
def lookback(range: str = "1Y", start: str | None = None, end: str | None = None, pid: int = PID, session: Session = Depends(get_session)):
    return _wrap(pf.lookback_payload, session, range, start, end, pid)


@router.post("/holdings/import")
async def import_holdings(file: UploadFile = File(...), commit: bool = False, replace: bool = False, as_of: dt.date | None = None,
                          pid: int = PID, session: Session = Depends(get_session)):
    """Preview (commit=false) or import a holdings list. A file with several titled tables (one per
    portfolio) imports into those portfolios by name, creating any that don't exist."""
    if not session.get(Portfolio, pid):
        _bad("Portfolio not found.", 404)
    day = as_of or dt.date.today()
    try:
        table = pf.read_table(file.filename or "", await file.read())
        rows = pf.parse_holdings(session, table, day)
    except ValueError as exc:
        _bad(str(exc))
    except Exception:
        _bad("Couldn't read that file. Save it as .xlsx or .csv and try again.")
    if not rows:
        _bad("No holdings found. The file needs a header row with ticker, quantity and average price.")
    realized_rows = [r for r in rows if r.get("kind") == "realized"]
    rows_h = [r for r in rows if r.get("kind") != "realized"]
    valid = [r for r in rows_h if not r["errors"]]
    existing = {p.name.strip().lower(): p for p in pf.list_portfolios(session)}
    groups: dict[str, dict] = {}
    for r in rows:
        name = r.get("portfolio") or ""
        g = groups.setdefault(name, {"name": name, "exists": (name.lower() in existing) if name else True, "rows": 0, "valid": 0,
                                     "holding_now": 0, "realized_lines": 0, "realized_total": 0.0})
        if r.get("kind") == "realized":
            g["realized_lines"] += 1
            g["realized_total"] += r["amount"] or 0
            continue
        g["rows"] += 1
        g["valid"] += 0 if r["errors"] else 1
    for g in groups.values():
        p = existing.get(g["name"].lower()) if g["name"] else session.get(Portfolio, pid)
        if p:
            g["holding_now"] = len(session.exec(select(Transaction.id).where(Transaction.portfolio_id == p.id)).all())
    result = {"imported": 0, "realized_imported": 0, "portfolios": 0, "created": []}
    if commit:
        result = pf.import_holdings(session, pid, rows, day, replace)
        services.bump_revision()
    return {"rows": rows_h, "realized": realized_rows, "valid": len(valid), "invalid": len(rows_h) - len(valid), "committed": commit,
            "groups": list(groups.values()), **result}


@router.get("/metrics")
def metrics(portfolios: str = "", symbols: str = "SPY,QQQ,XLK", range: str = "1Y", session: Session = Depends(get_session)):
    from .. import metrics as mx

    pids = [int(x) for x in portfolios.split(",") if x.strip().isdigit()]
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:12]
    return _wrap(mx.metrics_payload, session, pids, syms, range)


class OmniIn(BaseModel):
    text: str


@router.post("/omnibox")
def omnibox(body: OmniIn, pid: int = PID, session: Session = Depends(get_session)):
    """Turn a sentence like "קניתי 5 מניות של QQQ" into a draft transaction (not saved)."""
    from .. import omnibox as ob

    if not body.text.strip():
        _bad("Type what you bought or sold.")
    return ob.parse(session, body.text[:300], pid)


class OmniCommitIn(BaseModel):
    draft: dict
    log_call: bool = True
    thesis: str = ""


@router.post("/omnibox/commit")
def omnibox_commit(body: OmniCommitIn, session: Session = Depends(get_session)):
    """Save a confirmed draft into every portfolio it names, and log the call."""
    from .. import omnibox as ob

    try:
        out = ob.commit(session, body.draft, body.log_call, body.thesis)
    except (ValueError, NoPriceData) as exc:
        _bad(str(exc))
    services.bump_revision()
    return out


# ---------------------------------------------------------------- recommendations

class CallIn(BaseModel):
    ticker: str
    action: str = "BUY"
    date: dt.date
    thesis: str = ""
    target: float | None = None


@router.get("/calls")
def list_calls(session: Session = Depends(get_session)):
    from .. import calls

    return _wrap(calls.calls_payload, session)


@router.post("/calls")
def create_call(body: CallIn, session: Session = Depends(get_session)):
    from .. import calls

    try:
        rec = calls.create_call(session, body.ticker, body.action, body.date, body.thesis, body.target)
    except NoPriceData as exc:
        _bad(str(exc), 404)
    except ValueError as exc:
        _bad(str(exc), 422)
    return {"id": rec.id}


@router.put("/calls/{rec_id}")
def update_call(rec_id: int, body: CallIn, session: Session = Depends(get_session)):
    from ..models import Recommendation

    rec = session.get(Recommendation, rec_id)
    if not rec:
        _bad("Recommendation not found.", 404)
    rec.thesis, rec.target = body.thesis.strip(), body.target
    session.add(rec)
    session.commit()
    return {"ok": True}


@router.delete("/calls/{rec_id}")
def delete_call(rec_id: int, session: Session = Depends(get_session)):
    """Removes the recommendation only; the trades stay in the portfolios."""
    from ..models import Recommendation

    rec = session.get(Recommendation, rec_id)
    if not rec:
        _bad("Recommendation not found.", 404)
    for t in session.exec(select(Transaction).where(Transaction.rec_id == rec_id)).all():
        t.rec_id = None
        session.add(t)
    session.delete(rec)
    session.commit()
    return {"ok": True}


@router.get("/report")
def report(period: str = "week", all: bool = False, pid: int = PID, session: Session = Depends(get_session)):
    from .. import reports

    if all:
        return _wrap(reports.all_report, session, period)
    return _wrap(reports.portfolio_report, session, pid, period)


# ---------------------------------------------------------------- telegram

class TelegramIn(BaseModel):
    token: str | None = None
    weekly_report: bool | None = None


def _telegram_dict(st) -> dict:
    from .. import telegram_bot

    return {"configured": bool(st.telegram_token), "paired": bool(st.telegram_chat_id), "pair_code": st.telegram_pair_code,
            "weekly_report": st.weekly_report, **telegram_bot.status()}


@router.get("/telegram")
def telegram_get(session: Session = Depends(get_session)):
    return _telegram_dict(services.get_settings(session))


@router.put("/telegram")
def telegram_put(body: TelegramIn, session: Session = Depends(get_session)):
    from .. import telegram_bot

    st = services.get_settings(session)
    if body.weekly_report is not None:
        st.weekly_report = body.weekly_report
    if body.token is not None:
        token = body.token.strip()
        if token and not re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token):
            _bad("That doesn't look like a bot token. It has the form 123456789:AAE... (from @BotFather).")
        st.telegram_token = token
        st.telegram_chat_id = ""
        st.telegram_pair_code = telegram_bot.new_pair_code() if token else ""
        telegram_bot.start(token)
    session.add(st)
    session.commit()
    return _telegram_dict(st)


@router.post("/telegram/pair")
def telegram_new_code(session: Session = Depends(get_session)):
    """Unpair the current chat and make a new code."""
    from .. import telegram_bot

    st = services.get_settings(session)
    st.telegram_chat_id = ""
    st.telegram_pair_code = telegram_bot.new_pair_code()
    session.add(st)
    session.commit()
    return _telegram_dict(st)


@router.get("/health")
def health():
    return {"ok": True}
