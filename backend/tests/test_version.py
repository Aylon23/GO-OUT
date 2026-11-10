"""The version line: what is running, what the updater last saw, and an old server left open."""
import json

from fastapi.testclient import TestClient

from app import version
from app.main import app


def test_version_comes_from_the_updater_stamp(tmp_path, monkeypatch):
    stamp, check = tmp_path / ".version.json", tmp_path / ".update-check.json"
    # Windows PowerShell writes UTF-8 with a byte-order mark
    stamp.write_text(json.dumps({"sha": "abcdef1234567", "date": "2026-11-10T15:57:11Z", "message": "Title line\n\nBody"}), encoding="utf-8-sig")
    check.write_text(json.dumps({"at": "2026-11-10T16:05:00Z", "ok": True, "latest": "abcdef1234567"}), encoding="utf-8-sig")
    monkeypatch.setattr(version, "STAMP", stamp)
    monkeypatch.setattr(version, "CHECK", check)
    disk = version.on_disk()
    assert disk == {"sha": "abcdef1", "date": "2026-11-10T15:57:11Z", "note": "Title line", "source": "updater"}
    monkeypatch.setattr(version, "RUNNING", disk)
    s = TestClient(app).get("/api/version").json()
    assert s["running"]["sha"] == "abcdef1" and not s["restart_needed"]
    assert s["check"] == {"at": "2026-11-10T16:05:00Z", "ok": True, "latest": "abcdef1", "error": ""}


def test_an_old_server_left_running_is_noticed(tmp_path, monkeypatch):
    stamp = tmp_path / ".version.json"
    stamp.write_text(json.dumps({"sha": "1111111aaaa", "date": "2026-11-09T10:00:00Z"}))
    monkeypatch.setattr(version, "STAMP", stamp)
    monkeypatch.setattr(version, "CHECK", tmp_path / "none.json")
    monkeypatch.setattr(version, "RUNNING", version.on_disk())
    stamp.write_text(json.dumps({"sha": "2222222bbbb", "date": "2026-11-10T10:00:00Z"}))  # the updater ran; the old server is still up
    s = version.status()
    assert s["restart_needed"] and s["running"]["sha"] == "1111111" and s["on_disk"]["sha"] == "2222222" and s["check"] is None


def test_a_failed_update_check_is_reported(tmp_path, monkeypatch):
    check = tmp_path / ".update-check.json"
    check.write_text(json.dumps({"at": "2026-11-10T16:05:00Z", "ok": False, "error": "no internet"}))
    monkeypatch.setattr(version, "CHECK", check)
    assert version.status()["check"]["ok"] is False and version.status()["check"]["error"] == "no internet"
