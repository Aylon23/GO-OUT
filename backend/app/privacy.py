"""Privacy guard for every request that leaves the app for an AI provider.

Outgoing payloads may carry tickers, weights and factor exposures only. This module lists what must
never appear (person and portfolio names, nicknames, and money amounts: portfolio values, holding
values, trade and cash-flow amounts, in dollars and shekels) and scans a payload for them. The
OpenRouter client refuses to send anything that fails the scan.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field

from sqlmodel import Session, select

from .models import Portfolio, Transaction

MIN_AMOUNT = 100.0  # smaller numbers (weights, betas, percentages, prices of cheap stocks) are not amounts
_NUM = re.compile(r"-?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")
_lock = threading.Lock()
_cache: dict[tuple, "Forbidden"] = {}


class PrivacyError(Exception):
    pass


@dataclass
class Forbidden:
    terms: list[str] = field(default_factory=list)
    amounts: list[float] = field(default_factory=list)


def forbidden(session: Session) -> Forbidden:
    """Names and amounts that must never leave the app. Cached until the data changes."""
    from . import services
    from .market.cache import data_version

    key = (id(session.get_bind()), services._rev, data_version())
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    terms: set[str] = set()
    for p in session.exec(select(Portfolio)).all():
        for t in [p.name, *(p.aliases or "").split(",")]:
            t = (t or "").strip()
            if len(t) >= 2:
                terms.add(t)
    amounts: set[float] = set()
    try:
        fx = float(services.fx_series(session).iloc[-1])
    except Exception:
        fx = None
    rates = [1.0] + ([fx, 1 / fx] if fx else [])

    def add(v: float) -> None:
        for r in rates:
            x = abs(float(v)) * r
            if x >= MIN_AMOUNT:
                amounts.add(round(x, 2))

    for t in session.exec(select(Transaction)).all():
        q = float(t.quantity or 0)
        if q == 0:  # dividends and cash flows: the price field is the amount
            add(float(t.price or 0))
        elif abs(q) > 1:  # one share's "amount" is just its public price
            add(q * float(t.price or 0))
    try:
        from .scenarios import books

        ports, family = books(session, None)
        for b in [*ports, family]:
            add(b.value)
            for h in b.holdings:
                add(h.value)
    except Exception:  # no prices yet: names and trade amounts still guard
        pass
    out = Forbidden(sorted(terms, key=len, reverse=True), sorted(amounts))
    with _lock:
        _cache.clear()
        _cache[key] = out
    return out


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _numbers(obj) -> list[float]:
    out: list[float] = []
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        for m in _NUM.findall(obj):
            try:
                out.append(float(m.replace(",", "")))
            except ValueError:
                pass
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _numbers(k) + _numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _numbers(v)
    return out


def scan(session: Session, payload) -> list[str]:
    """Problems found in a payload (empty = clean)."""
    f = forbidden(session)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    low = text.lower()
    problems = []
    for t in f.terms:
        if re.search(rf"(?<!\w){re.escape(t.lower())}(?!\w)", low):
            problems.append(f"a portfolio or person name ({t[0]}…)")
    if f.amounts:
        cents = {int(round(a * 100)) for a in f.amounts}
        whole = {int(round(a)) for a in f.amounts if a >= 1000}
        for x in _numbers(payload if not isinstance(payload, str) else text):
            x = abs(x)
            if x < MIN_AMOUNT:
                continue
            # a leaked amount shows up to the cent, or rounded to a whole number
            if int(round(x * 100)) in cents or (x == int(x) and int(x) in whole):
                problems.append(f"an amount from the portfolios ({x:,.0f})")
                break
    return problems


def assert_clean(session: Session, payload) -> None:
    problems = scan(session, payload)
    if problems:
        raise PrivacyError("Not sent: the request would include " + "; ".join(problems) + ".")
