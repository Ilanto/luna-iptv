"""Channel cards: a fitted grid and cheap on-now lookups while painting."""

from datetime import datetime, timedelta, timezone

from shiboken6 import isValid

from luna_iptv.epg import GuideIndex
from luna_iptv.library import CARD_GAP, CARD_WIDTH, CardGrid
from luna_iptv.models import Channel, Programme
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

NOON = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def programme(channel, title, start, minutes=60):
    return Programme(channel, title, start, start + timedelta(minutes=minutes), "")


def test_guide_index_finds_on_now_and_upcoming_per_channel():
    index = GuideIndex(
        [
            programme("b", "Later", NOON + timedelta(hours=1)),
            programme("a", "Morning", NOON - timedelta(hours=1)),
            programme("a", "Noon", NOON),
            programme("b", "Gap before", NOON - timedelta(hours=2)),
        ]
    )
    assert index.now("a", NOON + timedelta(minutes=5)).title == "Noon"
    assert index.now("b", NOON) is None  # between programmes
    assert index.now("missing", NOON) is None
    assert [p.title for p in index.upcoming("b", 3, NOON)] == ["Later"]
    assert [p.title for p in index.upcoming("a", 3, NOON - timedelta(minutes=90))] == [
        "Morning",
        "Noon",
    ]


def test_card_grid_fills_its_width_with_whole_columns(qt_app):
    grid = CardGrid()
    grid.resize(1000, 600)
    grid.show()
    qt_app.processEvents()
    columns = grid.viewport().width() // (CARD_WIDTH + CARD_GAP)
    assert columns >= 1
    assert grid.gridSize().width() == grid.viewport().width() // columns
    grid.close()


def test_live_cards_ask_the_guide_index_only_for_live_channels(qt_app, tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    window = MainWindow(store)
    try:
        now = datetime.now(timezone.utc)
        window._guide_index["home"] = GuideIndex(
            [programme("tv1", "On now", now - timedelta(minutes=5))]
        )
        live = Channel("home:a", "A", "file:///a.ts", tvg_id="tv1")
        movie = Channel("home:m", "M", "file:///m.mkv", kind="movie", tvg_id="tv1")
        other = Channel("away:a", "A", "file:///a.ts", tvg_id="tv1")
        assert window.programme_now(live).title == "On now"
        assert window.programme_now(movie) is None
        assert window.programme_now(other) is None
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()
