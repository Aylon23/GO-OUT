"""Full transaction history import, with reconciliation against opening positions.

Flow: validate -> preview -> import -> (reconcile) -> apply, and undo.

- Rows are checked one by one: unknown ticker, sell more than held, duplicate, date before the symbol's
  first trading day, and more. Rows with errors are never imported.
- Duplicate protection: each row gets a hash of its content (plus its occurrence number among identical rows
  of the same file), so importing the same file again, or an overlapping one, adds nothing. A trade already
  entered by hand with the same date, symbol, quantity and price is a duplicate too.
- Never count both: a portfolio's opening positions (from a holdings list, dated the import day at average
  cost) and its history are compared per symbol; on approval each symbol uses one source only. Replaced
  opening positions and realized lines are archived, never deleted. Applying is undoable.
- SPLIT rows are checked against Yahoo's split history and kept as information: prices from Yahoo are
  already split-adjusted, so applying them again would double the split.
- Inferred recommendations: the same symbol bought (or sold) by 2+ portfolios within 10 trading days is a
  suggested call, stored as "suggested" until approved.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from collections import Counter, defaultdict

import numpy as np
from sqlmodel import Session, select

from .engine.series import holding_lots, neutral_fx, split_factor_after
from .market.cache import NoPriceData, first_trading_date, load_history, load_splits
from .models import (ArchivedTransaction, HistoryRow, ImportBatch, Portfolio, RealizedEntry, Recommendation,
                     Transaction, TxType)

TYPES = ("BUY", "SELL", "DIVIDEND", "SPLIT", "FEE", "DEPOSIT", "WITHDRAWAL")
COLUMNS = ["date", "type", "symbol", "quantity", "price", "currency", "fees", "note", "source_line"]
ALIASES = {"ticker": "symbol", "qty": "quantity", "shares": "quantity", "amount": "price", "fee": "fees",
           "commission": "fees", "line": "source_line", "ref": "source_line"}
CLUSTER_DAYS = 10  # trading days
QTY_TOL = 1e-6
COST_TOL = 0.01  # average costs within 1% match
PRICE_WARN = 0.25  # a trade price this far from that day's close gets a warning


class HistoryError(ValueError):
    pass


# ------------------------------------------------------------------ parsing

def _num(v: str) -> float | None:
    v = (v or "").strip().replace(",", "").replace("₪", "").replace("$", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _date(v: str) -> dt.date | None:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def parse(text: str) -> list[dict]:
    """CSV text (canonical columns, see docs/import-format.md) -> raw rows with their line numbers."""
    text = text.lstrip("﻿")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        raise HistoryError("The file is empty.")
    header = [ALIASES.get(h.strip().lower(), h.strip().lower()) for h in rows[0]]
    missing = [c for c in ("date", "type") if c not in header]
    if missing:
        raise HistoryError("Missing columns: " + ", ".join(missing) + ". The first row must name the columns (see the import format).")
    out = []
    for n, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        d = {k: (r[i].strip() if i < len(r) else "") for i, k in enumerate(header)}
        d["_line"] = n
        out.append(d)
    return out


def _normalize(raw: dict) -> tuple[dict, list[str]]:
    errs = []
    date = _date(raw.get("date", ""))
    if date is None:
        errs.append("Date must look like 2024-03-15 (or 15/03/2024).")
    typ = (raw.get("type") or "").strip().upper()
    if typ not in TYPES:
        errs.append(f"Type must be one of {', '.join(TYPES)}.")
    sym = (raw.get("symbol") or "").strip().upper()
    qty = _num(raw.get("quantity", "")) or 0.0
    price = _num(raw.get("price", ""))
    fees = _num(raw.get("fees", "")) or 0.0
    cur = (raw.get("currency") or "").strip().upper() or ("ILS" if sym.endswith(".TA") else "USD")
    if cur in ("NIS", "₪"):
        cur = "ILS"
    if cur not in ("USD", "ILS"):
        errs.append("Currency must be USD or ILS.")
    if fees < 0:
        errs.append("Fees can't be negative.")
    if typ in ("BUY", "SELL", "DIVIDEND", "SPLIT") and not sym:
        errs.append(f"A {typ} needs a symbol.")
    if typ in ("BUY", "SELL"):
        if qty <= 0:
            errs.append("Quantity must be above 0.")
        if price is None or price <= 0:
            errs.append("Price per share must be above 0.")
    elif typ == "SPLIT":
        if qty <= 0:
            errs.append("For a SPLIT, quantity is the ratio (4 for a 4-for-1 split).")
        price = 0.0
    elif typ in ("DIVIDEND", "FEE", "DEPOSIT", "WITHDRAWAL"):
        if price is None or price <= 0:
            errs.append("Price is the cash amount and must be above 0.")
        qty = 0.0
    if typ in ("DEPOSIT", "WITHDRAWAL"):
        sym = ""
    if date and date > dt.date.today():
        errs.append("The date is in the future.")
    row = {"date": date, "type": typ, "symbol": sym, "quantity": float(qty), "price": float(price or 0.0),
           "currency": cur, "fees": float(fees), "note": (raw.get("note") or "")[:300],
           "source_line": (raw.get("source_line") or "")[:120] or str(raw.get("_line", "")), "line": raw.get("_line")}
    return row, errs


def row_hash(pid: int, r: dict, occurrence: int) -> str:
    key = (f"{pid}|{r['date']}|{r['type']}|{r['symbol']}|{r['quantity']:.6f}|{r['price']:.6f}|{r['currency']}|"
           f"{r['fees']:.6f}|{occurrence}")
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# ------------------------------------------------------------------ what is in the books

def is_opening(t) -> bool:
    return t.origin == "opening" or (not t.origin and t.cost_price is not None)


def _active(session: Session, pid: int) -> list[Transaction]:
    return session.exec(select(Transaction).where(Transaction.portfolio_id == pid)).all()


def _known_hashes(session: Session, pid: int) -> set[str]:
    h = {t.source_hash for t in _active(session, pid) if t.source_hash}
    batches = [b.id for b in session.exec(select(ImportBatch).where(ImportBatch.portfolio_id == pid, ImportBatch.status != "undone")).all()]
    if batches:
        h |= {r.hash for r in session.exec(select(HistoryRow).where(HistoryRow.batch_id.in_(batches),
                                                                     HistoryRow.status.in_(["staged", "applied", "kept_out", "info"]))).all()}
    return h


def _same_trade(t: Transaction, r: dict) -> bool:
    if t.date != r["date"] or t.ticker.upper() != r["symbol"] or str(t.type.value if hasattr(t.type, "value") else t.type) != r["type"]:
        return False
    if r["type"] in ("BUY", "SELL"):
        return abs(t.quantity - r["quantity"]) < QTY_TOL and abs(t.price - r["price"]) <= 0.005 * max(r["price"], 1e-9)
    return abs(t.price - r["price"]) <= 0.005 * max(r["price"], 1e-9)


# ------------------------------------------------------------------ validation

def validate(session: Session, pid: int, text: str) -> dict:
    """Per-row results: {line, row, errors, warnings, status: new | duplicate | error, hash}."""
    if session.get(Portfolio, pid) is None:
        raise HistoryError("That portfolio doesn't exist.")
    raw = parse(text)
    known = _known_hashes(session, pid)
    manual = [t for t in _active(session, pid) if not is_opening(t) and not t.source_hash]
    seen: Counter = Counter()
    ticker_ok: dict[str, str | None] = {}
    first_day: dict[str, dt.date] = {}
    results = []
    for rr in raw:
        row, errs = _normalize(rr)
        warns: list[str] = []
        content = (row["date"], row["type"], row["symbol"], round(row["quantity"], 6), round(row["price"], 6), row["currency"], round(row["fees"], 6))
        seen[content] += 1
        h = row_hash(pid, row, seen[content]) if not errs else ""
        sym = row["symbol"]
        if sym and not errs:
            if sym not in ticker_ok:
                try:
                    first_day[sym] = first_trading_date(session, sym)
                    ticker_ok[sym] = None
                except NoPriceData:
                    ticker_ok[sym] = f"Unknown ticker {sym}: no price history found. Check the symbol (Tel Aviv listings end in .TA)."
            if ticker_ok[sym]:
                errs.append(ticker_ok[sym])
            elif row["date"] < first_day[sym]:
                errs.append(f"{sym} started trading on {first_day[sym].isoformat()}; this row is dated earlier.")
        if not errs and row["type"] in ("BUY", "SELL"):
            warns += _price_warnings(session, row)
        status = "error" if errs else "new"
        if status == "new" and h in known:
            status, reason = "duplicate", "Already imported."
            warns.append(reason)
        elif status == "new" and any(_same_trade(t, row) for t in manual):
            status = "duplicate"
            warns.append("Already recorded by hand (same date, symbol, quantity and price).")
        results.append({"line": row["line"], "source_line": row["source_line"], "row": _jsonable(row), "errors": errs,
                        "warnings": warns, "status": status, "hash": h})
    _check_sells(session, pid, results)
    _check_splits(session, results)
    counts = Counter(r["status"] for r in results)
    return {"rows": results, "counts": {"total": len(results), "new": counts["new"], "duplicate": counts["duplicate"], "error": counts["error"]}}


def _jsonable(row: dict) -> dict:
    out = dict(row)
    out["date"] = row["date"].isoformat() if row["date"] else None
    return out


def _price_warnings(session: Session, row: dict) -> list[str]:
    try:
        c = load_history(session, row["symbol"])["close"]
    except NoPriceData:
        return []
    before = c[c.index <= row["date"]]
    if before.empty:
        return []
    close = float(before.iloc[-1])
    if close <= 0:
        return []
    if row["symbol"].endswith(".TA") and row["price"] > 50 * close:
        return [f"The price looks like agorot ({row['price']:,.2f} vs a close of ₪{close:,.2f}); give Tel Aviv prices in shekels."]
    if abs(row["price"] / close - 1) > PRICE_WARN:
        return [f"The price is {row['price'] / close - 1:+.0%} from that day's close ({close:,.2f}). Check it."]
    return []


def _check_sells(session: Session, pid: int, results: list[dict]) -> None:
    """Selling more than held at that date (from the file plus trades already entered by hand)."""
    events: dict[str, list] = defaultdict(list)
    for t in _active(session, pid):
        if is_opening(t) or t.type not in (TxType.BUY, TxType.SELL):
            continue
        events[t.ticker.upper()].append((t.date, 0 if t.type == TxType.BUY else 1, t.quantity if t.type == TxType.BUY else -t.quantity, None))
    for r in results:
        row = r["row"]
        if r["status"] != "new" or row["type"] not in ("BUY", "SELL"):
            continue
        d = dt.date.fromisoformat(row["date"])
        events[row["symbol"]].append((d, 0 if row["type"] == "BUY" else 1, row["quantity"] if row["type"] == "BUY" else -row["quantity"], r))
    for sym, evs in events.items():
        splits = load_splits(session, sym)
        held = 0.0
        for d, _order, q, r in sorted(evs, key=lambda e: (e[0], e[1])):
            f = split_factor_after(splits, d)
            if q < 0 and held + q * f < -1e-6 * max(1.0, abs(q * f)):
                if r is not None:
                    r["errors"].append(f"Sells {abs(q):,.6g} {sym} but only {max(held, 0) / f:,.6g} are held on {d.isoformat()}.")
                    r["status"] = "error"
                    continue  # the bad row is left out of the running total
            held += q * f


def _check_splits(session: Session, results: list[dict]) -> None:
    for r in results:
        row = r["row"]
        if r["status"] != "new" or row["type"] != "SPLIT":
            continue
        d = dt.date.fromisoformat(row["date"])
        known = [ratio for sd, ratio in load_splits(session, row["symbol"]) if abs((sd - d).days) <= 5]
        if not known:
            r["warnings"].append("Yahoo has no split near this date; kept as information only.")
        elif all(abs(k - row["quantity"]) > 1e-6 for k in known):
            r["warnings"].append(f"Yahoo shows a {known[0]:g}-for-1 split here, not {row['quantity']:g}.")
        else:
            r["warnings"].append("Matches Yahoo's split; prices already account for it, so it is kept as information.")


# ------------------------------------------------------------------ reconciliation

def _txlike(rows: list[dict]):
    from .engine.series import Tx

    return [Tx(r["symbol"], r["type"], r["date"], r["quantity"], r["price"], r["currency"], r["fees"])
            for r in rows if r["type"] in ("BUY", "SELL")]


def history_positions(session: Session, rows: list[dict]) -> dict[str, dict]:
    """Holdings rebuilt from history as of today: quantity (current split basis) and average cost (native)."""
    from .services import fx_series

    fx = neutral_fx(fx_series(session))
    txs = _txlike(rows)
    out = {}
    for sym in sorted({t.ticker for t in txs}):
        lots = holding_lots(txs, sym, load_splits(session, sym), fx, "USD")
        out[sym] = {"quantity": lots["quantity"], "avg_cost": lots["avg_cost"] if lots["quantity"] > 0 else None,
                    "first_date": min(t.date for t in txs if t.ticker == sym).isoformat()}
    return out


def opening_positions(session: Session, pid: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for t in _active(session, pid):
        if not is_opening(t) or t.type != TxType.BUY:
            continue
        p = out.setdefault(t.ticker.upper(), {"quantity": 0.0, "cost": 0.0, "ids": []})
        p["quantity"] += t.quantity
        p["cost"] += t.quantity * (t.cost_price if t.cost_price is not None else t.price)
        p["ids"].append(t.id)
    return {s: {"quantity": p["quantity"], "avg_cost": p["cost"] / p["quantity"] if p["quantity"] else None, "ids": p["ids"]}
            for s, p in out.items()}


def diff(hist: dict[str, dict], opening: dict[str, dict]) -> list[dict]:
    """Per symbol: Match / Quantity differs / Avg cost differs / Only in history / Only in opening positions."""
    out = []
    for sym in sorted(set(hist) | set(opening)):
        h, o = hist.get(sym), opening.get(sym)
        hq = h["quantity"] if h else 0.0
        if h and o:
            if abs(hq - o["quantity"]) > max(QTY_TOL, 1e-6 * o["quantity"]):
                cat = "Quantity differs"
            elif h["avg_cost"] and o["avg_cost"] and abs(h["avg_cost"] / o["avg_cost"] - 1) > COST_TOL:
                cat = "Avg cost differs"
            else:
                cat = "Match"
        elif h:
            if hq <= QTY_TOL:
                cat = "Closed in history"  # bought and sold within the history; nothing to reconcile
            else:
                cat = "Only in history"
        else:
            cat = "Only in opening positions"
        out.append({"symbol": sym, "category": cat,
                    "history": {"quantity": hq, "avg_cost": h["avg_cost"] if h else None, "first_date": h["first_date"] if h else None} if h else None,
                    "opening": {"quantity": o["quantity"], "avg_cost": o["avg_cost"]} if o else None,
                    "default": "opening" if cat == "Only in opening positions" else "history"})
    return out


def _batch_rows(session: Session, batch_id: int, statuses=("staged",)) -> list[HistoryRow]:
    return session.exec(select(HistoryRow).where(HistoryRow.batch_id == batch_id, HistoryRow.status.in_(list(statuses)))
                        .order_by(HistoryRow.idx)).all()


def _row_dict(r: HistoryRow) -> dict:
    return {"date": r.date, "type": r.type, "symbol": r.symbol, "quantity": r.quantity, "price": r.price,
            "currency": r.currency, "fees": r.fees}


def reconcile_view(session: Session, batch: ImportBatch) -> dict:
    staged = [_row_dict(r) for r in _batch_rows(session, batch.id)]
    manual = [{"date": t.date, "type": t.type.value if hasattr(t.type, "value") else t.type, "symbol": t.ticker.upper(),
               "quantity": t.quantity, "price": t.price, "currency": t.currency, "fees": t.fees}
              for t in _active(session, batch.portfolio_id) if not is_opening(t)]
    rows = diff(history_positions(session, staged + manual), opening_positions(session, batch.portfolio_id))
    return {"batch": batch_dict(batch), "diff": rows, "counts": dict(Counter(r["category"] for r in rows))}


# ------------------------------------------------------------------ import, apply, undo

def preview(session: Session, pid: int, text: str) -> dict:
    v = validate(session, pid, text)
    new = [r["row"] for r in v["rows"] if r["status"] == "new"]
    rows = [{**r, "date": dt.date.fromisoformat(r["date"])} for r in new]
    manual = [{"date": t.date, "type": t.type.value if hasattr(t.type, "value") else t.type, "symbol": t.ticker.upper(),
               "quantity": t.quantity, "price": t.price, "currency": t.currency, "fees": t.fees}
              for t in _active(session, pid) if not is_opening(t)]
    opening = opening_positions(session, pid)
    d = diff(history_positions(session, rows + manual), opening)
    first = min((r["date"] for r in rows), default=None)
    return {**v, "first_date": first.isoformat() if first else None, "needs_reconciliation": bool(opening),
            "diff": d, "diff_counts": dict(Counter(x["category"] for x in d))}


def import_rows(session: Session, pid: int, text: str, filename: str = "") -> dict:
    """Stage the new rows. With no opening positions they are applied at once; otherwise they wait for reconciliation."""
    v = validate(session, pid, text)
    new = [r for r in v["rows"] if r["status"] == "new"]
    batch = ImportBatch(portfolio_id=pid, filename=filename[:200], rows_total=v["counts"]["total"], rows_new=len(new),
                        rows_duplicate=v["counts"]["duplicate"], rows_error=v["counts"]["error"])
    session.add(batch)
    session.commit()
    session.refresh(batch)
    for i, r in enumerate(new):
        row = r["row"]
        session.add(HistoryRow(batch_id=batch.id, idx=i, date=dt.date.fromisoformat(row["date"]), type=row["type"], symbol=row["symbol"],
                               quantity=row["quantity"], price=row["price"], currency=row["currency"], fees=row["fees"],
                               note=row["note"], source_line=row["source_line"], hash=r["hash"],
                               status="info" if row["type"] == "SPLIT" else "staged"))
    dates = [dt.date.fromisoformat(r["row"]["date"]) for r in new]
    batch.first_date = min(dates) if dates else None
    session.add(batch)
    session.commit()
    out = {"batch": batch_dict(batch), "validation": v}
    if not new:
        batch.status = "applied"
        batch.applied_at = dt.datetime.now(dt.timezone.utc)
        session.add(batch)
        session.commit()
        out["batch"] = batch_dict(batch)
        out["result"] = {"added": 0}
        return out
    if not opening_positions(session, pid):
        out["result"] = apply(session, batch.id, {})
        out["batch"] = batch_dict(session.get(ImportBatch, batch.id))
    else:
        out["reconcile"] = reconcile_view(session, batch)
    return out


def apply(session: Session, batch_id: int, decisions: dict[str, str] | None = None) -> dict:
    """Make the history the books' source. Per symbol, history or opening position (never both)."""
    from . import audit, services

    batch = session.get(ImportBatch, batch_id)
    if batch is None or batch.status != "staged":
        raise HistoryError("That import isn't waiting to be applied.")
    pid = batch.portfolio_id
    view = reconcile_view(session, batch)
    choice = {r["symbol"]: r["default"] for r in view["diff"]}
    for sym, c in (decisions or {}).items():
        if sym.upper() in choice and c in ("history", "opening"):
            choice[sym.upper()] = c
    before = _summary(session, pid)
    opening = opening_positions(session, pid)
    archived = added = kept_out = 0
    for sym, o in opening.items():
        if choice.get(sym) != "history":
            continue
        for tid in o["ids"]:
            t = session.get(Transaction, tid)
            _archive(session, t, batch.id, f"Replaced by history import #{batch.id}")
            archived += 1
        for e in session.exec(select(RealizedEntry).where(RealizedEntry.portfolio_id == pid, RealizedEntry.ticker == sym,
                                                          RealizedEntry.archived_batch.is_(None))).all():
            e.archived_batch = batch.id  # the history's sells now carry this realized profit
            session.add(e)
    for r in _batch_rows(session, batch.id):
        if r.symbol and choice.get(r.symbol) == "opening":
            r.status = "kept_out"
            kept_out += 1
        else:
            session.add(Transaction(portfolio_id=pid, ticker=r.symbol, type=TxType(r.type), date=r.date, quantity=r.quantity,
                                    price=r.price, currency=r.currency, fees=r.fees, note=r.note or f"History import #{batch.id}",
                                    origin="history", source_hash=r.hash, batch_id=batch.id))
            r.status = "applied"
            added += 1
        session.add(r)
    batch.status, batch.applied_at, batch.decisions = "applied", dt.datetime.now(dt.timezone.utc), json.dumps(choice)
    session.add(batch)
    session.commit()
    services.bump_revision()  # TWR, XIRR, shadow portfolios and risk are rebuilt from the first real trade
    suggested = infer_calls(session)
    after = _summary(session, pid)
    audit.record(session, "history_import", f"Applied history import #{batch.id} ({added} rows)",
                 {"batch": batch.id, "added": added, "archived": archived, "kept_out": kept_out}, undo={"history_batch": batch.id})
    return {"added": added, "archived_opening": archived, "kept_out": kept_out, "decisions": choice,
            "before": before, "after": after, "suggested_calls": len(suggested)}


