"""Automatic backups: a SQLite copy (backup API, safe while the app runs) plus a full Excel export.

- Daily at the first app start of each day (and hourly checks while it runs), and on demand.
- Written to ./backups/ and, optionally, a second folder the user picks (e.g. a synced Google Drive folder).
- Retention per folder: the newest backup of each of the last 30 days that have one, plus the newest of
  each of the last 12 months. Safety copies made before a restore: the newest 10. Only files this module
  names are ever deleted.
- Restore: integrity check, a safety backup of the current state first, then the copy is written into the
  live database with the backup API. Undo restores the safety copy.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from .db import ROOT

log = logging.getLogger("backup")
NAME = re.compile(r"^portfolio-(\d{4}-\d{2}-\d{2})-(\d{6})(-pre-restore)?\.(db|xlsx)$")
KEEP_DAYS, KEEP_MONTHS, KEEP_SAFETY = 30, 12, 10
WARN_AFTER_DAYS = 3
EXCEL_MAX_ROWS = 1_048_575  # Excel's sheet limit, minus the header row
_lock = threading.RLock()
_started = False


class BackupError(Exception):
    pass


@dataclass
class Item:
    stem: str
    day: dt.date
    at: dt.datetime
    safety: bool
    files: list[Path]


def primary_dir() -> Path:
    return Path(os.environ.get("PORTFOLIO_BACKUP_DIR") or ROOT / "backups")


def extra_dir(session: Session) -> Path | None:
    from .services import get_settings

    p = (get_settings(session).backup_dir_extra or "").strip()
    return Path(p).expanduser() if p else None


def check_folder(path: str) -> str | None:
    """Why a folder can't be used, or None if it can."""
    p = Path(path).expanduser()
    if not p.is_dir():
        return "That folder doesn't exist."
    probe = p / ".benchmark-write-test"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        return "The app can't write to that folder."
    return None


# ---------- the copy ----------

def sqlite_copy(engine, dest: Path) -> None:
    """Consistent copy of the live database with SQLite's online backup API."""
    tmp = dest.with_suffix(".db.part")
    tmp.unlink(missing_ok=True)
    raw = engine.raw_connection()
    try:
        src = raw.driver_connection
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        raw.close()
    os.replace(tmp, dest)


def excel_export(db_file: Path, dest: Path) -> int:
    """Every table of the copy as its own sheet (long tables continue on 'name (2)'). Returns rows written."""
    from openpyxl import Workbook

    tmp = dest.with_suffix(".xlsx.part")
    wb = Workbook(write_only=True)
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    total = 0
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            cur = con.execute(f'SELECT * FROM "{t}"')
            header = [d[0] for d in cur.description]
            part, n = 1, 0
            ws = _sheet(wb, t, header)
            while rows := cur.fetchmany(5000):
                for r in rows:
                    if n >= EXCEL_MAX_ROWS:  # Excel's limit: continue on "name (2)"
                        part, n = part + 1, 0
                        ws = _sheet(wb, f"{t} ({part})", header)
                    ws.append([v.hex() if isinstance(v, bytes) else v for v in r])
                    n += 1
                    total += 1
    finally:
        con.close()
    wb.save(tmp)
    os.replace(tmp, dest)
    return total


def _sheet(wb, title: str, header: list[str]):
    ws = wb.create_sheet(title=re.sub(r"[\[\]:*?/\\]", "_", title)[:31])
    ws.append(header)
    return ws


# ---------- listing and retention ----------

def scan(folder: Path) -> list[Item]:
    groups: dict[str, Item] = {}
    if not folder.is_dir():
        return []
    for f in folder.iterdir():
        m = NAME.match(f.name)
        if not m or not f.is_file():
            continue
        stem = f.name.rsplit(".", 1)[0]
        day = dt.date.fromisoformat(m.group(1))
        t = m.group(2)
        at = dt.datetime(day.year, day.month, day.day, int(t[:2]), int(t[2:4]), int(t[4:]))
        it = groups.setdefault(stem, Item(stem, day, at, bool(m.group(3)), []))
        it.files.append(f)
    return sorted(groups.values(), key=lambda i: i.at, reverse=True)


