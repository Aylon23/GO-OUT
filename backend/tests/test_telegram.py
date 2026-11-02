import datetime as dt

import pytest
from sqlmodel import Session

from app import db, services
from app.models import Portfolio
from app.telegram_bot import Bot
from tests.conftest import frame

TODAY = dt.date.today()
DAYS = [TODAY - dt.timedelta(days=29 - i) for i in range(30)]


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.updates = []
        self.next_id = 100

    def call(self, method, **p):
        if method == "getUpdates":
            u, self.updates = self.updates, []
            return u
        self.sent.append((method, p))
        if method == "sendMessage":
            self.next_id += 1
            return {"message_id": self.next_id}
        return True

    def texts(self):
        return [p.get("text", "") for m, p in self.sent if m in ("sendMessage", "editMessageText")]

    def last_buttons(self):
        for m, p in reversed(self.sent):
            if m == "sendMessage" and p.get("reply_markup"):
                return [b for row in p["reply_markup"]["inline_keyboard"] for b in row]
        return []


@pytest.fixture
def bot(session, fake):
    services.bump_revision()
    for t, a, b in (("ILS=X", 3.7, 3.7), ("SPY", 100, 110), ("QQQ", 100, 110), ("AVGO", 100, 130)):
        fake.frames[t] = frame([(d.isoformat(), a + (b - a) * i / 29) for i, d in enumerate(DAYS)])
    session.add(Portfolio(id=2, name="Keren", aliases="קרן"))
    session.add(Portfolio(id=3, name="Shir", aliases="שיר"))
    st = services.get_settings(session)
    st.telegram_token, st.telegram_pair_code = "1:x", "ABC123"
    session.add(st)
    session.commit()
    tg = FakeTelegram()
    b = Bot(tg, session_factory=lambda: Session(db.get_engine()))
    return b, tg


def msg(chat, text, uid=[0]):
    uid[0] += 1
    return {"update_id": uid[0], "message": {"chat": {"id": chat}, "text": text}}


def press(chat, data, mid=101, uid=[1000]):
    uid[0] += 1
    return {"update_id": uid[0], "callback_query": {"id": "q", "data": data, "message": {"chat": {"id": chat}, "message_id": mid}}}


def test_pairing_and_strangers_are_ignored(bot):
    b, tg = bot
    b.handle(msg(999, "קרן קנתה 5 AVGO"))
    assert tg.sent == []  # not paired: silence
    b.handle(msg(42, "/start WRONG"))
    assert "Settings" in tg.texts()[-1]
    b.handle(msg(42, "/start ABC123"))
    assert tg.texts()[-1].startswith("מחובר")
    b.handle(msg(999, "/report"))
    assert len(tg.sent) == 2  # a second chat still gets nothing


def test_trade_from_phone_with_confirmation(bot, session):
    b, tg = bot
    b.handle(msg(42, "/start ABC123"))
    b.handle(msg(42, "קרן קנתה 5 AVGO"))
    assert "קנייה AVGO" in tg.texts()[-1] and "Keren: 5 מניות" in tg.texts()[-1]
    ok = next(x for x in tg.last_buttons() if x["callback_data"].startswith("ok:"))
    b.handle(press(42, ok["callback_data"]))
    assert "נשמר ✅ Keren" in tg.texts()[-1] and "נשמר גם כהמלצה" in tg.texts()[-1]
    from app.models import Recommendation, Transaction
    from sqlmodel import select
    with Session(db.get_engine()) as s:
        txs = s.exec(select(Transaction).where(Transaction.portfolio_id == 2)).all()
        assert len(txs) == 1 and txs[0].quantity == 5 and txs[0].rec_id
        assert len(s.exec(select(Recommendation)).all()) == 1


def test_asks_which_portfolio_when_none_named(bot):
    b, tg = bot
    b.handle(msg(42, "/start ABC123"))
    b.handle(msg(42, "bought 2 AVGO"))
    assert tg.texts()[-1] == "לאיזה תיק?"
    shir = next(x for x in tg.last_buttons() if x["text"] == "Shir")
    b.handle(press(42, shir["callback_data"]))
    assert "Shir: 2 מניות" in tg.texts()[-1]
    cancel = next(x for x in tg.last_buttons() if x["callback_data"].startswith("no:"))
    b.handle(press(42, cancel["callback_data"]))
    assert tg.texts()[-1] == "בוטל."


def test_reports_and_weekly_schedule(bot):
    b, tg = bot
    b.handle(msg(42, "/start ABC123"))
    b.handle(msg(42, "קרן קנתה 5 AVGO"))
    b.handle(press(42, next(x for x in tg.last_buttons() if x["callback_data"].startswith("ok:"))["callback_data"]))
    b.handle(msg(42, "/report קרן"))
    assert "Keren" in tg.texts()[-1] and "S&P 500" in tg.texts()[-1]
    b.handle(msg(42, "/calls"))
    assert "המלצות" in tg.texts()[-1]
    sunday = dt.datetime(2026, 11, 8, 10, 0)
    assert b.maybe_weekly(sunday) and "סיכום השבוע" in tg.texts()[-1]
    assert not b.maybe_weekly(sunday)  # once per Sunday
    assert not b.maybe_weekly(dt.datetime(2026, 11, 9, 10, 0))  # not on Monday
