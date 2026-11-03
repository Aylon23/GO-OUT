"""Backups: valid while writing, restore round-trip keeps every row, retention deletes only old files."""
import datetime as dt
import sqlite3
import threading

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlmodel import Session, select

from app import backup, db
from app.models import AuditEntry, Portfolio, Transaction


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A real database file (the backup API works on files) and a backups folder."""
    engine = create_engine(f"sqlite:///{tmp_path / 'live.db'}", connect_args={"check_same_thread": False})
    db.set_engine(engine)
    monkeypatch.setenv("PORTFOLIO_BACKUP_DIR", str(tmp_path / "backups"))
    with Session(engine) as s:
        yield s
    engine.dispose()


def add_tx(s: Session, i: int, pid: int = 1) -> None:
    s.add(Transaction(portfolio_id=pid, ticker="AAPL", type="BUY", date=dt.date(2024, 1, 1) + dt.timedelta(days=i % 300),
                      quantity=1 + i, price=100.0 + i, currency="USD"))
    s.commit()


def dump(path_or_engine) -> dict[str, list[tuple]]:
    con = sqlite3.connect(path_or_engine) if isinstance(path_or_engine, (str,)) or hasattr(path_or_engine, "parts") else None
    if con is None:
        raw = path_or_engine.raw_connection()
        con = raw.driver_connection
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: sorted(con.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr) for t in tables}
    finally:
        if hasattr(path_or_engine, "raw_connection"):
            raw.close()
        else:
            con.close()


def test_backup_during_writes_is_valid(live):
    engine = db.get_engine()
    stop = threading.Event()
    written = []

    def writer():
        with Session(engine) as s:
            i = 0
            while not stop.is_set() and i < 3000:
                add_tx(s, i)
                written.append(i)
                i += 1

    t = threading.Thread(target=writer)
    t.start()
    while len(written) < 50:
        pass
    checked = 0
    for k in range(3):  # three backups while the writer keeps committing; each checked right away
        m = backup.make(live, "manual", now=dt.datetime(2026, 11, 3 - k, 10, 0, 0))
        f = backup.primary_dir() / f"{m['stem']}.db"
        assert backup.integrity_ok(f)
        con = sqlite3.connect(f)
        n = con.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        con.close()
        assert n >= 50
        wb = load_workbook(backup.primary_dir() / f"{m['stem']}.xlsx", read_only=True)
        assert set(wb.sheetnames) == tables  # one sheet per table
        assert sum(1 for _ in wb["transaction"].iter_rows()) == n + 1  # the Excel file is the same snapshot as the copy (plus a header)
        checked += 1
    stop.set()
    t.join()
    assert checked == 3 and len(written) > 50


def test_restore_round_trip_keeps_every_row(live):
    live.add(Portfolio(name="Keren", aliases="קרן"))
    live.commit()
    for i in range(40):
        add_tx(live, i, pid=1 + i % 2)
    before = dump(db.get_engine())
    made = backup.make(live, "manual", now=dt.datetime(2026, 11, 1, 9, 0, 0))
    # change everything
    for t in live.exec(select(Transaction)).all()[:25]:
        live.delete(t)
    live.get(Portfolio, 2).name = "Renamed"
    add_tx(live, 999)
    live.commit()
    changed = dump(db.get_engine())
    out = backup.restore(live, made["stem"])
    after = dump(db.get_engine())
    for table in before:
        if table == "auditentry":  # the restore itself is logged on top of the restored log
            assert set(before[table]) <= set(after[table])
            continue
        assert after[table] == before[table], table
    # the safety copy holds the state just before the restore
    safety = dump(str(backup.primary_dir() / f"{out['safety']}.db"))
    assert safety["transaction"] == changed["transaction"]
    with Session(db.get_engine()) as s:
        e = s.exec(select(AuditEntry).where(AuditEntry.action == "restore")).first()
        assert e and e.undo
        backup.undo_last_restore(s)
    assert dump(db.get_engine())["transaction"] == changed["transaction"]  # undo brings the changes back


def test_restore_refuses_a_damaged_file(live):
    made = backup.make(live, "manual", now=dt.datetime(2026, 11, 1, 9, 0, 0))
    f = backup.primary_dir() / f"{made['stem']}.db"
    f.write_bytes(b"not a database" * 100)
    n = len(backup.scan(backup.primary_dir()))
    with pytest.raises(backup.BackupError):
        backup.restore(live, made["stem"])
    assert len(backup.scan(backup.primary_dir())) == n  # no safety copy, nothing changed


def _touch(folder, stem, exts=("db", "xlsx")):
    for e in exts:
        (folder / f"{stem}.{e}").write_text("x")


def test_retention_deletes_only_old_files(tmp_path):
    folder = tmp_path / "b"
    folder.mkdir()
    today = dt.date(2026, 11, 3)
    stems = []
    for k in range(400):  # a backup most days for 400 days, two on some days
        d = today - dt.timedelta(days=k)
        if k % 7 == 3:
            continue
        for hh in ((9, 18) if k % 5 == 0 else (9,)):
            s = f"portfolio-{d.isoformat()}-{hh:02d}0000"
            _touch(folder, s)
            stems.append((d, hh, s))
    for k in range(14):  # safety copies
        _touch(folder, f"portfolio-{(today - dt.timedelta(days=k * 9)).isoformat()}-120000-pre-restore")
    (folder / "notes.txt").write_text("mine")
    (folder / "portfolio-old.db").write_text("not ours")

    removed = set(backup.apply_retention(folder))
    left = {i.stem for i in backup.scan(folder)}
    days = sorted({d for d, _h, _s in stems}, reverse=True)
    recent_days = set(days[:30])
    newest_of_day = {}
    for d, hh, s in stems:
        if d not in newest_of_day or hh > newest_of_day[d][0]:
            newest_of_day[d] = (hh, s)
    # the newest backup of each of the last 30 backup days stays
    assert all(newest_of_day[d][1] in left for d in recent_days)
    # the newest backup of each of the last 12 months stays
    months = sorted({(d.year, d.month) for d, _h, _s in stems}, reverse=True)[:12]
    for y, m in months:
        newest = max((x for x in stems if (x[0].year, x[0].month) == (y, m)), key=lambda x: (x[0], x[1]))
        assert newest[2] in left
    # nothing removed is newer than the 30th backup day, unless it was a same-day older copy
    for s in removed:
        if "pre-restore" in s:
            continue
        d = dt.date.fromisoformat(s[10:20])
        assert d < days[29] or newest_of_day[d][1] != s
    # 10 newest safety copies stay, both files of each pair go together, other files are untouched
    assert sum("pre-restore" in s for s in left) == 10
    assert (folder / "notes.txt").exists() and (folder / "portfolio-old.db").exists()
    assert not any((folder / f"{s}.db").exists() or (folder / f"{s}.xlsx").exists() for s in removed)
    month_rep = {max((x for x in stems if (x[0].year, x[0].month) == ym), key=lambda x: (x[0], x[1]))[2] for ym in months}
    expected = {newest_of_day[d][1] for d in recent_days} | month_rep
    assert {x for x in left if "pre-restore" not in x} == expected


def test_daily_backup_once_a_day_and_warning_after_3_days(live):
    t0 = dt.datetime(2026, 11, 1, 8, 0, 0)
    assert backup.status(live, now=t0)["warn"] is True  # never backed up
    assert backup.ensure_daily(live, now=t0) is not None
    assert backup.ensure_daily(live, now=t0 + dt.timedelta(hours=3)) is None  # already have today's
    assert backup.status(live, now=t0 + dt.timedelta(days=2, hours=23))["warn"] is False
    st = backup.status(live, now=t0 + dt.timedelta(days=3, minutes=1))
    assert st["warn"] is True and st["days_since"] >= 3


def test_second_folder_gets_a_copy(live, tmp_path):
    from app import services

    extra = tmp_path / "drive"
    extra.mkdir()
    services.get_settings(live).backup_dir_extra = str(extra)
    live.commit()
    m = backup.make(live, "manual", now=dt.datetime(2026, 11, 2, 7, 0, 0))
    assert (extra / f"{m['stem']}.db").exists() and (extra / f"{m['stem']}.xlsx").exists()
    st = backup.status(live, now=dt.datetime(2026, 11, 2, 8, 0, 0))
    assert st["backups"][0]["in_extra"] and st["locations"][1]["ok"]
