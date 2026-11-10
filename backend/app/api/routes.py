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


@router.get("/market/status")
def market_status(session: Session = Depends(get_session)):
    """US market state for the header chip, and when the prices on screen are from: the last check while
    the market is open, otherwise the last session's closing bell."""
    import datetime as dt

    from sqlmodel import func

    from ..market import clock
    from ..models import PriceCoverage
    st = clock.status()
    checked = session.exec(select(func.max(PriceCoverage.checked_at))).first()
    if checked is not None and checked.tzinfo is None:
        checked = checked.replace(tzinfo=dt.timezone.utc)
    close = dt.datetime.fromisoformat(st["last_close_at"])
    as_of = checked if st["state"] == "open" and checked else min(checked, close) if checked else close
    lab = clock.session_label(dt.date.fromisoformat(st["et_date"]) if st["state"] in ("open", "after") else dt.date.fromisoformat(st["last_session"]))
    return {**st, "as_of": as_of.isoformat(), "day_word": lab["word"]}


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
    anthropic_key: str | None = None
    allow_short_hedges: bool | None = None
    inbox_dir: str | None = None
    memo_language: str | None = None


def _settings_dict(s):
    key = (s.anthropic_key or "").strip()
    return {"base_currency": s.base_currency, "benchmarks": s.benchmark_list(), "risk_free": s.risk_free,
            "theme": s.theme, "auto_dividends": s.auto_dividends, "allow_short_hedges": bool(s.allow_short_hedges), "inbox_dir": s.inbox_dir or "", "memo_language": s.memo_language or "he",
            "anthropic_key_set": bool(key), "anthropic_key_hint": f"…{key[-4:]}" if len(key) > 8 else ""}


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
    if body.memo_language is not None:
        if body.memo_language not in ("he", "en"):
            _bad("Language must be he or en.")
        s.memo_language = body.memo_language
    if body.inbox_dir is not None:
        from pathlib import Path

        d = body.inbox_dir.strip()
        if d and not Path(d).expanduser().is_absolute():
            _bad("Use a full folder path, like C:\\Users\\you\\Documents\\inbox.")
        s.inbox_dir = d
    if body.allow_short_hedges is not None:
        s.allow_short_hedges = body.allow_short_hedges
    if body.anthropic_key is not None:
        key = body.anthropic_key.strip()
        if key and not key.startswith("sk-ant-"):
            _bad("That doesn't look like a Claude API key (they start with sk-ant-).")
        s.anthropic_key = key
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
    from ..market.names import auto_aliases

    name = _clean_name(body.name)
    p = Portfolio(name=name, note=body.note.strip(), aliases=_clean_aliases(body.aliases) or ", ".join(auto_aliases(name)))
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
    today: dt.date | None = None  # the user's local date (from the browser)
    portfolio_ids: list[int] | None = None  # chosen in the portfolio picker
    choose: dict | None = None  # answers to "Did you mean…"


@router.post("/omnibox")
def omnibox(body: OmniIn, session: Session = Depends(get_session)):
    """Turn a sentence like "קניתי 5 מניות של QQQ" into a draft transaction (not saved)."""
    from .. import omnibox as ob

    if not body.text.strip():
        _bad("Type what you bought or sold.")
    return ob.parse(session, body.text[:300], today=body.today, force_pids=body.portfolio_ids, choose=body.choose)


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


# ---------------------------------------------------------------- researched scenario specs

class SpecIn(BaseModel):
    text: str


def _extract_validate(session: Session, text: str):
    from .. import specs

    try:
        spec = specs.extract(text)
    except specs.SpecError as exc:
        _bad(str(exc))
    return spec, specs.validate(session, spec)


@router.post("/specs/validate")
def spec_validate(body: SpecIn, session: Session = Depends(get_session)):
    spec, report = _extract_validate(session, body.text)
    return services._clean({"spec": spec, "report": report})


@router.post("/specs")
def spec_save(body: SpecIn, session: Session = Depends(get_session)):
    from .. import specs

    spec, report = _extract_validate(session, body.text)
    if report["errors"]:
        _bad("The spec has errors; fix them before saving.", report=report)
    return services._clean(specs.row_dict(specs.save(session, spec, report, "paste")))


@router.get("/specs")
def spec_list(session: Session = Depends(get_session)):
    from .. import specs

    return specs.listing(session)


@router.get("/specs/{sid}")
def spec_get(sid: str, session: Session = Depends(get_session)):
    from .. import specs
    from ..models import ResearchedSpec

    row = session.get(ResearchedSpec, sid)
    if row is None:
        _bad("Scenario not found.", 404)
    return services._clean(specs.row_dict(row))


@router.delete("/specs/{sid}")
def spec_delete(sid: str, session: Session = Depends(get_session)):
    from ..models import ResearchedSpec

    row = session.get(ResearchedSpec, sid)
    if row is not None:
        session.delete(row)
        session.commit()
    return {"ok": True}


