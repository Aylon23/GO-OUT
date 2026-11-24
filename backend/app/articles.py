"""Links pasted into a question: the app reads each page itself and hands the article text to the models.

Free (a plain web request from this computer). A paywalled page gives only what is visible without logging in.
An article whose text happens to contain a number from the portfolios is left out (the privacy rule wins).
"""
from __future__ import annotations

import re

URL = re.compile(r"https?://[^\s<>\"')\]]+")
MAX_LINKS = 3
MAX_CHARS = 12000
MAX_BYTES = 3_000_000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def links(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL.findall(text or "")))[:MAX_LINKS]


def without_links(text: str) -> str:
    n = iter(range(1, 100))
    return URL.sub(lambda _: f"[article {next(n)}]", text or "").strip()


def extract(html: str) -> tuple[str, str]:
    """(title, article text) from a page: the <article> if there is one, else the paragraphs."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "figure", "iframe", "svg"]):
        t.decompose()
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    root = max(soup.find_all("article"), key=lambda a: len(a.get_text(" ", strip=True)), default=None) or soup.body or soup
    parts = [p.get_text(" ", strip=True) for p in root.find_all(["h1", "h2", "h3", "p", "li"])]
    parts = [p for p in parts if len(p) > 30 or p.endswith((".", ":", "?"))]
    text = "\n".join(dict.fromkeys(parts)) or root.get_text("\n", strip=True)
    return title[:200], re.sub(r"\n{2,}", "\n", text)[:MAX_CHARS]


def fetch(url: str, client=None) -> dict:
    """{url, ok, title, text, error}."""
    import httpx

    try:
        c = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "he,en;q=0.8"})
        r = c.get(url)
        if r.status_code >= 400:
            return {"url": url, "ok": False, "error": f"the site answered {r.status_code}"}
        if "html" not in r.headers.get("content-type", "html") or len(r.content) > MAX_BYTES:
            return {"url": url, "ok": False, "error": "not a web page the app can read"}
        title, text = extract(r.text)
        if len(text) < 200:
            return {"url": url, "ok": False, "title": title, "error": "almost no text (a paywall or a page built by scripts); paste the article text instead"}
        return {"url": url, "ok": True, "title": title, "text": text}
    except Exception as exc:
        return {"url": url, "ok": False, "error": f"could not open it ({type(exc).__name__})"}


def read_all(session, text: str, client=None) -> list[dict]:
    """Every link in the question, read; articles that would send a portfolio number are dropped."""
    from . import privacy

    out = []
    for u in links(text):
        a = fetch(u, client)
        if a["ok"] and privacy.scan(session, a["text"]):
            a = {"url": u, "ok": False, "title": a.get("title", ""), "error": "it contains a number that matches a portfolio amount, so it was not sent"}
        out.append(a)
    return out