def to_delete(items: list[Item]) -> list[Item]:
    """Which backups retention removes (newest of each of the last 30 days and 12 months stay)."""
    regular = [i for i in items if not i.safety]
    safety = [i for i in items if i.safety]
    keep: set[str] = set()
    days: dict[dt.date, Item] = {}
    months: dict[tuple[int, int], Item] = {}
    for i in regular:  # newest first
        days.setdefault(i.day, i)
        months.setdefault((i.day.year, i.day.month), i)
    for d in sorted(days, reverse=True)[:KEEP_DAYS]:
        keep.add(days[d].stem)
    for m in sorted(months, reverse=True)[:KEEP_MONTHS]:
        keep.add(months[m].stem)
    keep |= {i.stem for i in safety[:KEEP_SAFETY]}
    return [i for i in items if i.stem not in keep]


def apply_retention(folder: Path) -> list[str]:
    removed = []
    for it in to_delete(scan(folder)):
        for f in it.files:
            try:
                f.unlink()
            except OSError as exc:
                log.warning("Couldn't remove old backup %s: %s", f, exc)
        removed.append(it.stem)
    return removed


# ---------- make / restore ----------

def make(session: Session, reason: str = "manual", now: dt.datetime | None = None) -> dict:
    """Back up now into ./backups (and the extra folder, if set). reason: manual | daily | pre-restore."""
    from . import audit
    from .db import get_engine

    with _lock:
        now = now or dt.datetime.now()
        folder = primary_dir()
        folder.mkdir(parents=True, exist_ok=True)
        while True:  # two backups in the same second: name the second one a second later
            stem = f"portfolio-{now:%Y-%m-%d-%H%M%S}" + ("-pre-restore" if reason == "pre-restore" else "")
            db_file, xlsx = folder / f"{stem}.db", folder / f"{stem}.xlsx"
            if not db_file.exists():
                break
            now += dt.timedelta(seconds=1)
        sqlite_copy(get_engine(), db_file)
        rows = excel_export(db_file, xlsx)
        copied, errors = [str(folder)], []
        extra = extra_dir(session)
        if extra:
            problem = check_folder(str(extra))
            if problem:
                errors.append(f"{extra}: {problem}")
            else:
                try:
                    for f in (db_file, xlsx):
                        shutil.copy2(f, extra / f.name)
                    copied.append(str(extra))
                    apply_retention(extra)
                except OSError as exc:
                    errors.append(f"{extra}: {exc}")
        apply_retention(folder)
        audit.record(session, "backup", f"Backup ({reason})", {"stem": stem, "rows": rows, "folders": copied, "errors": errors})
        return {"stem": stem, "rows": rows, "folders": copied, "errors": errors,
                "bytes": db_file.stat().st_size + xlsx.stat().st_size}


