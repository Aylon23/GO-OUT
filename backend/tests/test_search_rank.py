from app.market import search as s


def q(sym, exch, qt="EQUITY", name=None):
    return {"symbol": sym, "shortname": name or sym, "exchange": exch, "exchDisp": exch, "quoteType": qt}


def test_filters_out_options_futures_crypto_currencies():
    raw = [q("AAPL", "NMS"), q("AAPL250117C00100000", "OPR", "OPTION"), q("ES=F", "CME", "FUTURE"),
           q("BTC-USD", "CCC", "CRYPTOCURRENCY"), q("ILS=X", "CCY", "CURRENCY"), q("^GSPC", "SNP", "INDEX"),
           q("VFIAX", "NAS", "MUTUALFUND")]
    out = [n["symbol"] for n in (s.normalize_quote(x) for x in raw) if n]
    assert out == ["AAPL", "^GSPC", "VFIAX"]


def test_ranking_exact_then_us_then_tase_then_other():
    items = [s.normalize_quote(x) for x in [q("TEVA.TA", "TLV"), q("TEV.DE", "GER"), q("TEVA", "NYQ"), q("TEVX", "NMS")]]
    assert [i["symbol"] for i in s.rank(items, "teva")] == ["TEVA", "TEVX", "TEVA.TA", "TEV.DE"]
    items = [s.normalize_quote(x) for x in [q("AAPL.MX", "MEX"), q("APLE", "NYQ"), q("AAPL", "NMS")]]
    assert [i["symbol"] for i in s.rank(items, "AAPL")][0] == "AAPL"


def test_currency_and_labels():
    n = s.normalize_quote(q("TEVA.TA", "TLV"))
    assert n["currency"] == "ILS" and n["exchange"] == "Tel Aviv" and n["type"] == "Stock"
    assert s.normalize_quote(q("QQQ", "NMS", "ETF"))["currency"] == "USD"


def test_cache_and_fallback(session, fake):
    s.clear_caches()
    fake.quotes = [q("AAPL", "NMS", name="Apple Inc.")]
    first = s.search(session, "apple")
    fake.quotes = []
    assert s.search(session, "APPLE")["results"] == first["results"]  # cached, case-insensitive
    s.remember_selection(session, {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "type": "Stock", "currency": "USD"})
    fake.fail_search = True
    s.clear_caches()
    out = s.search(session, "appl")
    assert out["source"] == "local"
    assert out["results"][0]["symbol"] == "AAPL"
    assert out["results"][-1].get("raw")  # raw ticker escape hatch
