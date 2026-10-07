"""Zapping, watched-progress bars and the last channel waiting on start-up."""

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem
from shiboken6 import isValid

from luna_iptv import theme
from luna_iptv.library import (
    CARD_GAP,
    POSTER_ART_HEIGHT,
    POSTER_HEIGHT,
    POSTER_WIDTH,
    PROGRESS_ROLE,
    CardGrid,
    ChannelDelegate,
    ChannelModel,
)
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

LIVE = [Channel(f"l{i}", f"Kanal {i}", f"file:///l{i}.ts") for i in range(3)]
FILM = Channel("film", "Film", "file:///film.mkv", kind="movie")


@pytest.fixture
def make_window(qt_app, tmp_path, monkeypatch):
    windows = []

    def create(recent=()):
        store = Store(tmp_path / f"library{len(windows)}.sqlite3")
        store.save_source({"id": "home", "type": "m3u", "name": "Home"})
        store.replace_channels("home", [*LIVE, FILM])
        for channel_id, position, duration in recent:
            store.save_progress(channel_id, position, duration)
        window = MainWindow(store)
        monkeypatch.setattr(window.player, "load", lambda *a, **kw: None)
        monkeypatch.setattr(window.player, "set_property", lambda *a: None)
        windows.append(window)
        qt_app.processEvents()
        return window

    yield create
    for window in windows:
        if isValid(window):
            window.close()
    qt_app.processEvents()


def channel(window, channel_id):
    return next(c for c in window.store.channels() if c.id == channel_id)


def test_page_keys_zap_through_live_channels_in_grid_order(make_window):
    window = make_window()
    window.play(channel(window, "home:l0"))
    assert window.zap(1)
    assert window.current.id == "home:l1"
    assert window.channel_list.currentIndex().data(Qt.UserRole).id == "home:l1"
    window.zap(-1)
    window.zap(-1)  # wraps from the first to the last channel
    assert window.current.id == "home:l2"


def test_zap_without_live_playback_only_scrolls(make_window):
    window = make_window()
    assert not window.zap(1)
    assert window.current is None
    window.play(channel(window, "home:film"))
    assert not window.zap(1)
    assert window.current.id == "home:film"


def test_progress_role_marks_only_resumable_films():
    model = ChannelModel()
    ended = Channel("ended", "Bitti", "file:///e.mkv", kind="movie")
    model.reset(
        [LIVE[0], FILM, ended],
        set(),
        {"l0": (50, 100), "film": (30, 120), "ended": (115, 120)},
    )
    assert model.index(0, 0).data(PROGRESS_ROLE) is None  # live channels have no progress
    assert model.index(1, 0).data(PROGRESS_ROLE) == pytest.approx(0.25)
    assert model.index(2, 0).data(PROGRESS_ROLE) is None  # finished
    changed = []
    model.dataChanged.connect(lambda first, last, roles: changed.append(first.row()))
    model.set_progress("film", 60, 120)
    assert model.index(1, 0).data(PROGRESS_ROLE) == pytest.approx(0.5)
    assert changed == [1]


def test_poster_card_paints_a_gold_progress_bar(qt_app):
    model = ChannelModel()
    model.reset([FILM], set(), {"film": (60, 120)})
    grid = CardGrid()
    grid.set_poster_mode(True)
    delegate = ChannelDelegate(grid)
    width, height = POSTER_WIDTH + CARD_GAP, POSTER_HEIGHT + CARD_GAP
    canvas = QImage(width, height, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    art_bottom = CARD_GAP // 2 + round(POSTER_WIDTH * 1.5)
    assert art_bottom <= CARD_GAP // 2 + POSTER_ART_HEIGHT + 1
    sample = canvas.pixelColor(CARD_GAP // 2 + 20, art_bottom - 12)
    assert sample == QColor(theme.GOLD)


def test_last_live_channel_waits_selected_without_playing(make_window, qt_app):
    window = make_window(recent=[("home:l1", 10, 0), ("home:film", 30, 120)])
    qt_app.processEvents()
    assert window.current is None
    assert window.channel_list.currentIndex().data(Qt.UserRole).id == "home:l1"
    assert "Kanal 1" in window.welcome_subtitle.text()


def test_media_controls_follow_playback_and_drive_the_window(make_window):
    window = make_window()
    window.play(channel(window, "home:l0"))
    state = window.mpris.updates[-1]
    assert state["status"] == "Playing" and state["title"] == "Kanal 0"
    assert state["can_go_next"] and state["track_id"].startswith("/org/mpris/MediaPlayer2/luna/")
    window.playback_property(window._playback_token, "pause", True)
    assert window.mpris.updates[-1]["status"] == "Paused"
    window.mpris.controller.next()  # the media "next" key
    assert window.current.id == "home:l1"
    assert window.mpris.updates[-1]["title"] == "Kanal 1"
    window.mpris.controller.stop()
    assert window.mpris.updates[-1] == {"status": "Stopped"}


def test_media_controls_hear_about_seeks_and_stops(make_window):
    window = make_window()
    window.play(channel(window, "home:film"))
    window.player_property("time-pos", 42.0)
    window.player_property("seeking", True)
    window.player_property("seeking", False)
    assert window.mpris.seeks == [42_000_000]
    window.stop_playback()
    assert window.mpris.position == 0


def test_history_reset_removes_progress_bars(make_window):
    window = make_window(recent=[("home:film", 30, 120)])
    index = window.model.index(window.model._rows["home:film"], 0)
    assert index.data(PROGRESS_ROLE) == pytest.approx(0.25)
    window.clear_history(reset_progress=True)
    assert index.data(PROGRESS_ROLE) is None


def test_last_live_channel_is_found_behind_many_films(make_window, qt_app):
    recent = [("home:l2", 10, 0)] + [("home:film", 30 + i, 120) for i in range(25)]
    window = make_window(recent=recent)
    qt_app.processEvents()
    assert window.channel_list.currentIndex().data(Qt.UserRole).id == "home:l2"
