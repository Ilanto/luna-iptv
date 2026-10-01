"""Playback keeps the desktop awake and always hands the inhibit back."""

import pytest
from shiboken6 import isValid

from luna_iptv.idle_inhibit import IdleInhibit
from luna_iptv.models import Channel, Playlist
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


class FakeBus:
    def __init__(self, missing=()):
        self.missing = set(missing)
        self.calls = []
        self.held = set()
        self._next = 40

    def call_blocking(self, service, path, interface, method, signature, args, timeout):
        self.calls.append((interface, method, signature, args))
        if service in self.missing:
            raise RuntimeError(f"{service} is not provided")
        if method == "Inhibit":
            self._next += 1
            self.held.add((interface, self._next))
            return self._next
        self.held.discard((interface, args[0]))
        return None


def test_prefers_gnome_session_manager_and_releases_its_cookie():
    bus = FakeBus()
    inhibit = IdleInhibit(lambda: bus)
    inhibit.set_active(True)
    inhibit.set_active(True)
    assert inhibit.active
    assert bus.calls == [
        ("org.gnome.SessionManager", "Inhibit", "susu", ("luna-iptv", 0, "Luna IPTV oynatıyor", 12))
    ]
    inhibit.set_active(False)
    assert not inhibit.active
    assert bus.calls[-1] == ("org.gnome.SessionManager", "Uninhibit", "u", (41,))
    assert bus.held == set()


def test_falls_back_to_freedesktop_screensaver():
    bus = FakeBus(missing={"org.gnome.SessionManager"})
    inhibit = IdleInhibit(lambda: bus)
    inhibit.set_active(True)
    assert bus.held == {("org.freedesktop.ScreenSaver", 41)}
    inhibit.close()
    assert bus.calls[-1] == ("org.freedesktop.ScreenSaver", "UnInhibit", "u", (41,))
    assert bus.held == set()


def test_missing_dbus_is_a_quiet_no_op():
    def no_bus():
        raise ImportError("dbus")

    inhibit = IdleInhibit(no_bus)
    inhibit.set_active(True)
    assert not inhibit.active
    inhibit.close()

    bus = FakeBus(missing={"org.gnome.SessionManager", "org.freedesktop.ScreenSaver"})
    inhibit = IdleInhibit(lambda: bus)
    inhibit.set_active(True)
    assert not inhibit.active
    inhibit.set_active(False)
    assert [method for _, method, _, _ in bus.calls] == ["Inhibit", "Inhibit"]


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    store.replace_channels("home", [Channel("live", "Live", "file:///live.ts")])
    value = MainWindow(store)
    value.bus = FakeBus()
    value.idle_inhibit = IdleInhibit(lambda: value.bus)
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    monkeypatch.setattr(value.player, "set_property", lambda *a: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def live(window):
    return next(c for c in window.store.channels() if c.id == "home:live")


def test_playback_holds_inhibit_until_pause_and_stop(window):
    window.play(live(window))
    assert window.idle_inhibit.active
    token = window._playback_token
    window.playback_property(token, "pause", True)
    assert not window.idle_inhibit.active
    window.playback_property(token, "pause", False)
    assert window.idle_inhibit.active
    window.stop_playback()
    assert not window.idle_inhibit.active
    assert window.bus.held == set()


def test_stale_pause_does_not_release_new_playback(window):
    window.play(live(window))
    old_token = window._playback_token
    window.play(live(window), recovering=True)
    window.playback_property(old_token, "pause", True)
    assert window.idle_inhibit.active


def test_refresh_that_removes_playing_channel_releases_inhibit(window):
    window.play(live(window))
    other = Channel("news", "News", "file:///news.ts")
    window.accept_import({"id": "home", "type": "m3u", "name": "Home"}, Playlist([other], [], []))
    assert window.current is None
    assert not window.idle_inhibit.active


def test_close_releases_inhibit(window):
    window.play(live(window))
    window.close()
    assert window.bus.held == set()


def test_untracked_pause_also_releases_inhibit(window):
    # Without playlist entry ids the player reports pause only through the
    # general property signal; the inhibit must follow it there too.
    window.play(live(window))
    window._untracked_playback_token = window._playback_token
    window.player_property("pause", True)
    assert not window.idle_inhibit.active
    window.player_property("pause", False)
    assert window.idle_inhibit.active