def _archive(session: Session, t: Transaction, batch_id: int, reason: str) -> None:
    session.add(ArchivedTransaction(original_id=t.id, portfolio_id=t.portfolio_id, ticker=t.ticker,
                                    type=t.type.value if hasattr(t.type, "value") else str(t.type), date=t.date, quantity=t.quantity,
                                    price=t.price, currency=t.currency, fees=t.fees, note=t.note, cost_price=t.cost_price,
                                    rec_id=t.rec_id, origin=t.origin or ("opening" if t.cost_price is not None else ""),
                                    source_hash=t.source_hash, original_batch_id=t.batch_id, archived_by_batch=batch_id, reason=reason))
    session.delete(t)


def undo(session: Session, batch_id: int) -> dict:
    """Take an applied import back out and restore what it replaced (all of it stays in the archive)."""
    from . import audit, services

    batch = session.get(ImportBatch, batch_id)
    if batch is None or batch.status not in ("applied", "staged"):
        raise HistoryError("That import can't be undone.")
    removed = 0
    for t in session.exec(select(Transaction).where(Transaction.batch_id == batch_id)).all():
        _archive(session, t, batch_id, f"History import #{batch_id} undone")
        removed += 1
    restored = 0
    for a in session.exec(select(ArchivedTransaction).where(ArchivedTransaction.archived_by_batch == batch_id,
                                                            ArchivedTransaction.restored == False)).all():  # noqa: E712
        if a.original_batch_id == batch_id:
            continue  # the import's own rows
        session.add(Transaction(portfolio_id=a.portfolio_id, ticker=a.ticker, type=TxType(a.type), date=a.date, quantity=a.quantity,
                                price=a.price, currency=a.currency, fees=a.fees, note=a.note, cost_price=a.cost_price,
                                rec_id=a.rec_id, origin=a.origin, source_hash=a.source_hash, batch_id=a.original_batch_id))
        a.restored = True
        session.add(a)
        restored += 1
    for e in session.exec(select(RealizedEntry).where(RealizedEntry.archived_batch == batch_id)).all():
        e.archived_batch = None
        session.add(e)
    for r in session.exec(select(HistoryRow).where(HistoryRow.batch_id == batch_id)).all():
        r.status = "undone"
        session.add(r)
    batch.status = "undone"
    session.add(batch)
    session.commit()
    services.bump_revision()
    audit.record(session, "history_import_undo", f"Undid history import #{batch_id}", {"removed": removed, "restored": restored})
    return {"removed": removed, "restored": restored}


