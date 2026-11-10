"""Which version of the app is running, and whether a newer one is already on disk.

The Windows updater (tools/update.ps1) writes .version.json after every check. Without it (a first run
after this was added, or a developer's clone) the version comes from git, or from the newest source file.
The version is read once when the server starts: if the files on disk are newer than that, the server
that is answering is an old one that was left running."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from .db import ROOT

STAMP = ROOT / ".version.json"
CHECK = ROOT / ".update-check.json"


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _newest_source() -> str | None:
    newest = 0.0
    for folder, pattern in ((ROOT / "backend" / "app", "*.py"), (ROOT / "frontend" / "src", "*.ts*")):
        for f in folder.rglob(pattern):
            try:
                newest = max(newest, f.stat().st_mtime)
            except OSError:
                pass
    return dt.datetime.fromtimestamp(newest, dt.timezone.utc).isoformat() if newest else None


def on_disk() -> dict:
    """The version of the files in the app folder right now."""
    s = _read_json(STAMP)
    if s.get("sha"):
        return {"sha": s["sha"][:7], "date": s.get("date"), "note": (s.get("message") or "").splitlines()[0][:120] if s.get("message") else "", "source": "updater"}
    if (ROOT / ".git").exists():
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h|%cI|%s"], capture_output=True, text=True, timeout=5).stdout.strip()
            sha, date, note = out.split("|", 2)
            return {"sha": sha, "date": date, "note": note[:120], "source": "git"}
        except Exception:
            pass
    sha = ""
    try:
        sha = (ROOT / ".version").read_text().strip()[:7]
    except OSError:
        pass
    return {"sha": sha, "date": _newest_source(), "note": "", "source": "files"}


RUNNING = on_disk()
STARTED_AT = dt.datetime.now(dt.timezone.utc).isoformat()


def status() -> dict:
    disk = on_disk()
    check = _read_json(CHECK)
    stale = bool(disk.get("date") and RUNNING.get("date") and disk["date"] > RUNNING["date"]) or (bool(disk.get("sha")) and bool(RUNNING.get("sha")) and disk["sha"] != RUNNING["sha"])
    return {"running": RUNNING, "started_at": STARTED_AT, "on_disk": disk, "restart_needed": stale,
            "check": {"at": check.get("at"), "ok": check.get("ok"), "latest": (check.get("latest") or "")[:7], "error": check.get("error") or ""} if check else None}
