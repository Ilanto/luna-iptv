"""Multi-view routing and lifetime with inert players; no mpv or native windows."""

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from luna_iptv.models import Channel, Programme
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow
from tests.test_mini_player import InertPlayer, InertVideo


class TilePlayer(InertPlayer):
    instances = []

    def __init__(self, parent):
        super().__init__(parent)
        self.loads = []
        self.properties = {}
        self.events = []
        self.instances.append(self)

    def set_property(self, name, value):
        self.properties[name] = value
        self.events.append((name, value))
        return super().set_property(name, value)

    def load(self, url, headers=None):
        self.loads.append((url, headers))
        self.events.append(("load", url))

    def shutdown(self):
        self.events.append(("shutdown", None))
        super().shutdown()


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    import luna_iptv.layout as layout_module
    import luna_iptv.window as window_module

    monkeypatch.setattr(window_module, "Player", InertPlayer)
    monkeypatch.setattr(layout_module, "VideoWidget", InertVideo)
    # Missing feature fails at the public entry point before importing its module.
    assert hasattr(MainWindow, "open_multiview"), "Multi-view entry point is missing"
    import luna_iptv.multiview as multi_module

    TilePlayer.instances = []
    monkeypatch.setattr(multi_module, "Player", TilePlayer)
    monkeypatch.setattr(multi_module, "VideoWidget", InertVideo)
    store = Store(tmp_path / "multi.sqlite3")
    source = store.save_source({"name": "Multi", "type": "m3u", "location": "multi.m3u"})
    store.replace_channels(
        source,
        [Channel(str(i), f"Kanal {i}", f"https://example.test/{i}") for i in range(6)],
    )
    window = MainWindow(store)
    yield window
    window.close()
    qt_app.processEvents()


def channels(window):
    return window.model.channels


def test_lazy_fill_replace_and_refill(window):
    view = window.open_multiview()
    assert view.isWindow() and view.windowTitle() == "Çoklu izleme"
    assert not TilePlayer.instances
    a, b, c, d = channels(window)[:4]
    assert view.add_channel(a)
    assert view.add_channel(b)
    first, second = (tile.player for tile in view.tiles[:2])
    view.focus_tile(0)
    assert view.add_channel(c)
    assert [t.channel for t in view.tiles[:2]] == [c, b]
    assert view.tiles[0].player is first  # reuse one handle when replacing a stream
    assert len(TilePlayer.instances) == 2
    view.tiles[1].clear_button.click()
    assert second.shutdown_count == 1 and view.tiles[1].player is None
    assert view.add_channel(d)
    assert [t.channel for t in view.tiles[:2]] == [c, d]
    assert len(TilePlayer.instances) == 3


def test_focus_mute_clicks_and_shortcuts(window, qt_app):
    view = window.open_multiview()
    view.set_layout(4)
    for channel in channels(window)[:4]:
        view.add_channel(channel)
    view.activateWindow()
    qt_app.processEvents()
    QTest.mouseClick(view.tiles[1].video, Qt.LeftButton)
    assert view.focused_index == 1
    assert [t.player.properties["mute"] for t in view.tiles] == [True, False, True, True]
    QTest.keyClick(view, Qt.Key_4)
    assert view.focused_index == 3
    assert [t.player.properties["mute"] for t in view.tiles] == [True, True, True, False]
    QTest.keyClick(view, Qt.Key_M)
    assert all(t.player.properties["mute"] for t in view.tiles)
    view.focus_tile(0)
    assert all(t.player.properties["mute"] for t in view.tiles)
    QTest.keyClick(view, Qt.Key_M)
    assert [t.player.properties["mute"] for t in view.tiles] == [False, True, True, True]
    QTest.keyClick(view, Qt.Key_F)
    assert view.isFullScreen()
    QTest.keyClick(view, Qt.Key_Escape)
    assert not view.isFullScreen()
    assert not window._fullscreen


def test_layout_shrink_frees_hidden_streams_and_moves_focus(window):
    view = window.open_multiview()
    view.layout_buttons[4].click()
    for channel in channels(window)[:4]:
        view.add_channel(channel)
    old = [t.player for t in view.tiles]
    view.focus_tile(3)
    view.layout_buttons[2].click()
    assert view.tile_count == 2 and view.focused_index < 2
    assert [p.shutdown_count for p in old] == [0, 0, 1, 1]
    assert all(t.isHidden() and t.channel is None for t in view.tiles[2:])
    assert view.grid.getItemPosition(view.grid.indexOf(view.tiles[1]))[:2] == (0, 1)
    view.set_layout(4)
    assert view.grid.getItemPosition(view.grid.indexOf(view.tiles[3]))[:2] == (1, 1)
    assert view.tiles[2].player is None
    view.focus_tile(20)
    assert view.focused_index < 4