def _summary(session: Session, pid: int) -> dict:
    """First real trade, value, TWR and XIRR since then (the same figures the portfolio page shows)."""
    from .engine.returns import twr
    from .services import Context, range_xirr

    ctx = Context(session, pid)
    if ctx.series is None:
        return {"first_trade": None, "value": 0.0, "twr": None, "xirr": None}
    s = ctx.series
    b, e = 0, len(s.dates) - 1
    return {"first_trade": min(t.date for t in ctx.txs).isoformat(), "value": float(s.value.iloc[-1]),
            "twr": _f(twr(s.ret, b, e)), "xirr": _f(range_xirr(s.value, s.flow, b, e)) if (s.dates[e] - s.dates[b]).days >= 30 else None}


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def batch_dict(b: ImportBatch) -> dict:
    return {"id": b.id, "portfolio_id": b.portfolio_id, "created_at": b.created_at.isoformat(), "filename": b.filename,
            "status": b.status, "rows_total": b.rows_total, "rows_new": b.rows_new, "rows_duplicate": b.rows_duplicate,
            "rows_error": b.rows_error, "first_date": b.first_date.isoformat() if b.first_date else None,
            "applied_at": b.applied_at.isoformat() if b.applied_at else None, "decisions": json.loads(b.decisions or "{}")}


