"""Scenario Specs: schema, extraction, validation against our data, the inbox."""
import copy
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import factors, services, specs
from app.main import app
from tests.conftest import frame

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "iran-escalation.scenario.json"


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def covid_frames(fake):
    # SPY from 19 Feb to 23 Mar 2020: 100 -> 66 (a 34% fall); USO and GLD flat; ILS=X up 5%
    days = [dt.date(2020, 2, 18) + dt.timedelta(days=i) for i in range(40)]
    fake.frames["SPY"] = frame([(d.isoformat(), 100.0 if d <= dt.date(2020, 2, 19) else 66.0) for d in days])
    fake.frames["USO"] = frame([(d.isoformat(), 20.0) for d in days])
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.4 if d <= dt.date(2020, 2, 19) else 3.57) for d in days])


def test_schema_matches_factor_model_and_example_round_trips():
    import jsonschema

    sch = specs.schema()
    assert sch["$defs"]["factor_id"]["enum"] == factors.BASE_IDS
    assert sch["$defs"]["sector_etf"]["enum"] == factors.SECTOR_ETFS
    ex = example()
    jsonschema.Draft202012Validator(sch).validate(ex)
    again = json.loads(json.dumps(ex, ensure_ascii=False))
    assert again == ex and specs.extract(json.dumps(ex)) == ex


def test_extract_from_surrounding_text():
    ex = example()
    body = json.dumps(ex, ensure_ascii=False, indent=2)
    fenced = f"Here is the research you asked for.\n\n```json\n{body}\n```\n\nLet me know if you want changes."
    assert specs.extract(fenced)["id"] == ex["id"]
    loose = f"Sure! {{not json}} The spec: {body} -- end. Also {{\"kind\": \"other\"}}"
    assert specs.extract(loose)["id"] == ex["id"]
    with pytest.raises(specs.SpecError, match="No JSON"):
        specs.extract("no braces here")
    with pytest.raises(specs.SpecError, match="kind"):
        specs.extract('{"hello": 1}')


def test_probabilities_order_and_unknown_factors(session, fake):
    ex = example()
    assert specs.validate(session, ex)["errors"] == []
    bad = copy.deepcopy(ex)
    bad["branches"][0]["probability"] = 0.5  # sums to 0.9
    assert any("add up to 0.90" in e for e in specs.validate(session, bad)["errors"])
    ok = copy.deepcopy(ex)
    ok["branches"][0]["probability"] = 0.595  # within ±0.01
    assert not any("add up" in e for e in specs.validate(session, ok)["errors"])
    order = copy.deepcopy(ex)
    order["branches"][1]["factor_moves"]["oil"] = {"low": 30, "mid": 25, "high": 45}
    assert any("low ≤ mid ≤ high" in e for e in specs.validate(session, order)["errors"])
    unknown = copy.deepcopy(ex)
    unknown["branches"][0]["factor_moves"]["bitcoin"] = {"low": 1, "mid": 2, "high": 3}
    errs = specs.validate(session, unknown)["errors"]
    assert any("unknown factor id 'bitcoin'" in e for e in errs)


def test_analog_verification_covid_window(session, fake):
    covid_frames(fake)
    assert specs.factor_move(session, "market", dt.date(2020, 2, 19), dt.date(2020, 3, 23)) == pytest.approx(-34.0)
    assert specs.factor_move(session, "shekel", dt.date(2020, 2, 19), dt.date(2020, 3, 23)) == pytest.approx(5.0)
    spec = example()
    b = spec["branches"][0]
    b["factor_moves"] = {"market": {"low": 1, "mid": 5, "high": 9}, "oil": {"low": -1, "mid": 0, "high": 1}}
    b["analogs"] = [{"name": "Covid crash", "start": "2020-02-19", "end": "2020-03-23"}]
    rep = specs.validate(session, spec)
    a = next(x for x in rep["analogs"] if x["name"] == "Covid crash")
    m = next(f for f in a["factors"] if f["factor"] == "market")
    assert m["actual"] == pytest.approx(-34.0) and m["claimed_mid"] == 5 and m["contradicts"]
    o = next(f for f in a["factors"] if f["factor"] == "oil")
    assert not o["contradicts"]  # a 0% mid implies no direction
    assert any("Covid crash" in w and "-34.0%" in w for w in rep["warnings"])
    assert b["factor_moves"]["market"]["mid"] == 5  # the spec's numbers are never replaced
    assert any("fewer than 2 past analogs" in w for w in rep["warnings"])


def test_warnings_for_stale_context_and_huge_moves(session, fake):
    covid_frames(fake)
    spec = example()
    rep = specs.validate(session, spec, today=dt.date(2026, 11, 2))
    assert any("VIX" in w and "days old" in w for w in rep["warnings"])
    assert not any("Brent" in w and "days old" in w for w in rep["warnings"])
    spec["horizon"] = "1w"
    spec["branches"][1]["factor_moves"]["market"] = {"low": -60, "mid": -50, "high": -40}
    rep = specs.validate(session, spec)
    assert any("market move of -60% is larger than any 1w move" in w for w in rep["warnings"])


def test_paste_import_api(session, fake):
    c = TestClient(app)
    text = "Research below:\n```json\n" + EXAMPLE.read_text(encoding="utf-8") + "\n```"
    v = c.post("/api/specs/validate", json={"text": text}).json()
    assert v["report"]["ok"] and v["spec"]["title"].startswith("EXAMPLE")
    saved = c.post("/api/specs", json={"text": text}).json()
    assert saved["id"] == example()["id"]
    assert [x["id"] for x in c.get("/api/specs").json()] == [saved["id"]]
    full = c.get(f"/api/specs/{saved['id']}").json()
    assert full["spec"] == example() and "warnings" in full["report"]
    bad = example()
    bad["branches"][0]["probability"] = 0.9
    r = c.post("/api/specs", json={"text": json.dumps(bad)})
    assert r.status_code == 400 and r.json()["detail"]["report"]["errors"]
    c.delete(f"/api/specs/{saved['id']}")
    assert c.get("/api/specs").json() == []


def test_inbox_moves_files(session, fake, tmp_path):
    good = example()
    (tmp_path / "good.json").write_text("Claude says:\n" + json.dumps(good), encoding="utf-8")
    bad = example()
    bad["id"] = "bad-0000-0000"
    bad["branches"][0]["probability"] = 0.1
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    (tmp_path / "junk.json").write_text("not json at all", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    out = specs.process_inbox(session, tmp_path)
    assert {r["file"]: r["ok"] for r in out} == {"good.json": True, "bad.json": False, "junk.json": False}
    assert (tmp_path / "processed" / "good.json").exists()
    assert (tmp_path / "failed" / "bad.json").exists()
    err = (tmp_path / "failed" / "bad.json.error.txt").read_text(encoding="utf-8")
    assert "add up to" in err
    assert (tmp_path / "failed" / "junk.json.error.txt").exists()
    assert (tmp_path / "notes.txt").exists() and not list(tmp_path.glob("*.json"))
    assert session.get(specs.ResearchedSpec, good["id"]).source == "inbox"
    # dropping the same name again doesn't overwrite the processed copy
    (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
    specs.process_inbox(session, tmp_path)
    assert len(list((tmp_path / "processed").glob("good*.json"))) == 2
