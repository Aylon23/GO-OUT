"""Telegram bot: type trades and get reports from your phone.

Runs inside the app and asks Telegram for new messages (long polling), so it works from a home
computer with no public address. It only answers the one chat paired in Settings.

    "קרן קנתה 5 AVGO"   -> draft with buttons: add / add without logging a call / cancel
    /report [name]      -> weekly summary (all portfolios, or one)
    /month [name]       -> monthly summary
    /calls              -> how your recommendations are doing vs the S&P 500
    /portfolios         -> value and gain of each portfolio
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
import threading
import time
import uuid

from sqlmodel import Session, select

from . import db
from .models import Portfolio, Settings

log = logging.getLogger("telegram")

HELP = (
    "אפשר לכתוב לי פעולה במילים, למשל:\n"
    "• קרן קנתה 5 AVGO\n"
    "• קרן, שיר ואבא קנו 3 NVDA ב-180\n"
    "• כולם מכרו TSLA\n"
    "• הפקדתי 5000 דולר לתיק של שיר\n\n"
    "פקודות:\n"
    "/report – סיכום שבועי (או /report קרן)\n"
    "/month – סיכום חודשי\n"
    "/calls – איך ההמלצות שלך מול ה-S&P\n"
    "/portfolios – כל התיקים"
)
TYPE_HE = {"BUY": "קנייה", "SELL": "מכירה", "DIVIDEND": "דיבידנד", "DEPOSIT": "הפקדה", "WITHDRAWAL": "משיכה"}
MISSING_HE = {"ticker": "איזו מניה? כתוב סימול כמו AVGO.", "quantity": "כמה? למשל \"5 מניות\" או \"ב-1000 דולר\".",
              "amount": "כמה כסף? למשל \"5000 דולר\".", "position": "אף תיק מהרשימה לא מחזיק את המניה הזו."}


class TelegramClient:
    """Thin HTTP client for the Bot API (replaced by a fake in tests)."""

    def __init__(self, token: str):
        import httpx

        self.http = httpx.Client(timeout=40)
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, **params) -> dict:
        r = self.http.post(self.base + method, json={k: v for k, v in params.items() if v is not None})
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or f"Telegram error {r.status_code}")
        return data["result"]


def _money(v, ccy) -> str:
    if v is None:
        return "?"
    return f"\u2066{'$' if ccy == 'USD' else '₪'}{v:,.2f}\u2069"


def describe(d: dict) -> str:
    """The draft, in Hebrew, for the confirmation message."""
    typ = TYPE_HE.get(d["type"], d["type"])
    lines = []
    if d["type"] in ("BUY", "SELL"):
        lines.append(f"{typ} {d['ticker']} · {d['date']}")
        lines.append(f"מחיר: {_money(d.get('price'), d.get('currency'))}" + (" (שער סגירה)" if d.get("price_source") == "close" else ""))
        for t in d.get("targets") or []:
            if t["skip"]:
                why = "לא מחזיק" if not t.get("held") else f"מחזיק רק {t['held']:g}"
                lines.append(f"• {t['portfolio_name']}: לא ({why})")
            else:
                total = (t["quantity"] or 0) * (d.get("price") or 0)
                lines.append(f"• {t['portfolio_name']}: {t['quantity']:g} מניות = {_money(total, d.get('currency'))}")
    else:
        what = f" מ-{d['ticker']}" if d.get("ticker") else ""
        lines.append(f"{typ}{what}: {_money(d.get('price'), d.get('currency'))} · {d['date']}")
        lines.append("לתיק: " + ", ".join(t["portfolio_name"] for t in d.get("targets") or []))
    lines += [f"⚠️ {w}" for w in d.get("warnings", [])]
    return "\n".join(lines)


class Bot:
    def __init__(self, client, session_factory=None):
        self.client = client
        self.session_factory = session_factory or (lambda: Session(db.get_engine()))
        self.pending: dict[str, tuple[float, dict]] = {}
        self.offset = None

    # ---- helpers
    def settings(self, s: Session) -> Settings:
        from .services import get_settings

        return get_settings(s)

    def send(self, chat_id, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> dict:
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]} if buttons else None
        return self.client.call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)

    def _remember(self, draft: dict) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v[0] < 3600}
        key = uuid.uuid4().hex[:10]
        self.pending[key] = (now, draft)
        return key

    def _portfolio_by_word(self, s: Session, word: str) -> Portfolio | None:
        w = word.strip().lower()
        for p in s.exec(select(Portfolio)).all():
            if w in [p.name.lower(), *[a.strip().lower() for a in (p.aliases or "").split(",") if a.strip()]]:
                return p
        return None

    # ---- updates
    def handle(self, upd: dict) -> None:
        with self.session_factory() as s:
            st = self.settings(s)
            if "callback_query" in upd:
                q = upd["callback_query"]
                chat = str(q["message"]["chat"]["id"])
                if chat != st.telegram_chat_id:
                    return
                self.client.call("answerCallbackQuery", callback_query_id=q["id"])
                self.on_button(s, chat, q["message"]["message_id"], q.get("data") or "")
                return
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            if not text:
                return
            chat = str(msg["chat"]["id"])
            if text.startswith("/start"):
                code = text[len("/start"):].strip()
                if st.telegram_pair_code and code == st.telegram_pair_code:
                    st.telegram_chat_id, st.telegram_pair_code = chat, ""
                    s.add(st)
                    s.commit()
                    self.send(chat, "מחובר ✅\n\n" + HELP)
                elif chat == st.telegram_chat_id:
                    self.send(chat, HELP)
                else:
                    self.send(chat, "כדי להתחבר, שלח /start עם הקוד שמופיע באפליקציה, בעמוד Settings.")
                return
            if chat != st.telegram_chat_id:
                return  # only the paired chat
            self.on_text(s, chat, text)

    def on_text(self, s: Session, chat: str, text: str) -> None:
        from . import calls as calls_mod
        from . import reports
        from .omnibox import parse

        cmd, _, rest = text.partition(" ")
        cmd = cmd.lower().split("@")[0]
        if cmd in ("/help", "/עזרה"):
            self.send(chat, HELP)
            return
        if cmd in ("/report", "/week", "/month"):
            period = "month" if cmd == "/month" else "week"
            if rest.strip():
                p = self._portfolio_by_word(s, rest)
                if not p:
                    self.send(chat, f"לא מצאתי תיק בשם \"{rest.strip()}\".")
                    return
                self.send(chat, reports.portfolio_report(s, p.id, period)["text"])
            else:
                self.send(chat, reports.all_report(s, period)["text"])
            return
        if cmd == "/calls":
            data = calls_mod.calls_payload(s)
            c = data["scorecard"]["all"]
            if not data["rows"]:
                self.send(chat, "אין עדיין המלצות. כל פעולה שתאשר כאן נשמרת כהמלצה.")
                return
            lines = [f"🎯 {c['count']} המלצות · {c['worked']} מתוך {c['scored']} הצליחו מול ה-S&P"]
            if c["avg_edge"] is not None:
                lines.append(f"יתרון ממוצע: \u2066{c['avg_edge'] * 100:+.1f}\u2069 נקודות")
            for r in data["rows"][:8]:
                if r["edge"] is None:
                    continue
                mark = "✅" if r["worked"] else "🔻"
                lines.append(f"{mark} {TYPE_HE[r['action']]} {r['ticker']} ({r['date']}): \u2066{r['return'] * 100:+.1f}%\u2069 מול S&P \u2066{r['spy'] * 100:+.1f}%\u2069")
            self.send(chat, "\n".join(lines))
            return
        if cmd in ("/portfolios", "/tikim"):
            from .portfolios import overview_payload

            o = overview_payload(s, "1M")
            lines = []
            for r in o["rows"]:
                if r["empty"]:
                    continue
                lines.append(f"• {r['name']}: {_money(r['value'], o['base'])} · מול עלות \u2066{(r['cost_gain_pct'] or 0) * 100:+.1f}%\u2069")
            self.send(chat, "\n".join(lines) or "אין עדיין תיקים עם אחזקות.")
            return
        # free text: a trade
        try:
            d = parse(s, text)
        except Exception as exc:  # parsing should never take the bot down
            self.send(chat, f"לא הצלחתי להבין: {exc}")
            return
        self.offer(s, chat, d)

    def ask_portfolio(self, s: Session, chat: str, d: dict) -> None:
        key = self._remember({"text": d["text"], "choose": d.get("_choose") or {}})
        ps = s.exec(select(Portfolio).order_by(Portfolio.id)).all()
        buttons = [[(p.name, f"pf:{key}:{p.id}")] for p in ps] + [[("כל התיקים", f"pf:{key}:all"), ("❌ ביטול", f"no:{key}")]]
        self.send(chat, "לאיזה תיק?", buttons)

    def offer(self, s: Session, chat: str, d: dict, force_pids: list[int] | None = None, choose: dict | None = None) -> None:
        if d.get("choices"):
            key = self._remember({"text": d["text"], "force": force_pids, "choose": choose or {}})
            dim, opts = next(iter(d["choices"].items()))
            label = "איזו מניה?" if dim == "ticker" else "כמה?"
            self.send(chat, "התכוונת ל… " + label, [[(f"{o:g}" if isinstance(o, float) else str(o), f"ch:{key}:{dim}={o}") for o in opts],
                                                     [("❌ ביטול", f"no:{key}")]])
            return
        if "portfolio" in d["missing"]:
            d["_choose"] = choose
            self.ask_portfolio(s, chat, d)
            return
        if d["missing"]:
            self.send(chat, "\n".join(MISSING_HE.get(m, m) for m in d["missing"]))
            return
        key = self._remember(d)
        rows = [[("✅ הוסף", f"ok:{key}")]]
        if d["type"] in ("BUY", "SELL"):
            rows = [[("✅ הוסף + שמור כהמלצה", f"ok:{key}")], [("✅ הוסף בלי המלצה", f"plain:{key}"), ("❌ ביטול", f"no:{key}")]]
        else:
            rows[0].append(("❌ ביטול", f"no:{key}"))
        self.send(chat, describe(d), rows)

    def on_button(self, s: Session, chat: str, message_id: int, data: str) -> None:
        from .omnibox import commit, parse
        from .services import bump_revision

        kind, _, rest = data.partition(":")
        key, _, extra = rest.partition(":")
        item = self.pending.pop(key, None)
        if item is None:
            self.client.call("editMessageText", chat_id=chat, message_id=message_id, text="פג תוקף. שלח שוב.")
            return
        _, d = item
        if kind == "no":
            self.client.call("editMessageText", chat_id=chat, message_id=message_id, text="בוטל.")
            return
        if kind == "pf":
            choose = d.get("choose") or {}
            if extra == "all":
                force = [p.id for p in s.exec(select(Portfolio)).all()]
            else:
                force = [int(extra)]
            d2 = parse(s, d["text"], force_pids=force, choose=choose)
            self.client.call("editMessageText", chat_id=chat, message_id=message_id, text=f"תיק: {', '.join(t['portfolio_name'] for t in d2['targets'])}")
            self.offer(s, chat, d2, force, choose)
            return
        if kind == "ch":
            dim, _, val = extra.partition("=")
            choose = dict(d.get("choose") or {})
            choose[dim] = float(val) if dim == "quantity" else val
            d2 = parse(s, d["text"], force_pids=d.get("force"), choose=choose)
            self.client.call("editMessageText", chat_id=chat, message_id=message_id, text=f"בחרת: {val}")
            self.offer(s, chat, d2, d.get("force"), choose)
            return
        try:
            out = commit(s, d, log_call=(kind == "ok"))
        except Exception as exc:
            self.client.call("editMessageText", chat_id=chat, message_id=message_id, text=f"לא נשמר: {exc}")
            return
        bump_revision()
        lines = [describe(d), ""]
        if out["saved"]:
            lines.append("נשמר ✅ " + ", ".join(x["portfolio"] for x in out["saved"]))
        for f in out["failed"]:
            lines.append(f"לא נשמר ב-{f['portfolio']}: {f['error']}")
        if out.get("rec_id"):
            lines.append("📌 נשמר גם כהמלצה")
        self.client.call("editMessageText", chat_id=chat, message_id=message_id, text="\n".join(lines))

    # ---- scheduled weekly summary
    def maybe_weekly(self, now: dt.datetime | None = None) -> bool:
        from . import reports

        now = now or dt.datetime.now()
        with self.session_factory() as s:
            st = self.settings(s)
            if not (st.weekly_report and st.telegram_chat_id):
                return False
            # Sundays from 09:00 local time, once per week
            if now.weekday() != 6 or now.hour < 9 or st.weekly_report_sent == now.date().isoformat():
                return False
            self.send(st.telegram_chat_id, reports.all_report(s, "week")["text"])
            st.weekly_report_sent = now.date().isoformat()
            s.add(st)
            s.commit()
            return True

    # ---- loop
    def poll_once(self, timeout: int = 30) -> int:
        updates = self.client.call("getUpdates", offset=self.offset, timeout=timeout, allowed_updates=["message", "callback_query"])
        for u in updates:
            self.offset = u["update_id"] + 1
            try:
                self.handle(u)
            except Exception:
                log.exception("telegram update failed")
        return len(updates)


_runner: dict = {"thread": None, "stop": None, "error": "", "username": ""}


def status() -> dict:
    t = _runner["thread"]
    return {"running": bool(t and t.is_alive()), "error": _runner["error"], "username": _runner["username"]}


def start(token: str) -> None:
    """(Re)start the bot thread for this token."""
    stop()
    if not token:
        return
    ev = threading.Event()

    def run():
        backoff = 2
        try:
            client = TelegramClient(token)
            _runner["username"] = client.call("getMe").get("username", "")
            _runner["error"] = ""
        except Exception as exc:
            _runner["error"] = f"Telegram didn't accept the token or can't be reached: {exc}"
            return
        bot = Bot(client)
        while not ev.is_set():
            try:
                bot.poll_once(timeout=25)
                bot.maybe_weekly()
                backoff = 2
                _runner["error"] = ""
            except Exception as exc:
                _runner["error"] = f"Connection problem, retrying: {exc}"
                ev.wait(backoff)
                backoff = min(backoff * 2, 60)

    th = threading.Thread(target=run, name="telegram-bot", daemon=True)
    _runner.update(thread=th, stop=ev)
    th.start()


def stop() -> None:
    if _runner["stop"] is not None:
        _runner["stop"].set()
    _runner.update(thread=None, stop=None)


def new_pair_code() -> str:
    return secrets.token_hex(3).upper()