def batches(session: Session, pid: int | None = None) -> list[dict]:
    q = select(ImportBatch).order_by(ImportBatch.id.desc())
    if pid is not None:
        q = q.where(ImportBatch.portfolio_id == pid)
    return [batch_dict(b) for b in session.exec(q).all()]


# ------------------------------------------------------------------ inferred recommendations

def trading_days_between(a: dt.date, b: dt.date) -> int:
    """Trading days from a to b (weekdays; a's day not counted)."""
    return int(np.busday_count(a, b))


def clusters(trades: list[dict], window: int = CLUSTER_DAYS) -> list[dict]:
    """trades: [{symbol, action, date, portfolio_id, price}] -> clusters where 2+ portfolios made the same trade
    within `window` trading days of the cluster's first trade."""
    out = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for t in trades:
        groups[(t["symbol"], t["action"])].append(t)
    for (sym, action), ts in groups.items():
        ts.sort(key=lambda t: (t["date"], t["portfolio_id"]))
        i = 0
        while i < len(ts):
            first = ts[i]
            j = i
            while j + 1 < len(ts) and trading_days_between(first["date"], ts[j + 1]["date"]) <= window:
                j += 1
            members = ts[i:j + 1]
            people = sorted({m["portfolio_id"] for m in members})
            if len(people) >= 2:
                out.append({"symbol": sym, "action": action, "first_date": first["date"], "people": people,
                            "price": first["price"], "trades": len(members), "last_date": members[-1]["date"]})
                i = j + 1
            else:
                i += 1
    return sorted(out, key=lambda c: (c["first_date"], c["symbol"]))


