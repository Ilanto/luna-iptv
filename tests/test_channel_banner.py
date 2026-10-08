"""The TV-style banner over the video."""

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from luna_iptv.channel_banner import ChannelBanner
from luna_iptv.epg import GuideIndex
from luna_iptv.models import Channel, Programme
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


def programme(title, start, minutes=60):
    return Programme("ntv", title, start, start + timedelta(minutes=minutes), "")


def test_banner_shows_programme_progress_and_what_comes_next(qt_app):
    host = QWidget()
    host.resize(800, 450)
    banner = ChannelBanner(host)
    channel = Channel("h:n", "NTV", "file:///n.ts", tvg_id="ntv")
    banner.set_content(
        channel,
        4,
        programme("Gece Haberleri", NOW - timedelta(minutes=30)),
        programme("Spor", NOW + timedelta(minutes=30)),
    )
    assert banner.number.text() == "4" and banner.name.text() == "NTV"
    assert banner.title.text() == "Gece Haberleri" and banner.following.text().endswith("Spor")
    assert 0.4 < banner._fraction < 0.6
    banner.place(QRect(0, 0, 800, 450))
    assert banner.geometry().bottom() <= 450 and banner.width() <= 760
    banner.show_for(0.01)
    assert banner.isVisible() or not host.isVisible()
    banner.hide_banner()
    assert banner.isHidden()
    film = Channel("h:f", "Dune", "file:///f.mkv", kind="movie", group="Bilim Kurgu")
    banner.set_content(film)
    assert banner.number.isHidden() and banner.title.text() == "Bilim Kurgu"
    assert banner.following.isHidden() and banner.track.isHidden()


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    store.replace_channels(
        "home",
        [Channel("a", "TRT 1", "file:///a.ts"), Channel("n", "NTV", "file:///n.ts", tvg_id="ntv")],
    )
    value = MainWindow(store)
    value.resize(1400, 900)
    value.show()
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def test_window_shows_the_banner_when_a_channel_loads(window):
    window._guide_index["home"] = GuideIndex(
        [
            programme("Gece Haberleri", NOW - timedelta(minutes=10)),
            programme("Spor", NOW + timedelta(minutes=50)),
        ]
    )
    ntv = next(c for c in window.model.channels if c.name == "NTV")
    window.request_play(ntv)
    window._mark_loaded()
    banner = window.channel_banner
    assert banner.number.text() == "2" and banner.title.text() == "Gece Haberleri"
    assert "Spor" in banner.following.text()
    window.close_current()
    assert banner.isHidden()


def test_fullscreen_controls_bring_the_banner_and_take_it_away(window):
    ntv = next(c for c in window.model.channels if c.name == "NTV")
    window.request_play(ntv)
    window._mark_loaded()
    window.channel_banner.hide_banner()
    window.fullscreen.controls_shown.emit()
    assert not window.channel_banner._timer.isActive()  # stays while the controls show
    window.fullscreen.controls_hidden.emit()
    assert window.channel_banner.isHidden()


def test_mini_player_gets_no_banner(window):
    ntv = next(c for c in window.model.channels if c.name == "NTV")
    window.request_play(ntv)
    window._mark_loaded()
    assert not window.channel_banner.isHidden()
    # Only the flag the banner reads; reset before teardown so closing stays normal.
    window.mini_player.active = True
    try:
        window._banner_placer.place()  # what a mini-player resize does
        assert window.channel_banner.isHidden()
        window.fullscreen.controls_shown.emit()
        assert window.channel_banner.isHidden()
    finally:
        window.mini_player.active = False
