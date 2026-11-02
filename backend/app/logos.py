"""Company logos, fetched once from free sources that need no key, then served from this computer.

Never hotlinked: the browser only ever asks /api/logo/{symbol}. Files live in data/logos/, with a
row per symbol in LogoCache. A miss is remembered and retried after 7 days. A logo you upload in
Settings overrides everything and never expires.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Field, Session, SQLModel, select

from . import db

log = logging.getLogger(__name__)
RETRY_AFTER = dt.timedelta(days=7)
MAX_BYTES = 300_000
MIN_BYTES = 200  # anything smaller is a blank or a 1x1 placeholder


class LogoCache(SQLModel, table=True):
    symbol: str = Field(primary_key=True)
    status: str = "none"  # ok | none | manual
    source: str = ""
    file: str = ""  # file name inside the logos folder
    content_type: str = ""
    fetched_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


def logo_dir() -> Path:
    d = db.DB_PATH.parent / "logos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sniff(data: bytes) -> Optional[str]:
    """The image type from its first bytes, or None if it isn't an image we serve."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    head = data[:400].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    return None


EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/x-icon": "ico", "image/svg+xml": "svg"}


def _download(url: str) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Benchmark portfolio app)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status != 200:
                return None
            return r.read(MAX_BYTES + 1)
    except Exception as exc:  # network errors, 404s, timeouts: just a miss
        log.debug("logo fetch %s failed: %s", url, exc)
        return None


def _website(symbol: str) -> Optional[str]:
    from .market.provider import ProviderError, get_provider

    try:
        site = get_provider().info(symbol).get("website") or ""
    except ProviderError:
        return None
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/]+)", site.strip())
    return m.group(1) if m else None


def _candidates(symbol: str) -> list[tuple[str, Callable[[], Optional[str]]]]:
    """(source name, function giving the URL) in the order tried. Free, no key."""
    plain = symbol.replace(".TA", "")
    return [
        ("parqet", lambda: f"https://assets.parqet.com/logos/symbol/{symbol}?format=png"),
        ("fmp", lambda: f"https://financialmodelingprep.com/image-stock/{plain}.png"),
        ("favicon", lambda: (f"https://www.google.com/s2/favicons?domain={d}&sz=128" if (d := _website(symbol)) else None)),
    ]


def fetch_remote(symbol: str) -> Optional[tuple[bytes, str, str]]:
    """Try each source; return (bytes, content type, source) for the first real image."""
    if os.environ.get("PORTFOLIO_PROVIDER", "").lower() == "synthetic" or os.environ.get("PORTFOLIO_NO_LOGOS") == "1":
        return None  # offline modes: letter avatars only
    for name, url_fn in _candidates(symbol):
        url = url_fn()
        if not url:
            continue
        data = _download(url)
        if not data or len(data) < MIN_BYTES or len(data) > MAX_BYTES:
            continue
        ctype = sniff(data)
        if ctype and ctype != "image/svg+xml":  # remote SVGs are not trusted
            return data, ctype, name
    return None


# Swappable in tests.
fetcher: Callable[[str], Optional[tuple[bytes, str, str]]] = fetch_remote


def _save(session: Session, symbol: str, data: bytes, ctype: str, source: str, status: str) -> LogoCache:
    name = f"{re.sub(r'[^A-Za-z0-9._-]', '_', symbol)}.{EXT[ctype]}"
    row = session.get(LogoCache, symbol)
    if row and row.file and row.file != name:
        (logo_dir() / row.file).unlink(missing_ok=True)
    (logo_dir() / name).write_bytes(data)
    row = row or LogoCache(symbol=symbol)
    row.status, row.source, row.file, row.content_type = status, source, name, ctype
    row.fetched_at = dt.datetime.now(dt.timezone.utc)
    session.add(row)
    session.commit()
    return row


def get_logo(session: Session, symbol: str) -> Optional[tuple[Path, str]]:
    """(file path, content type) for a symbol's logo, fetching it once if needed; None = use letters."""
    symbol = symbol.upper().strip()
    if not symbol or len(symbol) > 20:
        return None
    row = session.get(LogoCache, symbol)
    now = dt.datetime.now(dt.timezone.utc)
    if row is not None:
        path = logo_dir() / row.file if row.file else None
        if row.status in ("ok", "manual") and path and path.exists():
            return path, row.content_type
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=dt.timezone.utc)
        if row.status == "none" and now - fetched < RETRY_AFTER:
            return None
    got = fetcher(symbol)
    if got is None:
        row = row or LogoCache(symbol=symbol)
        row.status, row.file, row.source, row.content_type, row.fetched_at = "none", "", "", "", now
        session.add(row)
        session.commit()
        return None
    data, ctype, source = got
    row = _save(session, symbol, data, ctype, source, "ok")
    return logo_dir() / row.file, row.content_type


def set_manual(session: Session, symbol: str, data: bytes) -> LogoCache:
    symbol = symbol.upper().strip()
    if not symbol:
        raise ValueError("Add a symbol.")
    if len(data) > MAX_BYTES:
        raise ValueError("That image is too big. Use one under 300 KB.")
    ctype = sniff(data)
    if not ctype:
        raise ValueError("That isn't a PNG, JPEG, GIF, WebP, ICO or SVG image.")
    return _save(session, symbol, data, ctype, "manual", "manual")


def clear(session: Session, symbol: str) -> None:
    """Forget a symbol's logo (manual or fetched); the next request fetches again."""
    row = session.get(LogoCache, symbol.upper())
    if row is None:
        return
    if row.file:
        (logo_dir() / row.file).unlink(missing_ok=True)
    session.delete(row)
    session.commit()


def overrides(session: Session) -> list[str]:
    return [r.symbol for r in session.exec(select(LogoCache).where(LogoCache.status == "manual").order_by(LogoCache.symbol)).all()]
