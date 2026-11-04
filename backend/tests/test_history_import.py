"""History import: validation, no double counting, duplicates, reconciliation categories, clusters, undo, FEE, SPLIT."""
import datetime as dt
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import calls, history_import as hi, services
from app.engine.series import MarketData, Tx, build_series
from app.main import app
from app.models import ArchivedTransaction, ImportBatch, Portfolio, RealizedEntry, Recommendation, Transaction, TxType
from tests.conftest import frame

END = dt.date.today() - dt.timedelta(days=1)
DAYS = [d.date() for d in pd.bdate_range(END - dt.timedelta(days=420), END)]
START = DAYS[0]
PRICES = {"AAPL": 100.0, "MSFT": 200.0, "NVDA": 80.0, "QQQ": 300.0, "SPY": 400.0, "TSLA": 50.0}


@pytest.fixture
def c(session, fake):
    services.bump_revision()
    for s, p in PRICES.items():
        fake.frames[s] = frame([(d.isoformat(), p) for d in DAYS])
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.7) for d in DAYS])
    client = TestClient(app)
    client.put("/api/settings", json={"base_currency": "USD"})
    client.session = session
    return client


def d(i):
    return DAYS[i].isoformat()


def opening(session, pid, sym, qty, cost):
    """An opening position as the holdings-list import makes it: a BUY on the import day at average cost."""
    session.add(Transaction(portfolio_id=pid, ticker=sym, type=TxType.BUY, date=DAYS[-2], quantity=qty, price=PRICES[sym],
                            currency="USD", cost_price=cost, note="Imported holding (average cost)", origin="opening"))
    session.commit()


def csv(*rows):
    return "date,type,symbol,quantity,price,currency,fees,note,source_line\n" + "\n".join(",".join(str(x) for x in r) for r in rows)


# ---------- validation ----------

def test_validation_flags_each_problem(c):
    s = c.session
    s.add(Transaction(portfolio_id=1, ticker="MSFT", type=TxType.BUY, date=DAYS[20], quantity=2, price=200, currency="USD"))
    s.commit()
    text = csv(
        (d(10), "BUY", "AAPL", 5, 100, "USD", 1, "", "L1"),
        (d(11), "BUY", "ZZZZ", 5, 10, "USD", 0, "", "L2"),  # unknown ticker
        (d(12), "SELL", "AAPL", 8, 100, "USD", 0, "", "L3"),  # more than held
        ((START - dt.timedelta(days=30)).isoformat(), "BUY", "NVDA", 1, 80, "USD", 0, "", "L4"),  # before first trading day
        (d(20), "BUY", "MSFT", 2, 200, "USD", 0, "", "L5"),  # already entered by hand
        (d(21), "SWAP", "AAPL", 1, 100, "USD", 0, "", "L6"),  # unknown type
        ((dt.date.today() + dt.timedelta(days=5)).isoformat(), "DEPOSIT", "", 0, 1000, "USD", 0, "", "L7"),  # future
        (d(22), "BUY", "AAPL", 1, 160, "USD", 0, "", "L8"),  # price far from the close: a warning only
    )
    out = c.post("/api/history/validate", json={"portfolio": 1, "text": text}).json()
    by = {r["source_line"]: r for r in out["rows"]}
    assert by["L1"]["status"] == "new" and not by["L1"]["errors"]
    assert "Unknown ticker ZZZZ" in by["L2"]["errors"][0]
    assert by["L3"]["status"] == "error" and "only 5 are held" in by["L3"]["errors"][0]
    assert "started trading" in by["L4"]["errors"][0]
    assert by["L5"]["status"] == "duplicate" and "by hand" in by["L5"]["warnings"][0]
    assert by["L6"]["status"] == "error" and "Type must be" in by["L6"]["errors"][0]
    assert by["L7"]["status"] == "error" and "future" in by["L7"]["errors"][0]
    assert by["L8"]["status"] == "new" and "from that day's close" in by["L8"]["warnings"][0]
    assert out["counts"] == {"total": 8, "new": 2, "duplicate": 1, "error": 5}
    assert s.exec(select(ImportBatch)).all() == []  # validate saves nothing


def test_missing_columns_and_empty_file(c):
    assert c.post("/api/history/validate", json={"portfolio": 1, "text": "foo,bar\n1,2"}).status_code == 400
    assert c.post("/api/history/validate", json={"portfolio": 1, "text": ""}).status_code == 400


# ---------- no double counting ----------