def infer_calls(session: Session) -> list[Recommendation]:
    """Store new suggested calls from the books (opening positions excluded: their date is the import day)."""
    trades = [{"symbol": t.ticker.upper(), "action": t.type.value if hasattr(t.type, "value") else t.type, "date": t.date,
               "portfolio_id": t.portfolio_id, "price": t.price}
              for t in session.exec(select(Transaction).where(Transaction.type.in_([TxType.BUY, TxType.SELL]))).all() if not is_opening(t)]
    existing = session.exec(select(Recommendation)).all()
    made = []
    for c in clusters(trades):
        if any(r.ticker == c["symbol"] and r.action == c["action"] and abs(trading_days_between(min(r.date, c["first_date"]),
               max(r.date, c["first_date"]))) <= CLUSTER_DAYS for r in existing + made):
            continue
        price = c["price"]
        try:
            close = load_history(session, c["symbol"])["close"]
            b = close[close.index <= c["first_date"]]
            if not b.empty:
                price = float(b.iloc[-1])
        except NoPriceData:
            pass
        rec = Recommendation(ticker=c["symbol"], action=c["action"], date=c["first_date"], price=price, status="suggested",
                             source="inferred", people=json.dumps(c["people"]),
                             thesis=f"Inferred: {len(c['people'])} portfolios {'bought' if c['action'] == 'BUY' else 'sold'} "
                                    f"{c['symbol']} within {CLUSTER_DAYS} trading days.")
        session.add(rec)
        made.append(rec)
    session.commit()
    return made


