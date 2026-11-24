from app import articles

PAGE = """<html><head><title>t</title><meta property="og:title" content="Tariffs on chips"></head><body>
<nav>Home News Sports</nav><article><h1>Tariffs on chips</h1>
<p>The US plans new tariffs on semiconductors from ten countries, officials said on Monday.</p>
<p>Chip makers with factories abroad could face higher costs, analysts warned in a note.</p>
<p>Markets fell after the report, with the technology sector leading the losses this week.</p></article>
<footer>Copyright</footer><script>var x=1</script></body></html>"""


class Resp:
    def __init__(self, text, code=200, ctype="text/html"):
        self.text, self.status_code, self.headers, self.content = text, code, {"content-type": ctype}, text.encode()


class Client:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        return self.pages[url]


def test_links_are_found_and_replaced():
    q = "What does this mean for me? https://www.ynet.co.il/economy/article/abc123. And https://x.com/a, https://www.ynet.co.il/economy/article/abc123"
    assert articles.links(q) == ["https://www.ynet.co.il/economy/article/abc123", "https://x.com/a"]
    assert articles.without_links("see https://a.com/1 and https://b.com/2") == "see [article 1] and [article 2]"


def test_article_text_is_extracted_without_menus():
    title, text = articles.extract(PAGE)
    assert title == "Tariffs on chips" and "semiconductors" in text and "Home News" not in text and "var x" not in text


def test_read_all_reports_what_could_not_be_read(session):
    c = Client({"https://a.com/1": Resp(PAGE), "https://b.com/2": Resp("<html><body><p>Subscribe</p></body></html>"),
                "https://c.com/3": Resp("", 403)})
    out = articles.read_all(session, "https://a.com/1 https://b.com/2 https://c.com/3", c)
    assert [a["ok"] for a in out] == [True, False, False]
    assert "paywall" in out[1]["error"] and "403" in out[2]["error"]


def test_the_question_sent_carries_the_article(session):
    from app.panel import question_message

    a = [{"url": "https://a.com/1", "ok": True, "title": "Tariffs on chips", "text": "body"}]
    msgs, _ = question_message(session, "impact? https://a.com/1", "1m", None, a)
    import json
    body = json.loads(msgs[0]["content"])
    assert body["question"] == "impact? [article 1]" and body["articles"][0]["text"] == "body" and "articles" in body["note"]
