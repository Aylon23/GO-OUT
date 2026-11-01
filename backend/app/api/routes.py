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
from ..models import PriceCache, PriceCoverage, RecentSymbol, SplitEvent, SymbolMeta, Transaction
from ..transactions import TxError, TxIn, export_csv, parse_csv, rows_to_tx, validate

router = APIRouter(prefix="/api")


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
def list_tx(session: Session = Depends(get_session)):
    rows = session.exec(select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())).all()
    return [services.tx_dict(t) for t in rows]


def _save(session: Session, data: TxIn, tx: Transaction | None = None):
    existing = list(session.exec(select(Transaction)).all())
    try:
        data = validate(session, data, existing, exclude_id=tx.id if tx else None)
    except TxError as exc:
        raise HTTPException(status_code=422, detail={"message": "Check the highlighted fields.", "fields": exc.errors})
    tx = tx or Transaction(type=data.type, date=data.date)
    for k, v in data.model_dump().items():
        setattr(tx, k, v)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    services.bump_revision()
    return services.tx_dict(tx)


@router.post("/transactions")
def create_tx(data: TxIn, session: Session = Depends(get_session)):
    return _save(session, data)


@router.put("/transactions/{tx_id}")
def update_tx(tx_id: int, data: TxIn, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if not tx:
        _bad("Transaction not found.", 404)
    return _save(session, data, tx)


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
async def import_csv(file: UploadFile = File(...), commit: bool = False, session: Session = Depends(get_session)):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    rows = parse_csv(text)
    if not rows:
        _bad("The file is empty or has no header row. See the CSV format in the README.")
    results = rows_to_tx(session, rows)
    ok = [r for r in results if not r["errors"]]
    if commit:
        for r in ok:
            t = dict(r["tx"])
            t["date"] = dt.date.fromisoformat(t["date"])
            session.add(Transaction(**t))
        session.commit()
        services.bump_revision()
    return {"rows": results, "valid": len(ok), "invalid": len(results) - len(ok), "committed": commit}


@router.get("/transactions/export", response_class=PlainTextResponse)
def export(session: Session = Depends(get_session)):
    return PlainTextResponse(export_csv(session), headers={"Content-Disposition": "attachment; filename=transactions.csv"})


# ---------------------------------------------------------------- analytics

def _wrap(fn, *args):
    try:
        return fn(*args)
    except NoPriceData as exc:
        _bad(str(exc), 502)
    except ValueError as exc:
        _bad(str(exc), 422)


@router.get("/portfolio")
def portfolio(range: str = "1Y", start: str | None = None, end: str | None = None, session: Session = Depends(get_session)):
    return _wrap(services.portfolio_payload, session, range, start, end)


@router.get("/holdings")
def holdings(range: str = "1Y", start: str | None = None, end: str | None = None, session: Session = Depends(get_session)):
    return _wrap(services.holdings_payload, session, range, start, end)


@router.get("/holdings/{ticker}")
def holding(ticker: str, range: str = "1Y", session: Session = Depends(get_session)):
    return _wrap(services.holding_detail, session, ticker, range)


@router.get("/risk")
def risk(range: str = "MAX", start: str | None = None, end: str | None = None, session: Session = Depends(get_session)):
    return _wrap(services.risk_payload, session, range, start, end)


class SwapIn(BaseModel):
    ticker: str
    replacement: str


@router.post("/whatif/swap")
def swap(body: SwapIn, session: Session = Depends(get_session)):
    return _wrap(services.swap_payload, session, body.ticker, body.replacement)


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
def custom(body: CustomIn, session: Session = Depends(get_session)):
    return _wrap(services.custom_payload, session, [h.model_dump() for h in body.holdings], body.start,
                 body.initial, body.monthly, body.rebalance)


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
def demo(session: Session = Depends(get_session)):
    if session.exec(select(Transaction)).first():
        _bad("Demo data can only be loaded into an empty portfolio.")
    try:
        n = load_demo(session)
    except (NoPriceData, ValueError) as exc:
        session.rollback()
        _bad(str(exc), 502)
    services.bump_revision()
    return {"loaded": n}


@router.delete("/transactions")
def delete_all(session: Session = Depends(get_session)):
    session.exec(delete(Transaction))
    session.commit()
    services.bump_revision()
    return {"ok": True}


@router.get("/health")
def health():
    return {"ok": True}