def suggested(session: Session) -> list[dict]:
    names = {p.id: p.name for p in session.exec(select(Portfolio)).all()}
    rows = session.exec(select(Recommendation).where(Recommendation.status == "suggested").order_by(Recommendation.date)).all()
    return [{"id": r.id, "symbol": r.ticker, "action": r.action, "first_date": r.date.isoformat(), "price": r.price,
             "people": [{"id": pid, "name": names.get(pid, "?")} for pid in json.loads(r.people or "[]")], "note": r.thesis}
            for r in rows]


def decide_suggestion(session: Session, rec_id: int, approve: bool) -> dict:
    from . import audit

    r = session.get(Recommendation, rec_id)
    if r is None or r.status != "suggested":
        raise HistoryError("That suggestion isn't waiting for a decision.")
    r.status = "approved" if approve else "dismissed"
    linked = 0
    if approve:  # the trades that make up the cluster follow this call
        people = json.loads(r.people or "[]")
        for t in session.exec(select(Transaction).where(Transaction.ticker == r.ticker, Transaction.rec_id.is_(None),
                                                        Transaction.portfolio_id.in_(people))).all():
            if (t.type.value if hasattr(t.type, "value") else t.type) == r.action and 0 <= trading_days_between(r.date, t.date) <= CLUSTER_DAYS \
                    and not is_opening(t):
                t.rec_id = r.id
                session.add(t)
                linked += 1
    session.add(r)
    session.commit()
    audit.record(session, "suggested_call", f"{'Approved' if approve else 'Dismissed'} suggested call {r.action} {r.ticker}",
                 {"id": r.id, "linked_trades": linked}, undo={"recommendation_status": {"id": r.id, "status": "suggested"}})
    return {"id": r.id, "status": r.status, "linked_trades": linked}