def test_close_frees_every_player_once_and_reopens_empty(window):
    view = window.open_multiview()
    view.set_layout(4)
    for channel in channels(window)[:4]:
        view.add_channel(channel)
    players = list(TilePlayer.instances)
    view.tiles[0].clear()
    view.close()
    view.close()
    assert [p.shutdown_count for p in players] == [1, 1, 1, 1]
    assert window.multiview is None
    other = window.open_multiview()
    assert other is not view and all(t.player is None for t in other.tiles)


def test_main_stop_saves_progress_and_cancels_recovery(window, monkeypatch):
    movie = Channel("movie", "Film", "https://example.test/movie", kind="movie")
    window.current = movie
    window._playback_active = True
    window._current_persistent = True
    window._position, window._duration = 123, 1000
    saved = []
    monkeypatch.setattr(window, "save_progress", lambda: saved.append(window.current))
    window.recovery.begin(movie.id, live=True)
    window.open_multiview()
    assert saved == [movie]
    assert ["stop"] in window.player.commands
    assert window.current is None and not window._playback_active
    assert "çoklu" in window.message.text().lower()
    assert window.mpris.updates[-1]["status"] == "Stopped"


def test_locked_requires_pin_each_time_and_denial_keeps_main(window, monkeypatch):
    channel = channels(window)[0]
    window.model.set_locked({channel.id})
    calls = []
    monkeypatch.setattr(window, "guard", lambda reason: calls.append(reason) or False)
    window.current = channel
    assert window.open_multiview(channel) is None
    assert window.current is channel and window.multiview is None
    monkeypatch.setattr(window, "guard", lambda reason: calls.append(reason) or True)
    view = window.open_multiview(channel)
    assert view.add_channel(channel)
    assert len(calls) == 3


def test_kids_cannot_add_locked_channel_even_to_existing_window(window, monkeypatch):
    channel = channels(window)[0]
    view = window.open_multiview()
    window.model.set_locked({channel.id})
    monkeypatch.setattr(window, "kids_profile", lambda: True)
    monkeypatch.setattr(window, "guard", lambda reason: pytest.fail("Kids must not ask PIN"))
    assert not view.add_channel(channel)
    assert not TilePlayer.instances


@pytest.mark.parametrize("action", ["profile", "close", "tray"])
def test_profile_or_main_close_closes_multiview(window, monkeypatch, action):
    view = window.open_multiview(channels(window)[0])
    player = view.tiles[0].player
    if action == "profile":
        profile = window.store.create_profile("İkinci", "#a9b8ff")
        assert window.switch_profile(profile)
    else:
        if action == "tray":
            monkeypatch.setattr(window.tray, "hide_on_close", lambda: True)
        window.close()
    assert view._closed and player.shutdown_count == 1
    assert window.multiview is None
    if action == "tray":
        monkeypatch.setattr(window.tray, "hide_on_close", lambda: False)


@pytest.mark.parametrize("kind", ["live", "movie", "series"])
def test_context_menu_only_live_and_profile_entry(window, kind):
    channel = Channel("test", "Test", "https://example.test/test", kind=kind)
    menu = window.build_channel_menu(channel)
    actions = {a.text(): a for a in menu.actions()}
    assert ("Çoklu izlemeye ekle" in actions) == (kind == "live")
    if kind == "live":
        actions["Çoklu izlemeye ekle"].trigger()
        assert window.multiview.tiles[0].channel is channel
    else:
        assert window.open_multiview(channel) is None
    assert "Çoklu izleme" in [a.text() for a in window.build_profile_menu().actions()]


def test_programme_overlay_headers_and_resource_options(window, monkeypatch):
    channel = channels(window)[0]
    channel.headers = {"User-Agent": "Luna test"}
    now = datetime.now(timezone.utc)
    programme = Programme("test", "Şimdi", now, now + timedelta(hours=1), "")
    monkeypatch.setattr(window, "programme_now", lambda channel: programme)
    view = window.open_multiview(channel)
    tile = view.tiles[0]
    assert channel.name in tile.name_label.text()
    assert tile.programme_label.text() == "Şimdi"
    assert tile.player.loads == [(channel.url, channel.headers)]
    assert tile.player.properties["hwdec"] == "auto-safe"
    assert 1 <= tile.player.properties["vd-lavc-threads"] <= 2
    assert tile.player.properties["cache-secs"] <= 5
    assert tile.player.events.index(("mute", True)) < tile.player.events.index(
        ("load", channel.url)
    )
    programme.title = "Sıradaki"
    view.refresh_programmes()
    assert tile.programme_label.text() == "Sıradaki"


def test_profile_menu_opens_empty_window(window):
    menu = window.build_profile_menu()
    next(a for a in menu.actions() if a.text() == "Çoklu izleme").trigger()
    assert window.multiview is not None
    assert all(t.channel is None for t in window.multiview.tiles)


