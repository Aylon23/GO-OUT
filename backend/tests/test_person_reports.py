"""Weekly/monthly reports: numbers match the app's screens, RTL layout, missed-run catch-up, delivery to me only."""
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import person_reports, render, services
from app.main import app
from app.models import PersonReport
from tests.conftest import frame


def bdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class FakeTG:
    def __init__(self):
        self.sent = []

    def send_file(self, chat_id, data, filename, caption="", photo=False):
        self.sent.append((chat_id, filename, photo, caption, len(data)))
        return {}


@pytest.fixture
def fam(session, fake, tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "portfolio.db")
    days = bdays(dt.date(2026, 8, 3), 70)
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.6) for d in days])
    fake.frames["SPY"] = frame([(d.isoformat(), 500 + i) for i, d in enumerate(days)])
    fake.frames["QQQ"] = frame([(d.isoformat(), 400 + 2 * i) for i, d in enumerate(days)])
    fake.frames["AAPL"] = frame([(d.isoformat(), 200 + 0.5 * i) for i, d in enumerate(days)])
    fake.frames["NVDA"] = frame([(d.isoformat(), 150 - 0.3 * i) for i, d in enumerate(days)])
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    c.post("/api/transactions", json={"ticker": "AAPL", "type": "BUY", "date": days[2].isoformat(), "quantity": 10, "price": 201, "currency": "USD"})
    c.post("/api/transactions", json={"ticker": "NVDA", "type": "BUY", "date": days[2].isoformat(), "quantity": 10, "price": 149.4, "currency": "USD"})
    pid2 = c.post("/api/portfolios", json={"name": "קרן"}).json()["id"]
    c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": "AAPL", "type": "BUY", "date": days[3].isoformat(), "quantity": 5, "price": 201.5, "currency": "USD"})
    c.session, c.days, c.pid2 = session, days, pid2
    return c


def test_numbers_match_the_app_screens(fam):
    start, end = dt.date(2026, 9, 6), dt.date(2026, 9, 12)
    d = person_reports.person_data(fam.session, 1, start, end)
    pp = fam.get(f"/api/portfolio?portfolio=1&range=CUSTOM&start={start}&end={end}").json()
    s = pp["summary"]
    assert d["return"] == pytest.approx(s["twr"])
    assert d["value_ils"] == pytest.approx(s["value"] * 3.6)
    assert d["change_ils"] == pytest.approx(s["range_gain"] * 3.6)
    assert d["sp500"] == pytest.approx(next(b["twr"] for b in s["benchmarks"] if b["symbol"] == "SPY"))
    assert d["nasdaq"] == pytest.approx(next(b["twr"] for b in s["benchmarks"] if b["symbol"] == "QQQ"))
    hp = fam.get(f"/api/holdings?portfolio=1&range=CUSTOM&start={start}&end={end}").json()
    contrib = {r["ticker"]: r["contribution"] for r in hp["rows"]}
    assert d["top"][0]["symbol"] == "AAPL" and d["top"][0]["contribution"] == pytest.approx(contrib["AAPL"])
    assert d["bottom"][0]["symbol"] == "NVDA" and d["bottom"][0]["contribution"] == pytest.approx(contrib["NVDA"])


def test_rtl_layout_right_aligns_hebrew():
    # exactly one reordering: Pillow's own (libraqm) or ours (python-bidi)
    assert render.visual("שלום") == ("שלום" if render.RAQM else "םולש")
    c = render.Canvas(600, 120, rtl=True, margin=40)
    c.text("שלום עולם", 40)
    img = c.img.convert("L")
    cols = [x for x in range(600) if any(img.getpixel((x, y)) < 120 for y in range(20, 90))]
    assert cols and 600 - 40 - 6 <= max(cols) <= 600 - 40 + 1  # text ends at the right margin
    assert min(cols) > 200  # and doesn't start at the left
    ltr = render.Canvas(600, 120, rtl=False, margin=40)
    ltr.text("Hello world", 40)
    img2 = ltr.img.convert("L")
    cols2 = [x for x in range(600) if any(img2.getpixel((x, y)) < 120 for y in range(20, 90))]
    assert 38 <= min(cols2) <= 46


def test_generate_files_and_family_summary(fam):
    made = person_reports.generate(fam.session, "weekly", dt.date(2026, 9, 6), dt.date(2026, 9, 12), "he")
    names = sorted(r.name for r in made)
    assert names == sorted(["My portfolio", "קרן", "Family"])
    folder = person_reports.reports_dir()
    for r in made:
        im = Image.open(folder / r.png)
        assert im.size == (1080, 1350)
        assert (folder / r.pdf).read_bytes()[:4] == b"%PDF"
    fam_r = next(r for r in made if r.portfolio_id is None)
    assert len(json.loads(fam_r.data)["people"]) == 2


def test_missed_runs_catch_up_and_go_to_me_only(fam):
    st = services.get_settings(fam.session)
    st.report_last_weekly = "2026-09-26"  # last made for the week ending 26 Sep
    st.report_last_monthly = "2026-08-31"
    st.telegram_token, st.telegram_chat_id = "123:abc", "42"
    fam.session.add(st)
    fam.session.commit()
    tg = FakeTG()
    today = dt.date(2026, 10, 14)  # a Wednesday: Sundays 4 and 11 Oct were missed, and the 1 Oct monthly
    made = person_reports.run_due(fam.session, today, client=tg)
    periods = sorted({(r.kind, r.period_end.isoformat()) for r in made})
    assert periods == [("monthly", "2026-09-30"), ("weekly", "2026-10-03"), ("weekly", "2026-10-10")]
    st = services.get_settings(fam.session)
    assert st.report_last_weekly == "2026-10-10" and st.report_last_monthly == "2026-09-30"
    assert {chat for chat, *_ in tg.sent} == {"42"}  # only my paired chat
    assert len(tg.sent) == 2 * len(made) and all(x.sent_at for x in made)
    assert person_reports.run_due(fam.session, today, client=tg) == []  # nothing twice
    lst = fam.get("/api/reports/list").json()
    assert len(lst) == len(made)
    r = fam.get(f"/api/reports/file/{lst[0]['id']}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_hebrew_reads_right_to_left_in_both_layout_modes(monkeypatch):
    """Draw "אב" (alef then bet): alef must end up to the RIGHT of bet, with or without libraqm."""
    from PIL import ImageFont

    f = render.font(80, True)

    def alef_x(raqm: bool) -> tuple[float, float]:
        monkeypatch.setattr(render, "RAQM", raqm)
        c = render.Canvas(400, 140, rtl=True, margin=20)
        c.text("אב", 80)
        img = c.img.convert("L")
        dark = [x for x in range(400) if any(img.getpixel((x, y)) < 120 for y in range(10, 120))]
        mid = (min(dark) + max(dark)) / 2
        # alef has a diagonal stroke; compare ink on each half against a lone alef drawn the same way
        c2 = render.Canvas(400, 140, rtl=True, margin=20)
        c2.text("א", 80)
        a = c2.img.convert("L")
        a_cols = [x for x in range(400) if any(a.getpixel((x, y)) < 120 for y in range(10, 120))]
        return mid, (min(a_cols) + max(a_cols)) / 2

    for raqm in ([True, False] if render.RAQM else [False]):
        mid, alef_mid = alef_x(raqm)
        assert alef_mid > mid  # alef (first letter) sits on the right half: right to left
