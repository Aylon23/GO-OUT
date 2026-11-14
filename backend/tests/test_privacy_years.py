"""A year in the text (the 2023 of "October 2023") is not a leaked amount, even if a holding is worth about $2,023."""
from app import privacy
from app.models import Transaction
import datetime as dt


def test_years_are_not_amounts(session, fake):
    session.add(Transaction(date=dt.date(2024, 1, 2), type="BUY", ticker="AAA", quantity=10, price=202.31, currency="USD"))
    session.commit()
    privacy.clear_cache()
    assert privacy.scan(session, {"analog": "Hamas attack, October 2023", "year": 2023}) == []
    # the exact amount, to the cent, is still caught; so is a non-year whole amount
    assert privacy.scan(session, {"x": "2023.10"})
    session.add(Transaction(date=dt.date(2024, 1, 2), type="BUY", ticker="BBB", quantity=10, price=512.3, currency="USD"))
    session.commit()
    privacy.clear_cache()
    assert privacy.scan(session, {"x": 5123})


def test_an_everyday_word_used_as_a_nickname_is_not_a_name(session, fake):
    from app.models import Portfolio
    session.add(Portfolio(name="Keren", aliases="קרן, שלי"))
    session.commit()
    privacy.clear_cache()
    assert privacy.scan(session, {"reason": "התרחיש שלי מניח יציבות"}) == []
    assert privacy.scan(session, {"reason": "התיק של קרן יורד"})  # the real name is still caught
