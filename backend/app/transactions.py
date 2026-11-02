"""Transaction validation, CSV import/export."""
from __future__ import annotations

import csv
import datetime as dt
import io

from pydantic import BaseModel
from sqlmodel import Session, select

from .engine.series import split_factor_after
from .market.cache import NoPriceData, currency_for, first_trading_date, load_splits
from .models import Transaction, TxType

CSV_COLUMNS = ["date", "type", "ticker", "quantity", "price", "currency", "fees", "note"]


class TxIn(BaseModel):
    ticker: str = ""
    type: TxType
    date: dt.date
    quantity: float = 0.0
    price: float = 0.0
    currency: str = "USD"
    fees: float = 0.0
    note: str = ""


class TxError(Exception):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def validate(session: Session, tx: TxIn, existing: list[Transaction], exclude_id: int | None = None) -> TxIn:
    """Raise TxError with field-level messages. `existing` is the current transaction list."""
    errs: dict[str, str] = {}
    tx.ticker = tx.ticker.strip().upper()
    tx.currency = (tx.currency or "").upper()
    if tx.currency not in ("USD", "ILS"):
        errs["currency"] = "Use USD or ILS."
    if tx.date > dt.date.today():
        errs["date"] = "Date can't be in the future."
    if tx.fees < 0:
        errs["fees"] = "Fees can't be negative."
    if tx.type in (TxType.BUY, TxType.SELL, TxType.DIVIDEND):
        if not tx.ticker:
            errs["ticker"] = "Pick a symbol."
    else:
        tx.ticker = ""
        tx.quantity = 0.0
    if tx.type in (TxType.BUY, TxType.SELL):
        if tx.quantity <= 0:
            errs["quantity"] = "Enter a quantity above 0."
        if tx.price <= 0:
            errs["price"] = "Enter a price above 0."
    elif tx.price <= 0:
        errs["price"] = "Enter an amount above 0."
    if tx.ticker and "ticker" not in errs:
        try:
            first = first_trading_date(session, tx.ticker)
            if tx.date < first and "date" not in errs:
                errs["date"] = f"{tx.ticker} started trading on {first.strftime('%-d %b %Y')}. Pick that date or later."
        except NoPriceData as exc:
            errs["ticker"] = str(exc)
    if tx.type == TxType.SELL and not errs:
        msg = check_sell(session, tx, existing, exclude_id)
        if msg:
            errs["quantity"] = msg
    if errs:
        raise TxError(errs)
    return tx


def check_sell(session: Session, tx: TxIn, existing: list[Transaction], exclude_id: int | None) -> str | None:
    """Selling more than you hold on that date (or breaking a later sale) is not allowed."""
    splits = load_splits(session, tx.ticker)
    events = []
    for t in existing:
        if t.ticker.upper() != tx.ticker or t.id == exclude_id or t.type not in (TxType.BUY, TxType.SELL):
            continue
        sign = 1 if t.type == TxType.BUY else -1
        events.append((t.date, 0 if sign > 0 else 1, 0, sign * t.quantity * split_factor_after(splits, t.date)))
    factor = split_factor_after(splits, tx.date)
    events.append((tx.date, 1, 1, -tx.quantity * factor))
    held = 0.0
    for d, _, is_new, q in sorted(events):
        if is_new and held + q < -1e-6:
            return f"You held {max(held, 0) / factor:,.6g} {tx.ticker} on that date."
        held += q
        if held < -1e-6:
            return f"This leaves your sale on {d.strftime('%-d %b %Y')} without enough shares."
    return None


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames:
        return []
    fields = [f.strip().lower() for f in reader.fieldnames]
    reader.fieldnames = fields
    rows = []
    for i, r in enumerate(reader, start=2):
        rows.append({"line": i, **{k: (v or "").strip() for k, v in r.items() if k}})
    return rows


def _num(v: str, default: float = 0.0) -> float:
    v = (v or "").replace(",", "").strip()
    return float(v) if v else default


def rows_to_tx(session: Session, rows: list[dict], pid: int = 1) -> list[dict]:
    """Validate each row in date order against the database plus earlier CSV rows."""
    existing = list(session.exec(select(Transaction).where(Transaction.portfolio_id == pid)).all())
    pending: list[Transaction] = []
    out = []
    ordered = sorted(rows, key=lambda r: (r.get("date", ""), 0 if r.get("type", "").upper() != "SELL" else 1))
    for r in ordered:
        res = {"line": r["line"], "raw": {k: r.get(k, "") for k in CSV_COLUMNS}}
        try:
            missing = [c for c in ("date", "type") if not r.get(c)]
            if missing:
                raise TxError({c: "Required." for c in missing})
            try:
                d = dt.date.fromisoformat(r["date"])
            except ValueError:
                raise TxError({"date": "Use YYYY-MM-DD."})
            typ = r["type"].upper()
            if typ not in TxType.__members__:
                raise TxError({"type": "Use BUY, SELL, DIVIDEND, DEPOSIT or WITHDRAWAL."})
            ticker = r.get("ticker", "").upper()
            cur = (r.get("currency") or "").upper() or (currency_for(session, ticker) if ticker else "ILS")
            tx = TxIn(ticker=ticker, type=TxType(typ), date=d, quantity=_num(r.get("quantity", "")),
                      price=_num(r.get("price", "")), currency=cur, fees=_num(r.get("fees", "")), note=r.get("note", ""))
            tx = validate(session, tx, existing + pending)
            pending.append(Transaction(**tx.model_dump()))
            res["tx"] = {**tx.model_dump(), "type": tx.type.value, "date": tx.date.isoformat()}
            res["errors"] = {}
        except TxError as exc:
            res["errors"] = exc.errors
        except ValueError as exc:
            res["errors"] = {"row": str(exc)}
        out.append(res)
    return sorted(out, key=lambda r: r["line"])


def export_csv(session: Session, pid: int = 1) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for t in session.exec(select(Transaction).where(Transaction.portfolio_id == pid).order_by(Transaction.date, Transaction.id)).all():
        w.writerow([t.date.isoformat(), t.type.value, t.ticker, f"{t.quantity:g}", f"{t.price:g}", t.currency, f"{t.fees:g}", t.note])
    return buf.getvalue()
