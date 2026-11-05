"""P0-7 / P0-8: no internal key shows up in a scenario result, and outcomes read the same way everywhere."""
from pathlib import Path

from app import impact
from tests.test_openrouter import _family, ai  # noqa: F401 (fixture)

ONE = {"shocks": [{"symbol": "SPY", "kind": "factor", "move": -0.05}], "horizon_weeks": 4, "text": "", "regime": "normal", "edits": []}


def test_raw_branch_names_are_replaced():
    assert impact.readable_branches({"title": "Oil spike", "branches": [{"name": "other"}]})["branches"][0]["name"] == "Oil spike"
    assert impact.readable_branches({"branches": [{"name": "branch 1"}]})["branches"][0]["name"] == "Single path"
    two = impact.readable_branches({"branches": [{"name": "base"}, {"name": "Ceasefire holds"}]})["branches"]
    assert [b["name"] for b in two] == ["Path 1", "Ceasefire holds"]
    keep = {"branches": [{"name": "Oil spike"}]}
    assert impact.readable_branches(keep) is keep


def test_a_run_never_returns_a_raw_name(session, ai):
    c = _family(session)
    r = c.post("/api/impact/shocks", json=ONE).json()
    names = [b["name"] for b in r["branches"]] + [b["name"] for b in r["spec"]["branches"]]
    assert names and not any(impact.RAW_NAME.match(n) for n in names), names


def test_scenario_ui_uses_one_vocabulary():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/components/Impact.tsx").read_text()
    for gone in ("Most likely", "Bad case", "Good case", "{b.cause}"):
        assert gone not in src, gone
    for word in ("Typical", "Worst of 20", "Best of 20", "prob(b.prob_loss)"):
        assert word in src, word
    fmt = (Path(__file__).resolve().parents[2] / "frontend/src/lib/format.ts").read_text()
    assert '"> 99%"' in fmt and '"< 1%"' in fmt