def integrity_ok(db_file: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def find(session: Session, stem: str) -> Path:
    for folder in (primary_dir(), extra_dir(session)):
        if folder and (folder / f"{stem}.db").is_file() and NAME.match(f"{stem}.db"):
            return folder / f"{stem}.db"
    raise BackupError("That backup isn't there any more.")


def restore(session: Session, stem: str) -> dict:
    """Replace the live data with a backup. A safety backup of the current state is made first."""
    from . import audit, privacy, services
    from .db import get_engine, migrate
    from .market.cache import clear_memory_cache

    with _lock:
        src_file = find(session, stem)
        if not integrity_ok(src_file):
            raise BackupError("That backup file is damaged, so nothing was changed.")
        safety = make(session, "pre-restore")
        engine = get_engine()
        session.close()
        src = sqlite3.connect(f"file:{src_file}?mode=ro", uri=True)
        raw = engine.raw_connection()
        try:
            src.backup(raw.driver_connection)
        finally:
            raw.close()
            src.close()
        migrate(engine)  # an older backup may lack newer columns
        services.bump_revision()
        clear_memory_cache()
        privacy.clear_cache()
        with Session(engine) as s:
            audit.record(s, "restore", f"Restored {stem}", {"from": stem, "safety": safety["stem"]},
                         undo={"restore": safety["stem"]})
        return {"restored": stem, "safety": safety["stem"]}


def undo_last_restore(session: Session) -> dict:
    import json

    from sqlmodel import select

    from .models import AuditEntry

    e = session.exec(select(AuditEntry).where(AuditEntry.action == "restore", AuditEntry.undone_at.is_(None))
                     .order_by(AuditEntry.id.desc())).first()
    if e is None or not e.undo:
        raise BackupError("There's no restore to undo.")
    target = json.loads(e.undo)["restore"]
    entry_id = e.id
    out = restore(session, target)
    from .db import get_engine

    with Session(get_engine()) as s:
        row = s.get(AuditEntry, entry_id)
        if row is not None:  # the entry exists in the restored data only if it predates the backup
            row.undone_at = dt.datetime.now(dt.timezone.utc)
            s.add(row)
            s.commit()
    return out


# ---------- status ----------

def status(session: Session, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    prim = primary_dir()
    items = scan(prim)
    extra = extra_dir(session)
    extra_items = {i.stem for i in scan(extra)} if extra else set()
    regular = [i for i in items if not i.safety]
    last = regular[0].at if regular else None
    days_since = (now - last).total_seconds() / 86400 if last else None
    locations = [{"path": str(prim.resolve()), "kind": "local", "ok": True, "error": None, "count": len(items)}]
    if extra:
        problem = check_folder(str(extra))
        locations.append({"path": str(extra), "kind": "extra", "ok": problem is None, "error": problem, "count": len(extra_items)})
    return {
        "last_backup": last.isoformat() if last else None,
        "warn": last is None or days_since >= WARN_AFTER_DAYS,
        "days_since": round(days_since, 1) if days_since is not None else None,
        "locations": locations, "extra_dir": str(extra) if extra else "",
        "backups": [{"stem": i.stem, "at": i.at.isoformat(), "safety": i.safety,
                     "has_excel": any(f.suffix == ".xlsx" for f in i.files),
                     "bytes": sum(f.stat().st_size for f in i.files), "in_extra": i.stem in extra_items} for i in items],
        "keep": {"days": KEEP_DAYS, "months": KEEP_MONTHS},
    }


def last(session: Session, now: dt.datetime | None = None) -> dict:
    """Last backup time and the 'no backup for 3 days' flag (used by the Home queue)."""
    st = status(session, now)
    return {"last_backup": st["last_backup"], "days_since": st["days_since"], "no_backup_3_days": st["warn"]}


def sync_queue_flag(session: Session, now: dt.datetime | None = None) -> bool:
    """Keep one open Home-queue item while backups are 3+ days old; close it once a backup succeeds."""
    from sqlmodel import select

    from . import action_queue
    from .models import ActionItem

    info = last(session, now)
    open_items = session.exec(select(ActionItem).where(ActionItem.source == "backup", ActionItem.status == "open")).all()
    if info["no_backup_3_days"]:
        if not open_items:
            when = f"for {int(info['days_since'])} days" if info["days_since"] is not None else "yet"
            action_queue.add(session, "backup", "", f"No backup {when}",
                             "If this computer fails, changes since then are lost.", "Back up now (Settings → Data)",
                             impact_amt=0.0)
        return True
    for i in open_items:
        i.status, i.reason = "done", "A backup was made."
        session.add(i)
    session.commit()
    return False


def ensure_daily(session: Session, now: dt.datetime | None = None) -> dict | None:
    """Make today's backup if there isn't one yet, then update the Home-queue flag."""
    now = now or dt.datetime.now()
    made = None
    if not any(i.day == now.date() and not i.safety for i in scan(primary_dir())):
        try:
            made = make(session, "daily", now)
        except Exception as exc:  # the flag below still tells the owner
            log.warning("Daily backup failed: %s", exc)
    sync_queue_flag(session, now)
    return made


def start_scheduler(interval: float = 3600.0) -> None:
    global _started
    if _started:
        return
    _started = True
    from .db import get_engine

    def loop():
        stop = threading.Event()
        while True:
            try:
                with Session(get_engine()) as s:
                    r = ensure_daily(s)
                    if r:
                        log.info("Daily backup %s (%s rows)", r["stem"], r["rows"])
            except Exception as exc:  # never let backups break the app
                log.warning("Daily backup failed: %s", exc)
            stop.wait(interval)

    threading.Thread(target=loop, name="backup", daemon=True).start()
