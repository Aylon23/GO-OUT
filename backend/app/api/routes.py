from __future__ import annotations

import datetime as dt

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


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        _bad("Give the portfolio a name.")
    if len(name) > 60:
        _bad("Keep the name under 60 characters.")
    return name


@router.get("/portfolios")
def list_portfolios(session: Session = Depends(get_session)):
    from sqlalchemy import func

    counts = dict(session.exec(select(Transaction.portfolio_id, func.count()).group_by(Transaction.portfolio_id)).all())
    return [pf.portfolio_dict(p, counts.get(p.id, 0)) for p in pf.list_portfolios(session)]


@router.post("/portfolios")
def create_portfolio(body: PortfolioIn, session: Session = Depends(get_session)):
    p = Portfolio(name=_clean_name(body.name), note=body.note.strip())
    session.add(p)
    session.commit()
    session.refresh(p)
    return pf.portfolio_dict(p)


@router.put("/portfolios/{pid}")
def update_portfolio(pid: int, body: PortfolioIn, session: Session = Depends(get_session)):
    p = session.get(Portfolio, pid)
    if not p:
        _bad("Portfolio not found.", 404)
    p.name, p.note = _clean_name(body.name), body.note.strip()
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
async def import_holdings(file: UploadFile = File(...), commit: bool = False, as_of: dt.date | None = None,
                          pid: int = PID, session: Session = Depends(get_session)):
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
        _bad("The file has no rows. The first row should be the column names.")
    valid = [r for r in rows if not r["errors"]]
    n = 0
    if commit:
        n = pf.import_holdings(session, pid, rows, day)
        services.bump_revision()
    return {"rows": rows, "valid": len(valid), "invalid": len(rows) - len(valid), "committed": commit, "imported": n}


@router.get("/health")
def health():
    return {"ok": True}