@router.get("/inbox")
def inbox_info(session: Session = Depends(get_session)):
    from .. import specs

    d = specs.inbox_dir(session)
    failed = sorted((d / "failed").glob("*.error.txt"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
    return {"path": str(d.resolve()), "failed": [{"file": f.name.replace(".error.txt", ""), "error": f.read_text(encoding="utf-8")[:2000]} for f in failed]}


@router.post("/inbox/check")
def inbox_check(session: Session = Depends(get_session)):
    from .. import specs

    return specs.process_inbox(session)


# ---------------------------------------------------------------- factor exposures

@router.get("/exposures")
def exposures(refresh: bool = False, session: Session = Depends(get_session)):
    from .. import factors

    if refresh:
        from ..scenarios import books

        _, fam = books(session, None)
        factors.exposures(session, [h.symbol for h in fam.holdings], refresh=True)
    return services._clean(factors.payload(session))


# ---------------------------------------------------------------- logos

_IMG_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}


@router.get("/logo/{symbol}", include_in_schema=False)
def logo(symbol: str, session: Session = Depends(get_session)):
    from fastapi.responses import FileResponse, Response
    from .. import logos

    got = logos.get_logo(session, symbol)
    if got is None:
        return Response(status_code=404, headers={"Cache-Control": "max-age=3600"})
    path, ctype = got
    return FileResponse(path, media_type=ctype, headers={"Cache-Control": "max-age=86400", **_IMG_HEADERS})


@router.post("/logo/{symbol}")
async def logo_upload(symbol: str, file: UploadFile = File(...), session: Session = Depends(get_session)):
    from .. import logos

    data = await file.read(logos.MAX_BYTES + 1)
    try:
        row = logos.set_manual(session, symbol, data)
    except ValueError as exc:
        _bad(str(exc))
    return {"symbol": row.symbol, "source": row.source}


@router.delete("/logo/{symbol}")
def logo_clear(symbol: str, session: Session = Depends(get_session)):
    from .. import logos

    logos.clear(session, symbol)
    return {"ok": True}


@router.get("/logos/overrides")
def logo_overrides(session: Session = Depends(get_session)):
    from .. import logos

    return logos.overrides(session)


# ---------------------------------------------------------------- scenarios

class ScenarioParseIn(BaseModel):
    text: str
    ai: bool = True


class ShockIn(BaseModel):
    symbol: str
    kind: str | None = None
    move: float


class ScenarioRunIn(BaseModel):
    shocks: list[ShockIn]
    horizon_weeks: float = 1.0
    events: list[str] = []
    used_events: list[str] = []
    portfolio_ids: list[int] | None = None
    regime: str = "normal"


class ScenarioSaveIn(BaseModel):
    name: str
    text: str = ""
    shocks: list[ShockIn]
    events: list[str] = []
    used_events: list[str] = []
    horizon_weeks: float = 1.0


@router.get("/scenario/library")
def scenario_library(session: Session = Depends(get_session)):
    from .. import scenarios
    return scenarios.library(session)


@router.post("/scenario/parse")
def scenario_parse(body: ScenarioParseIn, session: Session = Depends(get_session)):
    from .. import scenarios
    if not body.text.strip():
        _bad("Describe a scenario, like “oil jumps 20%”.")
    return services._clean(scenarios.parse(session, body.text.strip()[:500], use_ai=body.ai))


@router.post("/scenario/run")
def scenario_run(body: ScenarioRunIn, session: Session = Depends(get_session)):
    from .. import scenarios
    for s in body.shocks:
        if not -0.95 <= s.move <= 5:
            _bad(f"{s.symbol}: a move of {s.move:+.0%} is outside what the model can use (−95% to +500%).")
    if body.regime not in ("normal", "selloff"):
        _bad("Regime must be normal or selloff.")
    out = scenarios.run(session, [s.model_dump() for s in body.shocks], body.horizon_weeks, body.events, body.portfolio_ids,
                        regime=body.regime, used_events=body.used_events)
    return services._clean(out)


@router.get("/scenario/check")
def scenario_check(portfolio_ids: str = "", session: Session = Depends(get_session)):
    from .. import scenarios
    pids = [int(x) for x in portfolio_ids.split(",") if x.strip().isdigit()] or None
    return services._clean(scenarios.model_check(session, pids))


@router.get("/scenarios")
def scenarios_list(session: Session = Depends(get_session)):
    from .. import scenarios
    from ..models import Scenario
    return [scenarios.saved_dict(s) for s in session.exec(select(Scenario).order_by(Scenario.created_at.desc())).all()]


@router.post("/scenarios")
def scenarios_save(body: ScenarioSaveIn, session: Session = Depends(get_session)):
    import json
    from .. import scenarios
    from ..models import Scenario
    if not body.name.strip():
        _bad("Give the scenario a name.")
    row = Scenario(name=body.name.strip()[:80], text=body.text[:500], shocks=json.dumps([s.model_dump() for s in body.shocks]),
                   events=json.dumps(body.events), used_events=json.dumps(body.used_events), horizon_weeks=body.horizon_weeks)
    session.add(row)
    session.commit()
    session.refresh(row)
    return scenarios.saved_dict(row)


@router.delete("/scenarios/{sid}")
def scenarios_delete(sid: int, session: Session = Depends(get_session)):
    from ..models import Scenario
    row = session.get(Scenario, sid)
    if row is None:
        _bad("Scenario not found.", 404)
    session.delete(row)
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------- impact engine

class EditIn(BaseModel):
    branch: str
    factor: str
    mid: float


class ImpactSpecIn(BaseModel):
    regime: str | None = None  # None = by each branch's cause
    edits: list[EditIn] = []
    seed: int = 7


class ImpactShocksIn(ImpactSpecIn):
    shocks: list[ShockIn]
    horizon_weeks: float = 1.0
    used_events: list[str] = []
    text: str = ""


def _check_regime(r):
    if r not in (None, "normal", "selloff"):
        _bad("Regime must be normal or selloff.")


@router.post("/impact/spec/{sid}")
def impact_spec(sid: str, body: ImpactSpecIn, session: Session = Depends(get_session)):
    import json as _json
    from .. import impact
    from ..models import ResearchedSpec

    _check_regime(body.regime)
    row = session.get(ResearchedSpec, sid)
    if row is None:
        _bad("Scenario not found.", 404)
    out = impact.run(session, _json.loads(row.spec), regime=body.regime, edits=[e.model_dump() for e in body.edits], seed=body.seed)
    out["report"] = _json.loads(row.report)
    return services._clean(out)


@router.post("/impact/shocks")
def impact_shocks(body: ImpactShocksIn, session: Session = Depends(get_session)):
    from .. import impact

    _check_regime(body.regime)
    if not body.shocks:
        _bad("Add at least one move.")
    spec = impact.spec_from_shocks(session, [s.model_dump() for s in body.shocks], body.horizon_weeks, body.text[:80], body.text,
                                   body.used_events, body.regime)
    out = impact.run(session, spec, regime=body.regime, edits=[e.model_dump() for e in body.edits], seed=body.seed)
    return services._clean(out)


# ---------------------------------------------------------------- actions, new money, watchlist

class ActionsIn(ImpactSpecIn):
    spec_id: str | None = None
    shocks: list[ShockIn] = []
    horizon_weeks: float = 1.0
    used_events: list[str] = []
    text: str = ""


def _spec_from(body, session):
    import json as _json
    from .. import impact
    from ..models import ResearchedSpec

    if body.spec_id:
        row = session.get(ResearchedSpec, body.spec_id)
        if row is None:
            _bad("Scenario not found.", 404)
        return _json.loads(row.spec)
    if not body.shocks:
        _bad("Give a scenario: a researched one or at least one move.")
    return impact.spec_from_shocks(session, [s.model_dump() for s in body.shocks], body.horizon_weeks, body.text[:80], body.text,
                                   body.used_events, body.regime)


@router.post("/actions")
def scenario_actions(body: ActionsIn, session: Session = Depends(get_session)):
    from .. import actions

    _check_regime(body.regime)
    spec = _spec_from(body, session)
    return services._clean(actions.actions(session, spec, regime=body.regime, edits=[e.model_dump() for e in body.edits], seed=body.seed))


class NewMoneyIn(BaseModel):
    amount: float
    currency: str = "USD"
    person: int | None = None
    seed: int = 7
    use_book: bool = False


@router.post("/new-money")
def new_money(body: NewMoneyIn, session: Session = Depends(get_session)):
    from .. import actions

    if body.amount <= 0:
        _bad("Enter an amount above zero.")
    if body.currency not in ("ILS", "USD"):
        _bad("Currency must be ILS or USD.")
    if body.use_book:
        from .. import book

        entries = [e for e in book.entries_of(book.current(session)) if float(e["probability"]) > 0]
        if not entries:
            _bad("Your book has no scenarios yet.")
        specs = {s["id"]: s for s in actions.saved_specs(session)}
        chosen = [specs[e["scenario_id"]] for e in entries if e["scenario_id"] in specs]
        weights = {e["scenario_id"]: float(e["probability"]) for e in entries}
        weights["normal"] = max(0.0, 1 - sum(weights.values()))
        out = actions.new_money(session, body.amount, body.currency, body.person, scenarios=chosen, weights=weights, seed=body.seed)
        out["book"] = True
        return services._clean(out)
    return services._clean(actions.new_money(session, body.amount, body.currency, body.person, seed=body.seed))


# ---------------------------------------------------------------- scenario book

class BookEntryIn(BaseModel):
    scenario_id: str
    probability: float


class BookIn(BaseModel):
    entries: list[BookEntryIn]
    note: str = ""


@router.get("/book/scenarios")
def book_available(session: Session = Depends(get_session)):
    from .. import actions

    return [{"id": s["id"], "title": s["title"], "kind": s["kind"], "horizon": s["spec"].get("horizon"),
             "branches": len(s["spec"].get("branches", []))} for s in actions.saved_specs(session)]


@router.get("/book")
def book_get(session: Session = Depends(get_session)):
    from .. import book

    v = book.current(session)
    entries = book.entries_of(v)
    return services._clean({"version": v.id if v else None, "saved_at": v.created_at.isoformat() if v else None, "entries": entries,
                            "result": book.run(session, entries), "signposts": book.signposts(session, entries)})


@router.put("/book")
def book_put(body: BookIn, session: Session = Depends(get_session)):
    from .. import book

    try:
        v = book.save(session, [e.model_dump() for e in body.entries], body.note)
    except book.BookError as exc:
        _bad(str(exc))
    entries = book.entries_of(v)
    return services._clean({"version": v.id, "saved_at": v.created_at.isoformat(), "entries": entries,
                            "result": book.run(session, entries), "signposts": book.signposts(session, entries)})


@router.get("/book/versions")
def book_versions(session: Session = Depends(get_session)):
    from .. import book

    return book.versions(session)


@router.get("/book/compare")
def book_compare(a: int, b: int, session: Session = Depends(get_session)):
    from .. import book

    try:
        return services._clean(book.compare(session, a, b))
    except book.BookError as exc:
        _bad(str(exc), 404)


# ---------------------------------------------------------------- stress tests, memo, journal, risk summary

@router.get("/stress")
def stress(session: Session = Depends(get_session)):
    from .. import stresstests

    return services._clean(stresstests.stress_suite(session))


@router.get("/stress/reverse")
def stress_reverse(session: Session = Depends(get_session)):
    from .. import stresstests

    return services._clean(stresstests.reverse(session))


@router.get("/risk/summary")
def risk_summary(session: Session = Depends(get_session)):
    from .. import stresstests

    return services._clean(stresstests.risk_summary(session))


class MemoIn(ActionsIn):
    lang: str | None = None


def _memo(body: MemoIn, session: Session) -> dict:
    import json as _json
    from .. import memo
    from ..models import ResearchedSpec

    _check_regime(body.regime)
    spec = _spec_from(body, session)
    report = None
    if body.spec_id:
        report = _json.loads(session.get(ResearchedSpec, body.spec_id).report)
    lang = body.lang or services.get_settings(session).memo_language or "he"
    if lang not in ("he", "en"):
        _bad("Language must be he or en.")
    m = memo.build(session, spec, report, lang=lang, regime=body.regime)
    if m.get("empty"):
        _bad("Add holdings first.")
    return m


@router.post("/memo")
def memo_json(body: MemoIn, session: Session = Depends(get_session)):
    return services._clean(_memo(body, session))


@router.post("/memo/pdf")
def memo_pdf(body: MemoIn, session: Session = Depends(get_session)):
    from fastapi.responses import Response
    from .. import render

    m = _memo(body, session)
    return Response(render.memo_pdf(m), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="memo.pdf"'})


@router.post("/memo/card")
def memo_card(body: MemoIn, session: Session = Depends(get_session)):
    from fastapi.responses import Response
    from .. import render

    m = _memo(body, session)
    return Response(render.memo_card(m), media_type="image/png", headers={"Content-Disposition": 'attachment; filename="memo.png"'})


@router.get("/scenario-journal")
def scenario_journal_panel(session: Session = Depends(get_session)):
    from .. import scenario_journal

    return services._clean(scenario_journal.panel(session))


# ---------------------------------------------------------------- periodic reports (image card + PDF)

class ReportsIn(BaseModel):
    kind: str = "weekly"
    send: bool = False
    lang: str | None = None


@router.get("/reports/list")
def reports_list(session: Session = Depends(get_session)):
    from .. import person_reports

    return person_reports.listing(session)


@router.post("/reports/generate")
def reports_generate(body: ReportsIn, session: Session = Depends(get_session)):
    from .. import person_reports

    if body.kind not in ("weekly", "monthly"):
        _bad("Kind must be weekly or monthly.")
    if body.lang not in (None, "he", "en"):
        _bad("Language must be he or en.")
    today = dt.date.today()
    if body.kind == "weekly":
        sunday = today - dt.timedelta(days=(today.weekday() + 1) % 7)
        start, end = person_reports.week_period(sunday)
    else:
        start, end = person_reports.month_period(today.replace(day=1))
    made = person_reports.generate(session, body.kind, start, end, body.lang)
    errors = []
    if body.send:
        for r in made:
            try:
                person_reports.send(session, r)
            except Exception as exc:
                errors.append(str(exc))
                break
    return {"made": len(made), "period_start": start.isoformat(), "period_end": end.isoformat(), "send_errors": errors,
            "reports": person_reports.listing(session)[: len(made)]}


@router.get("/reports/file/{rid}.{ext}")
def reports_file(rid: int, ext: str, session: Session = Depends(get_session)):
    from fastapi.responses import FileResponse
    from .. import person_reports
    from ..models import PersonReport

    r = session.get(PersonReport, rid)
    if r is None or ext not in ("png", "pdf"):
        _bad("Report not found.", 404)
    path = person_reports.reports_dir() / (r.png if ext == "png" else r.pdf)
    if not path.exists():
        _bad("The file is missing; generate the report again.", 404)
    return FileResponse(path, media_type="image/png" if ext == "png" else "application/pdf", filename=path.name)


@router.post("/reports/{rid}/send")
def reports_send(rid: int, session: Session = Depends(get_session)):
    from .. import person_reports
    from ..models import PersonReport

    r = session.get(PersonReport, rid)
    if r is None:
        _bad("Report not found.", 404)
    try:
        person_reports.send(session, r)
    except Exception as exc:
        _bad(str(exc))
    return {"ok": True}


# ---------------------------------------------------------------- earnings

@router.get("/earnings")
def earnings_get(date: dt.date | None = None, session: Session = Depends(get_session)):
    from .. import earnings

    return services._clean(earnings.brief_payload(session, date))


@router.get("/earnings/candidates")
def earnings_candidates(date: dt.date | None = None, session: Session = Depends(get_session)):
    from .. import earnings

    day = date or earnings.last_trading_day(session)
    return {"date": day.isoformat(), "candidates": earnings.candidates(session, day)}


@router.get("/earnings/journal")
def earnings_journal(session: Session = Depends(get_session)):
    from .. import earnings

    return services._clean(earnings.journal(session))


@router.post("/earnings/import")
def earnings_import(body: SpecIn, session: Session = Depends(get_session)):
    from .. import earnings, specs

    try:
        brief = specs.extract(body.text, ("earnings_brief",))
        return earnings.import_brief(session, brief)
    except (specs.SpecError, earnings.BriefError) as exc:
        _bad(str(exc))


@router.get("/watchlist")
def watchlist(session: Session = Depends(get_session)):
    from ..models import Watch

    return [w.symbol for w in session.exec(select(Watch).order_by(Watch.added_at)).all()]


@router.post("/watchlist/{symbol}")
def watch_add(symbol: str, session: Session = Depends(get_session)):
    from ..models import Watch

    sym = symbol.upper().strip()
    try:
        ensure_history(session, sym)
    except NoPriceData as exc:
        _bad(str(exc), 404)
    if session.get(Watch, sym) is None:
        session.add(Watch(symbol=sym))
        session.commit()
    return {"ok": True, "symbol": sym}


@router.delete("/watchlist/{symbol}")
def watch_remove(symbol: str, session: Session = Depends(get_session)):
    from ..models import Watch

    w = session.get(Watch, symbol.upper())
    if w is not None:
        session.delete(w)
        session.commit()
    return {"ok": True}


@router.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- AI (OpenRouter, free models only)

class AiSettingsIn(BaseModel):
    free_only: bool | None = None
    roster: list[dict] | None = None
    reset_roster: bool = False


def _ai_status(session: Session) -> dict:
    from .. import ai_jobs, openrouter

    key = openrouter.api_key()
    return {"key_set": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else "",
            "free_only": openrouter.free_only(session), "roster": openrouter.roster(session),
            "quota": openrouter.quota_status(session), "pace_per_min": openrouter.PACE_PER_MIN,
            "jobs": ai_jobs.listing(session, 10)}


@router.get("/ai/status")
def ai_status(session: Session = Depends(get_session)):
    return _ai_status(session)


@router.get("/ai/quota")
def ai_quota(refresh: bool = False, session: Session = Depends(get_session)):
    from .. import openrouter

    if refresh and openrouter.api_key():
        try:
            openrouter.refresh_quota(session, force=True)
        except Exception as exc:
            return {**openrouter.quota_status(session, refresh=False), "error": str(exc)}
    return openrouter.quota_status(session)


@router.put("/ai/settings")
def ai_settings(body: AiSettingsIn, session: Session = Depends(get_session)):
    import json as _json

    from .. import openrouter

    s = services.get_settings(session)
    if body.free_only is not None:
        s.ai_free_only = body.free_only
    if body.reset_roster:
        s.ai_roster = ""
    elif body.roster is not None:
        roles = []
        for r in body.roster:
            models = [str(m).strip() for m in (r.get("models") or []) if str(m).strip()][:3]
            if not models:
                _bad(f"{r.get('label') or r.get('role')}: set a model.")
            roles.append({"role": str(r.get("role") or ""), "label": str(r.get("label") or r.get("role") or ""), "models": models})
        s.ai_roster = _json.dumps(roles)
    # with free-only on, every model in the roster must be a :free variant
    if s.ai_free_only:
        bad = [m for r in (_json.loads(s.ai_roster) if s.ai_roster else openrouter.DEFAULT_ROSTER) for m in r["models"] if not m.endswith(":free")]
        if bad:
            _bad(f"Free models only is on: {', '.join(bad)} is not a :free model.")
    session.add(s)
    session.commit()
    return _ai_status(session)


@router.get("/ai/models")
def ai_models():
    from .. import openrouter

    return openrouter.free_models()


@router.get("/ai/calls")
def ai_calls(limit: int = 30, session: Session = Depends(get_session)):
    from .. import openrouter

    return openrouter.recent_calls(session, min(200, max(1, limit)))


@router.get("/ai/jobs")
def ai_jobs_list(session: Session = Depends(get_session)):
    from .. import ai_jobs

    return ai_jobs.listing(session)


@router.post("/ai/jobs/{job_id}/resume")
def ai_job_resume(job_id: int, session: Session = Depends(get_session)):
    from .. import ai_jobs

    ai_jobs.resume(session, job_id)
    return ai_jobs.listing(session)


@router.get("/ai/context-pack")
def ai_context_pack(tickers: str = "", analogs: bool = True, session: Session = Depends(get_session)):
    """The context pack as it would be sent (redacted view): no names or amounts, with the privacy check."""
    from .. import context_pack

    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]
    pack = context_pack.build(session, syms, include_analogs=analogs)
    return context_pack.redacted_view(session, pack)


# ---------------------------------------------------------------- backups

class BackupSettingsIn(BaseModel):
    extra_dir: str = ""


class RestoreIn(BaseModel):
    stem: str
    confirm: bool = False


@router.get("/backups/last")
def backups_last(session: Session = Depends(get_session)):
    """Last backup time and the 'no backup for 3 days' flag."""
    from .. import backup

    return backup.last(session)


@router.get("/backups")
def backups_status(session: Session = Depends(get_session)):
    from .. import backup

    return backup.status(session)


@router.post("/backups")
def backups_make(session: Session = Depends(get_session)):
    from .. import backup

    try:
        r = backup.make(session, "manual")
    except backup.BackupError as exc:
        _bad(str(exc))
    backup.sync_queue_flag(session)
    return {**backup.status(session), "made": r}


@router.put("/backups/settings")
def backups_settings(body: BackupSettingsIn, session: Session = Depends(get_session)):
    from .. import audit, backup

    path = body.extra_dir.strip()
    if path:
        problem = backup.check_folder(path)
        if problem:
            _bad(problem)
    s = services.get_settings(session)
    old = s.backup_dir_extra
    s.backup_dir_extra = path
    session.add(s)
    session.commit()
    audit.record(session, "settings", "Second backup folder " + (f"set to {path}" if path else "removed"),
                 {"backup_dir_extra": path}, undo={"backup_dir_extra": old})
    return backup.status(session)


@router.post("/backups/restore")
def backups_restore(body: RestoreIn, session: Session = Depends(get_session)):
    from .. import backup

    if not body.confirm:
        _bad("Confirm the restore first.")
    try:
        r = backup.restore(session, body.stem)
    except backup.BackupError as exc:
        _bad(str(exc))
    return r


@router.post("/backups/undo-restore")
def backups_undo_restore(session: Session = Depends(get_session)):
    from .. import backup

    try:
        return backup.undo_last_restore(session)
    except backup.BackupError as exc:
        _bad(str(exc))


# ---------------------------------------------------------------- AI scenario debate

class DebateIn(BaseModel):
    text: str
    horizon: str | None = None
    cause: str | None = None


@router.post("/debates")
def debate_start(body: DebateIn, wait: bool = False, session: Session = Depends(get_session)):
    """Reads the question; if the horizon or cause is missing, returns the chips to ask with (nothing is sent)."""
    from .. import debate, debate_input

    if not body.text.strip():
        _bad("Write the scenario first.")
    inp = debate_input.read(session, body.text, body.horizon, body.cause)
    if inp["needs"]:
        return {"needs": inp["needs"], "read": inp}
    d = debate.start(session, inp["text"], inp["horizon"], inp["cause"], inp, background=not wait)
    return debate.payload(session, d)


@router.get("/debates")
def debate_list(session: Session = Depends(get_session)):
    from .. import debate

    return debate.listing(session)


@router.get("/debates/leaderboard")
def debate_board(session: Session = Depends(get_session)):
    from .. import debate

    debate.score_due(session)
    return debate.leaderboard(session)


@router.get("/debates/{did}")
def debate_get(did: int, session: Session = Depends(get_session)):
    from .. import debate
    from ..models import Debate

    d = session.get(Debate, did)
    if d is None:
        _bad("That debate isn't there.", 404)
    return debate.payload(session, d)


@router.post("/debates/{did}/queue")
def debate_queue(did: int, session: Session = Depends(get_session)):
    from .. import debate
    from ..models import Debate

    d = session.get(Debate, did)
    if d is None or d.phase != "done":
        _bad("The debate hasn't finished.")
    return {"added": debate.send_to_queue(session, d)}


@router.post("/debates/{did}/book")
def debate_book(did: int, session: Session = Depends(get_session)):
    """Adds the merged spec to My book at 10% (or what's left); set your own probability there."""
    from .. import book
    from ..models import Debate

    d = session.get(Debate, did)
    if d is None or not d.spec_id:
        _bad("The debate hasn't finished.")
    entries = book.entries_of(book.current(session))
    if any(e["scenario_id"] == d.spec_id for e in entries):
        _bad("It's already in My book.")
    room = 1 - sum(float(e["probability"]) for e in entries)
    p = round(min(0.10, room), 4)
    if p <= 0:
        _bad("My book is at 100%. Lower another scenario's probability first.")
    try:
        book.save(session, [*entries, {"scenario_id": d.spec_id, "probability": p}], note=f"Added from AI debate #{did}")
    except book.BookError as exc:
        _bad(str(exc))
    return {"probability": p}


@router.get("/actions")
def actions_list(source: str | None = None, portfolio: int | None = None, session: Session = Depends(get_session)):
    from .. import action_queue

    return action_queue.listing(session, source=source, portfolio_id=portfolio)


class ActionStatusIn(BaseModel):
    status: str
    reason: str = ""


@router.post("/actions/{item_id}")
def actions_set(item_id: int, body: ActionStatusIn, session: Session = Depends(get_session)):
    from .. import action_queue

    if body.status not in ("done", "snoozed", "dismissed", "open"):
        _bad("Unknown status.")
    action_queue.set_status(session, item_id, body.status, body.reason)
    return {"ok": True}


# ---------------------------------------------------------------- AI stock council

class CouncilRunIn(BaseModel):
    tickers: list[str] | None = None


class CouncilSettingsIn(BaseModel):
    min_weight: bool | None = None
    weekday: int | None = None


@router.get("/council")
def council_overview(session: Session = Depends(get_session)):
    from .. import council

    return council.overview(session)


@router.post("/council/run")
def council_run(body: CouncilRunIn, session: Session = Depends(get_session)):
    from .. import council

    run = council.start(session, body.tickers, kind="stock" if body.tickers else "full")
    return council.run_status(session, run)


@router.put("/council/settings")
def council_settings(body: CouncilSettingsIn, session: Session = Depends(get_session)):
    from .. import council

    s = services.get_settings(session)
    if body.min_weight is not None:
        s.council_min_weight = body.min_weight
    if body.weekday is not None:
        if not -1 <= body.weekday <= 6:
            _bad("Pick a day of the week, or off.")
        s.council_weekday = body.weekday
    session.add(s)
    session.commit()
    return council.overview(session)


@router.get("/council/leaderboard")
def council_board(session: Session = Depends(get_session)):
    from .. import council

    return council.leaderboard(session)


@router.get("/council/actions")
def council_actions(portfolio: int = 1, session: Session = Depends(get_session)):
    from .. import council

    return council.actions_for(session, portfolio)


@router.get("/council/stock/{ticker}")
def council_stock(ticker: str, session: Session = Depends(get_session)):
    from .. import council

    return council.stock_detail(session, ticker)


# ---------------------------------------------------------------- history import with reconciliation

class HistoryIn(BaseModel):
    portfolio: int
    text: str
    filename: str = ""


class ApplyIn(BaseModel):
    decisions: dict[str, str] = {}


def _history_call(fn, *args):
    from .. import history_import

    try:
        return fn(*args)
    except history_import.HistoryError as exc:
        _bad(str(exc))


@router.post("/history/validate")
def history_validate(body: HistoryIn, session: Session = Depends(get_session)):
    """Per-row results (new / duplicate / error) with errors and warnings. Nothing is saved."""
    from .. import history_import

    return _history_call(history_import.validate, session, body.portfolio, body.text)


@router.post("/history/preview")
def history_preview(body: HistoryIn, session: Session = Depends(get_session)):
    """Validation plus the reconciliation diff against opening positions. Nothing is saved."""
    from .. import history_import

    return _history_call(history_import.preview, session, body.portfolio, body.text)


@router.post("/history/import")
def history_import_rows(body: HistoryIn, session: Session = Depends(get_session)):
    """Stage the new rows; applied at once when the portfolio has no opening positions, else waits for /apply."""
    from .. import history_import

    return _history_call(history_import.import_rows, session, body.portfolio, body.text, body.filename)


@router.get("/history/batches")
def history_batches(portfolio: int | None = None, session: Session = Depends(get_session)):
    from .. import history_import

    return history_import.batches(session, portfolio)


@router.get("/history/batches/{batch_id}/reconcile")
def history_reconcile(batch_id: int, session: Session = Depends(get_session)):
    from .. import history_import
    from ..models import ImportBatch

    b = session.get(ImportBatch, batch_id)
    if b is None:
        _bad("That import isn't there.", 404)
    return history_import.reconcile_view(session, b)


@router.post("/history/batches/{batch_id}/apply")
def history_apply(batch_id: int, body: ApplyIn, session: Session = Depends(get_session)):
    from .. import history_import

    return _history_call(history_import.apply, session, batch_id, body.decisions)


@router.post("/history/batches/{batch_id}/undo")
def history_undo(batch_id: int, session: Session = Depends(get_session)):
    from .. import history_import

    return _history_call(history_import.undo, session, batch_id)


@router.get("/history/suggested-calls")
def history_suggested(refresh: bool = False, session: Session = Depends(get_session)):
    from .. import history_import

    if refresh:
        history_import.infer_calls(session)
    return history_import.suggested(session)


@router.post("/history/suggested-calls/{rec_id}/approve")
def history_suggested_approve(rec_id: int, session: Session = Depends(get_session)):
    from .. import history_import

    return _history_call(history_import.decide_suggestion, session, rec_id, True)


@router.post("/history/suggested-calls/{rec_id}/dismiss")
def history_suggested_dismiss(rec_id: int, session: Session = Depends(get_session)):
    from .. import history_import

    return _history_call(history_import.decide_suggestion, session, rec_id, False)


# ---------------------------------------------------------------- the analyst panel (paid models)

class PanelIn(BaseModel):
    text: str
    horizon: str = "1m"
    web: bool = True


class PanelSettingsIn(BaseModel):
    budget_usd: float | None = None
    models: dict[str, str] | None = None  # seat key -> model id; "" = pick the newest flagship


def _panel_setup(session: Session) -> dict:
    from .. import openrouter, panel

    seats = openrouter.panel_seats(session)
    try:
        msgs, symbols = panel.question_message(session, "", "1m")
        chars = len(msgs[0]["content"])
    except Exception:
        chars, symbols = 0, []
    return {"key_set": bool(openrouter.api_key()), "seats": seats, "budget": openrouter.budget_status(session),
            "holdings": len(symbols), "estimate_usd": {"web": panel.estimate_cost(seats, chars, True), "no_web": panel.estimate_cost(seats, chars, False)},
            "horizons": list(panel.HORIZON_DAYS)}


@router.get("/panel/setup")
def panel_setup(session: Session = Depends(get_session)):
    return _panel_setup(session)


@router.put("/panel/settings")
def panel_settings(body: PanelSettingsIn, session: Session = Depends(get_session)):
    import json as _json

    from .. import openrouter

    s = services.get_settings(session)
    if body.budget_usd is not None:
        if not (0 <= body.budget_usd <= 500):
            _bad("Set a monthly budget between $0 and $500.")
        s.ai_budget_usd = float(body.budget_usd)
    if body.models is not None:
        keys = {x["key"] for x in openrouter.PANEL_SEATS}
        s.ai_panel = _json.dumps([{"key": k, "model": v.strip()} for k, v in body.models.items() if k in keys and v.strip()])
    session.add(s)
    session.commit()
    return _panel_setup(session)


@router.post("/panel")
def panel_start(body: PanelIn, wait: bool = False, session: Session = Depends(get_session)):
    from .. import openrouter, panel

    if not body.text.strip():
        _bad("Write the question first.")
    try:
        run = panel.start(session, body.text, body.horizon, body.web, background=not wait)
    except openrouter.AiError as exc:
        _bad(str(exc))
    return panel.payload(session, run)


@router.get("/panel")
def panel_list(session: Session = Depends(get_session)):
    from .. import panel

    return panel.listing(session)


@router.get("/panel/leaderboard")
def panel_board(session: Session = Depends(get_session)):
    from .. import panel

    try:
        panel.score_due(session)
    except Exception:
        pass
    return panel.leaderboard(session)


@router.get("/panel/{run_id}")
def panel_get(run_id: int, session: Session = Depends(get_session)):
    from .. import panel
    from ..models import PanelRun

    run = session.get(PanelRun, run_id)
    if run is None:
        raise HTTPException(404, "No such run.")
    return panel.payload(session, run)


class AssistantIn(BaseModel):
    text: str
    web: bool = True


@router.get("/assistant/{run_id}")
def assistant_messages(run_id: int, session: Session = Depends(get_session)):
    from .. import assistant

    return assistant.messages(session, run_id)


@router.post("/assistant/{run_id}")
def assistant_ask(run_id: int, body: AssistantIn, session: Session = Depends(get_session)):
    from .. import assistant, openrouter, privacy

    try:
        return assistant.ask(session, run_id, body.text, body.web)
    except (openrouter.AiError, privacy.PrivacyError) as exc:
        _bad(str(exc))
