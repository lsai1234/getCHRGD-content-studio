"""The UK market spine (Bet 2): the context pack, the wrap-aware calendar, and
that both thread into the engine + discovery scans."""

from __future__ import annotations

from datetime import date

from chrgd.uk import (
    uk_calendar_beats,
    uk_calendar_seed,
    uk_context_block,
)


def test_context_block_names_britain_concretely():
    block = uk_context_block()
    assert "UK MARKET SPINE" in block
    # Named, concrete British texture — not just the word "UK".
    assert "PureGym" in block
    assert "£" in block
    assert "British English" in block


def test_calendar_window_matches_the_season():
    # High summer → Love Island / heatwave beat is live; the January rush is not.
    summer = uk_calendar_beats(date(2026, 7, 22))
    titles = " ".join(b["title"] for b in summer)
    assert "Love Island" in titles
    assert "January gym rush" not in titles


def test_calendar_window_wraps_the_year_end():
    # A window like 12-27:02-10 must be live on BOTH sides of New Year.
    for d in (date(2026, 12, 30), date(2026, 1, 5)):
        titles = " ".join(b["title"] for b in uk_calendar_beats(d))
        assert "January gym rush" in titles


def test_calendar_seed_flags_itself_as_context_not_search():
    seed = uk_calendar_seed(date(2026, 2, 15))  # Six Nations season
    assert "UK CULTURAL CALENDAR" in seed
    assert "Six Nations" in seed
    # It must tell the scout to STILL web-search for the live detail.
    assert "web-search" in seed.lower() or "still" in seed.lower()


def test_spine_reaches_engine_base():
    from chrgd.pipeline import engine_base

    assert "UK MARKET SPINE" in engine_base()


def test_spine_and_calendar_reach_the_moments_scan(monkeypatch):
    # The moments scan carries BOTH the market pack (system) and the calendar
    # seed (ask), so the scout opens UK-native and season-aware.
    import chrgd.trends as trends

    seen = {}

    class FakeClient:
        def search(self, system, user):
            seen["system"] = system
            seen["user"] = user
            return '{"moments": []}'

    from chrgd.config import Settings

    trends.scout_discover(
        Settings(CHRGD_SECRET_KEY="x"), "moments", 3, client=FakeClient()
    )
    assert "UK MARKET SPINE" in seen["system"]
    assert "UK CULTURAL CALENDAR" in seen["user"]


def test_non_moments_lane_gets_spine_but_not_calendar(monkeypatch):
    import chrgd.trends as trends
    from chrgd.config import Settings

    seen = {}

    class FakeClient:
        def search(self, system, user):
            seen["system"] = system
            seen["user"] = user
            return '{"moments": []}'

    trends.scout_discover(
        Settings(CHRGD_SECRET_KEY="x"), "ragebait", 3, client=FakeClient()
    )
    assert "UK MARKET SPINE" in seen["system"]
    assert "UK CULTURAL CALENDAR" not in seen["user"]
