"""The Rehber page: a virtualised timetable over the XMLTV guide."""

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QPointF
from shiboken6 import isValid

from luna_iptv.epg import GuideIndex
from luna_iptv.guide_view import HEADER, LOGO_COLUMN, ROW, GuideGrid, ProgrammeCard
from luna_iptv.models import Channel, Programme
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


def programme(channel, title, start, minutes=60):
    return Programme(channel, title, start, start + timedelta(minutes=minutes), "Açıklama")


def test_between_returns_overlaps_including_one_that_started_earlier():
    index = GuideIndex(
        [
            programme("a", "Long", NOW - timedelta(hours=3), 240),
            programme("a", "Next", NOW + timedelta(hours=1)),
            programme("a", "Later", NOW + timedelta(hours=5)),
        ]
    )
    titles = [p.title for p in index.between("a", NOW, NOW + timedelta(hours=2))]
    assert titles == ["Long", "Next"]
    assert index.between("missing", NOW, NOW + timedelta(hours=1)) == []
    assert index.channel_ids() == {"a"}


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    store.replace_channels(
        "home",
        [
            Channel("n", "Nova", "file:///n.ts", tvg_id="nova"),
            Channel("h", "Haber", "file:///h.ts", tvg_id="haber"),
            Channel("x", "Rehbersiz", "file:///x.ts", tvg_id="none"),
            Channel("f", "Film", "file:///f.mkv", kind="movie", tvg_id="nova"),
        ],
    )
    value = MainWindow(store)
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def test_guide_page_lists_only_live_channels_with_programmes(window):
    window.set_section("guide")
    assert window.library_pages.currentWidget() is window.guide_view
    assert window.guide_view.empty.isVisibleTo(window)  # no XMLTV loaded yet
    window._guide_index["home"] = GuideIndex(
        [
            programme("nova", "Derin Mavi", NOW - timedelta(minutes=10)),
            programme("haber", "Gündem", NOW),
        ]
    )
    window.refresh_guide_view()
    assert [c.name for c, _ in window.guide_view.grid.rows] == ["Nova", "Haber"]
    assert window.guide_view.grid.isVisibleTo(window)
    window.set_section("live")
    assert window.library_pages.currentWidget() is window.browse


def test_search_keeps_channels_with_matching_programmes(window):
    window._guide_index["home"] = GuideIndex(
        [programme("nova", "Derin Mavi", NOW), programme("haber", "Gündem Özel", NOW)]
    )
    window.set_section("guide")
    window.guide_view.search.setText("gundem")
    assert [c.name for c, _ in window.guide_view.grid.rows] == ["Haber"]
    assert len(window.guide_view.grid.matches) == 1
    window.guide_view.search.clear()
    assert len(window.guide_view.grid.rows) == 2


def test_grid_hits_programmes_and_logo_column(qt_app):
    grid = GuideGrid()
    grid.resize(1000, 400)
    index = GuideIndex([programme("nova", "Derin Mavi", NOW - timedelta(minutes=10))])
    channel = Channel("home:n", "Nova", "file:///n.ts", tvg_id="nova")
    grid.set_rows([(channel, index)])
    grid.scroll_to(NOW)
    y = HEADER + ROW / 2
    hit_channel, hit = grid.hit(QPointF(grid.x_for(NOW), y))
    assert hit_channel is channel and hit.title == "Derin Mavi"
    assert grid.hit(QPointF(LOGO_COLUMN / 2, y)) == (channel, None)
    assert grid.hit(QPointF(grid.x_for(NOW), y + ROW)) is None  # below the last row
    grid.close()


def test_programme_card_offers_reminders_only_for_future_programmes(qt_app):
    channel = Channel("home:n", "Nova", "file:///n.ts", tvg_id="nova")
    future = programme("nova", "Sonra", NOW + timedelta(hours=2))
    card = ProgrammeCard(channel, future, can_remind=True)
    assert card.remind_button is not None
    reminded = []
    card.remind.connect(lambda c, p: reminded.append(p.title))
    card.remind_button.click()
    assert reminded == ["Sonra"]
    live = ProgrammeCard(
        channel, programme("nova", "Şimdi", NOW - timedelta(minutes=5)), can_remind=True
    )
    assert live.remind_button is None
    watched = []
    live.watch.connect(watched.append)
    live.watch_button.click()
    assert watched == [channel]


def test_reminder_set_from_the_guide_marks_the_programme(window):
    later = programme("nova", "Gece Belgeseli", NOW + timedelta(hours=3))
    window._guide_index["home"] = GuideIndex([later])
    window.set_section("guide")
    channel = window.guide_view.grid.rows[0][0]
    card = window.guide_view.open_programme(channel, later)
    assert card.remind_button is not None and card.remind_button.isEnabled()
    card.remind_button.click()
    assert window.reminder_service.reminders()[0]["title"] == "Gece Belgeseli"
    assert (channel.id, int(later.start.timestamp())) in window.guide_view.grid.reminded
    again = window.guide_view.open_programme(channel, later)
    assert not again.remind_button.isEnabled()
    assert again.remind_button.text() == "Hatırlatıcı kurulu"
    again.close()