def test_no_double_counting(c):
    s = c.session
    opening(s, 1, "AAPL", 10, 100.0)
    s.add(RealizedEntry(portfolio_id=1, ticker="AAPL", amount=50.0))
    s.commit()
    text = csv((d(5), "BUY", "AAPL", 6, 95, "USD", 0, "", ""), (d(9), "BUY", "AAPL", 4, 107.5, "USD", 0, "", ""))
    out = c.post("/api/history/import", json={"portfolio": 1, "text": text}).json()
    assert out["batch"]["status"] == "staged" and out["reconcile"]["diff"][0]["category"] == "Match"
    # while staged, nothing is counted twice: the history isn't in the books yet
    assert sum(t.quantity for t in s.exec(select(Transaction).where(Transaction.ticker == "AAPL")).all()) == 10
    res = c.post(f"/api/history/batches/{out['batch']['id']}/apply", json={}).json()
    assert res["added"] == 2 and res["archived_opening"] == 1
    active = s.exec(select(Transaction).where(Transaction.portfolio_id == 1, Transaction.ticker == "AAPL")).all()
    assert {t.origin for t in active} == {"history"} and sum(t.quantity for t in active) == 10
    arch = s.exec(select(ArchivedTransaction)).all()
    assert len(arch) == 1 and arch[0].origin == "opening" and arch[0].cost_price == 100.0  # archived, not deleted
    ctx = services.Context(s, 1)
    assert services.open_positions(ctx)["AAPL"] == pytest.approx(10)  # not 20
    assert float(ctx.series.value.iloc[-1]) == pytest.approx(10 * 100.0)
    assert s.exec(select(RealizedEntry)).one().archived_batch == out["batch"]["id"]  # its sells carry realized gains now
    assert res["after"]["first_trade"] == d(5)  # results are measured from the first real trade


def test_apply_undo_restores_opening_positions(c):
    s = c.session
    opening(s, 1, "AAPL", 10, 100.0)
    out = c.post("/api/history/import", json={"portfolio": 1, "text": csv((d(5), "BUY", "AAPL", 10, 100, "USD", 0, "", ""))}).json()
    bid = out["batch"]["id"]
    c.post(f"/api/history/batches/{bid}/apply", json={})
    r = c.post(f"/api/history/batches/{bid}/undo").json()
    assert r == {"removed": 1, "restored": 1}
    active = s.exec(select(Transaction).where(Transaction.ticker == "AAPL")).all()
    assert len(active) == 1 and active[0].origin == "opening" and active[0].cost_price == 100.0
    assert len(s.exec(select(ArchivedTransaction)).all()) == 2  # both versions stay in the archive


def test_keeping_the_opening_position_keeps_history_out(c):
    s = c.session
    opening(s, 1, "AAPL", 10, 100.0)
    out = c.post("/api/history/import", json={"portfolio": 1, "text": csv((d(5), "BUY", "AAPL", 12, 100, "USD", 0, "", ""),
                                                                          (d(6), "DEPOSIT", "", 0, 500, "USD", 0, "", ""))}).json()
    res = c.post(f"/api/history/batches/{out['batch']['id']}/apply", json={"decisions": {"AAPL": "opening"}}).json()
    assert res["kept_out"] == 1 and res["added"] == 1  # the deposit still goes in
    aapl = s.exec(select(Transaction).where(Transaction.ticker == "AAPL")).all()
    assert len(aapl) == 1 and aapl[0].origin == "opening"


# ---------- duplicates ----------

def test_duplicate_import_adds_zero_rows(c):
    s = c.session
    rows = [(d(5), "BUY", "AAPL", 3, 100, "USD", 1, "", ""), (d(5), "BUY", "AAPL", 3, 100, "USD", 1, "", ""),  # two identical real trades
            (d(8), "DIVIDEND", "AAPL", 0, 2.4, "USD", 0, "", ""), (d(9), "DEPOSIT", "", 0, 1000, "USD", 0, "", "")]
    first = c.post("/api/history/import", json={"portfolio": 1, "text": csv(*rows)}).json()
    assert first["batch"]["status"] == "applied" and first["result"]["added"] == 4  # no opening positions: applied at once
    n = len(s.exec(select(Transaction)).all())
    again = c.post("/api/history/import", json={"portfolio": 1, "text": csv(*rows)}).json()
    assert again["batch"]["rows_new"] == 0 and again["batch"]["rows_duplicate"] == 4
    assert len(s.exec(select(Transaction)).all()) == n
    overlap = c.post("/api/history/import", json={"portfolio": 1, "text": csv(*rows, (d(30), "SELL", "AAPL", 1, 100, "USD", 0, "", ""))}).json()
    assert overlap["batch"]["rows_new"] == 1 and len(s.exec(select(Transaction)).all()) == n + 1


# ---------- reconciliation categories ----------

