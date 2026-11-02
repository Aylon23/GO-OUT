"""Company logos: fetched once, cached on disk and in the database, overridable, never hotlinked."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import db, logos
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400


@pytest.fixture
def client(session, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "portfolio.db")
    calls = []

    def fake(symbol):
        calls.append(symbol)
        return (PNG, "image/png", "test") if symbol == "AAPL" else None

    monkeypatch.setattr(logos, "fetcher", fake)
    c = TestClient(app)
    c.calls = calls
    c.dir = tmp_path / "logos"
    return c


def test_fetched_once_then_served_from_disk(client):
    r = client.get("/api/logo/aapl")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert "max-age" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    assert (client.dir / "AAPL.png").exists()
    assert client.get("/api/logo/AAPL").status_code == 200
    assert client.calls == ["AAPL"]  # second request came from the cache


def test_miss_is_remembered_for_a_week(client, session):
    assert client.get("/api/logo/ZZZZ").status_code == 404
    assert client.get("/api/logo/ZZZZ").status_code == 404
    assert client.calls == ["ZZZZ"]
    row = session.get(logos.LogoCache, "ZZZZ")
    row.fetched_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)
    session.add(row)
    session.commit()
    client.get("/api/logo/ZZZZ")
    assert client.calls == ["ZZZZ", "ZZZZ"]  # retried after 7 days


def test_manual_override_wins_and_can_be_removed(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
    r = client.post("/api/logo/SEDG", files={"file": ("l.svg", svg, "image/svg+xml")})
    assert r.status_code == 200
    got = client.get("/api/logo/SEDG")
    assert got.status_code == 200 and got.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in got.headers["content-security-policy"]
    assert client.get("/api/logos/overrides").json() == ["SEDG"]
    assert client.calls == []  # an override never goes to the network
    client.delete("/api/logo/SEDG")
    assert client.get("/api/logos/overrides").json() == []
    assert client.get("/api/logo/SEDG").status_code == 404


def test_upload_rejects_non_images(client):
    r = client.post("/api/logo/AAPL", files={"file": ("x.txt", b"hello world" * 50, "text/plain")})
    assert r.status_code == 400


def test_sniff():
    assert logos.sniff(PNG) == "image/png"
    assert logos.sniff(b"\xff\xd8\xff\xe0" + b"0" * 10) == "image/jpeg"
    assert logos.sniff(b"<html>") is None