def test_pin_profile_switch_cannot_load_previous_profile_channel(window, monkeypatch):
    channel = channels(window)[0]
    window.model.set_locked({channel.id})
    profile = window.store.create_profile("Yeni", "#a9b8ff")

    def switch_during_pin(reason):
        window.switch_profile(profile)
        return True

    monkeypatch.setattr(window, "guard", switch_during_pin)
    assert window.open_multiview(channel) is None
    assert window.multiview is None
    assert not TilePlayer.instances


def test_idle_release_and_late_signals_after_clear(window, monkeypatch):
    view = window.open_multiview(channels(window)[0])
    active = []
    monkeypatch.setattr(view.idle_inhibit, "set_active", active.append)
    tile = view.tiles[0]
    previous = tile.player
    previous.file_loaded.emit()
    assert active[-1] is True
    previous.error.emit("internal details")
    assert active[-1] is False
    assert "internal details" not in tile.programme_label.text()
    tile.clear()
    assert active[-1] is False
    view.add_channel(channels(window)[1])
    previous.file_loaded.emit()
    previous.error.emit("old error")
    assert not tile.running
    assert "old error" not in tile.programme_label.text()
    tile.player.file_loaded.emit()
    assert active[-1] is True
    view.close()
    assert active[-1] is False


def test_invalid_channel_and_denied_replacement_preserve_tiles(window, monkeypatch):
    view = window.open_multiview(channels(window)[0])
    view.add_channel(channels(window)[1])
    previous = [t.channel for t in view.tiles]
    for channel in (
        Channel("empty", "Boş", ""),
        Channel("movie", "Film", "https://example.test/movie", kind="movie"),
    ):
        assert not view.add_channel(channel)
    locked = channels(window)[2]
    window.model.set_locked({locked.id})
    monkeypatch.setattr(window, "guard", lambda reason: False)
    assert window.open_multiview(locked) is None
    assert window.multiview is view
    assert [t.channel for t in view.tiles] == previous


def test_pin_can_delete_window_before_returning(window, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    channel = channels(window)[0]
    window.model.set_locked({channel.id})

    def close_during_pin(reason):
        window.close_multiview()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        return True

    monkeypatch.setattr(window, "guard", close_during_pin)
    assert window.open_multiview(channel) is None
    assert window.multiview is None


def test_focus_waits_for_mute_completion_and_ignores_old_routes(window, monkeypatch, qt_app):
    from concurrent.futures import Future

    view = window.open_multiview(channels(window)[0])
    view.add_channel(channels(window)[1])
    pending = []
    unmuted = []

    def delayed_set(index, name, value):
        future = Future()
        if name == "mute" and value:
            pending.append(future)
        else:
            unmuted.append(index)
            future.set_result(None)
        return future

    for index, tile in enumerate(view.tiles[:2]):
        monkeypatch.setattr(tile.player, "set_property", lambda n, v, i=index: delayed_set(i, n, v))
    view.focus_tile(0)
    first_route = pending[:]
    assert not unmuted
    view.focus_tile(1)
    for future in first_route:
        future.set_result(None)
    qt_app.processEvents()
    assert not unmuted
    for future in pending[2:]:
        future.set_result(None)
    qt_app.processEvents()
    assert unmuted == [1]
    view.focus_tile(0)
    view.close()
    for future in pending[4:]:
        future.set_result(None)
    qt_app.processEvents()
    assert unmuted == [1]


def test_reused_player_ignores_queued_old_playback_events(window, qt_app):
    view = window.open_multiview(channels(window)[0])
    view.add_channel(channels(window)[1])
    tile = view.tiles[1]
    player = tile.player
    # Emit from a worker, so Qt queues the events until after replacement.
    import threading

    worker = threading.Thread(
        target=lambda: (
            player.file_loaded.emit(),
            player.error.emit("old stream error"),
            player.ended.emit(),
        )
    )
    worker.start()
    worker.join()
    view.add_channel(channels(window)[2])
    qt_app.processEvents()
    assert tile.player is player
    assert not tile.running
    assert tile.programme_label.text() == "Program bilgisi yok"


def test_kids_profile_cannot_open_multiview_and_limits_close_it(qt_app, tmp_path, monkeypatch):
    from luna_iptv.window import MainWindow

    window = MainWindow(Store(tmp_path / "kids.sqlite3"))
    try:
        monkeypatch.setattr(window, "kids_profile", lambda: True)
        assert window.open_multiview() is None and window.multiview is None
        assert "çocuk" in window.message.text()
        closed = []
        monkeypatch.setattr(window, "close_multiview", lambda: closed.append(True))
        window.show_kids_limit("bedtime")
        assert closed == [True]
    finally:
        window.close()