def test_diff_categories(c):
    s = c.session
    opening(s, 1, "AAPL", 10, 100.0)  # history: same -> Match
    opening(s, 1, "MSFT", 5, 200.0)  # history: 7 shares -> Quantity differs
    opening(s, 1, "NVDA", 3, 50.0)  # history: 3 at 80 -> Avg cost differs
    opening(s, 1, "QQQ", 2, 300.0)  # not in history -> Only in opening positions
    text = csv((d(5), "BUY", "AAPL", 10, 100, "USD", 0, "", ""), (d(5), "BUY", "MSFT", 7, 200, "USD", 0, "", ""),
               (d(5), "BUY", "NVDA", 3, 80, "USD", 0, "", ""), (d(5), "BUY", "SPY", 1, 400, "USD", 0, "", ""),
               (d(6), "BUY", "TSLA", 4, 50, "USD", 0, "", ""), (d(7), "SELL", "TSLA", 4, 50, "USD", 0, "", ""))
    prev = c.post("/api/history/preview", json={"portfolio": 1, "text": text}).json()
    cat = {r["symbol"]: r["category"] for r in prev["diff"]}
    assert cat == {"AAPL": "Match", "MSFT": "Quantity differs", "NVDA": "Avg cost differs", "QQQ": "Only in opening positions",
                   "SPY": "Only in history", "TSLA": "Closed in history"}
    default = {r["symbol"]: r["default"] for r in prev["diff"]}
    assert default["QQQ"] == "opening" and default["MSFT"] == "history"
    assert prev["needs_reconciliation"] and prev["first_date"] == d(5)
    assert s.exec(select(ImportBatch)).all() == []  # preview saves nothing


# ---------- inferred recommendations ----------

def test_cluster_detection_window():
    base = dt.date(2024, 1, 2)  # a Tuesday
    t = lambda sym, action, day, pid: {"symbol": sym, "action": action, "date": day, "portfolio_id": pid, "price": 10.0}
    ten = base + dt.timedelta(days=14)  # 10 trading days later
    eleven = base + dt.timedelta(days=15)
    assert hi.trading_days_between(base, ten) == 10 and hi.trading_days_between(base, eleven) == 11
    out = hi.clusters([t("X", "BUY", base, 1), t("X", "BUY", ten, 2),  # inside the window: a cluster
                       t("Y", "BUY", base, 1), t("Y", "BUY", eleven, 2),  # one day too late: none
                       t("Z", "BUY", base, 1), t("Z", "BUY", base, 1),  # the same portfolio twice: none
                       t("W", "BUY", base, 1), t("W", "SELL", base, 2)])  # buy and sell don't mix
    assert [(x["symbol"], x["people"], x["first_date"]) for x in out] == [("X", [1, 2], base)]


def test_suggested_calls_wait_for_approval(c):
    s = c.session
    s.add(Portfolio(name="Keren"))
    s.commit()
    c.post("/api/history/import", json={"portfolio": 1, "text": csv((d(30), "BUY", "MSFT", 2, 200, "USD", 0, "", ""))})
    out = c.post("/api/history/import", json={"portfolio": 2, "text": csv((d(33), "BUY", "MSFT", 1, 200, "USD", 0, "", ""))}).json()
    assert out["result"]["suggested_calls"] == 1
    sug = c.get("/api/history/suggested-calls").json()
    assert len(sug) == 1 and sug[0]["symbol"] == "MSFT" and sug[0]["action"] == "BUY" and sug[0]["first_date"] == d(30)
    assert [p["id"] for p in sug[0]["people"]] == [1, 2] and sug[0]["price"] == 200.0
    assert calls.call_rows(s) == []  # suggested calls aren't scored as your calls
    r = c.post(f"/api/history/suggested-calls/{sug[0]['id']}/approve").json()
    assert r["status"] == "approved" and r["linked_trades"] == 2
    assert [row["ticker"] for row in calls.call_rows(s)] == ["MSFT"]
    c.get("/api/history/suggested-calls?refresh=true")
    assert len(s.exec(select(Recommendation)).all()) == 1  # not suggested again


# ---------- FEE and SPLIT ----------

def test_fee_lowers_the_return_and_is_not_a_flow():
    md = MarketData(frame([("2024-01-02", 100), ("2024-01-03", 100)]), "USD")
    fx = pd.Series([3.7], index=[dt.date(2024, 1, 2)])
    s = build_series([Tx("", "DEPOSIT", dt.date(2024, 1, 2), 0, 1000, "USD"), Tx("AAPL", "BUY", dt.date(2024, 1, 2), 5, 100, "USD"),
                      Tx("", "FEE", dt.date(2024, 1, 3), 0, 10, "USD")], {"AAPL": md}, fx, "USD")
    assert s.flow.loc[dt.date(2024, 1, 3)] == 0  # not money taken out
    assert s.ret.loc[dt.date(2024, 1, 3)] == pytest.approx(-0.01)  # 10 out of 1,000


def test_split_rows_are_information_only(c, fake):
    fake.split_map["AAPL"] = [(DAYS[50], 4.0)]
    rows = [(d(10), "BUY", "AAPL", 1, 100, "USD", 0, "", ""), (d(50), "SPLIT", "AAPL", 4, 0, "USD", 0, "", "")]
    v = c.post("/api/history/validate", json={"portfolio": 1, "text": csv(*rows)}).json()
    split = v["rows"][1]
    assert split["status"] == "new" and "Matches Yahoo's split" in split["warnings"][0]
    c.post("/api/history/import", json={"portfolio": 1, "text": csv(*rows)})
    assert [t.type for t in c.session.exec(select(Transaction)).all()] == [TxType.BUY]  # the split isn't applied twice
