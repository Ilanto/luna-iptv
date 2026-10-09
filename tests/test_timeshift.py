"""Live transport uses only bounded, observed cache data."""

from concurrent.futures import Future

import pytest
from test_guide_view import window as window
from test_player import event_player as event_player

from luna_iptv.timeshift import cache_minutes, cached_ranges, live_cache_options
from luna_iptv.transport import TransportController


class Player:
    def __init__(self):
        self.commands = []
        self.properties = []

    def command(self, command):
        self.commands.append(command)

    def set_property(self, name, value):
        self.properties.append((name, value))
        result = Future()
        result.set_result(None)
        return result


def ready(qt_app, *, enabled=True):
    player = Player()
    transport = TransportController(player)
    transport.prepare(True, backbuffer=enabled)
    transport.loaded()
    transport.observe("demuxer-cache-state", {"seekable-ranges": [{"start": 100, "end": 200}]})
    transport.observe("time-pos", 150)
    return transport, player


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_cache_options_and_pause_budget(minutes):
    options = live_cache_options(minutes)
    assert options["cache"] == options["demuxer-seekable-cache"] == "yes"
    assert int(options["demuxer-max-back-bytes"]) == minutes * 60 * 500_000
    assert int(options["demuxer-max-bytes"]) == minutes * 60 * 500_000
    assert options["cache-secs"] == str(minutes * 60)
    assert options["cache-on-disk"] == "no"


def test_cache_off_and_damaged_setting_defaults():
    assert live_cache_options(0) == {}
    assert cache_minutes(None) == cache_minutes("wrong") == cache_minutes(True) == 30


def test_player_attaches_options_only_to_live_load(event_player):
    player, backend = event_player
    player.load("https://example.test/live", live_cache_minutes=30)
    options = backend.commands[-1][0][-1]
    assert "demuxer-max-back-bytes=%9%900000000" in options
    assert "cache=%3%yes" in options
    player.load("file:///tmp/movie.ts")
    assert "demuxer-max-back-bytes" not in backend.commands[-1][0][-1]
    player.load("https://example.test/live", live_cache_minutes=0)
    assert "cache=" not in backend.commands[-1][0][-1]


def test_cache_options_survive_render_wait(event_player):
    player, backend = event_player
    player._render_ready = False
    player.load("https://example.test/live", live_cache_minutes=15)
    player._ready()
    assert "450000000" in backend.commands[-1][0][-1]


def test_live_skips_clamp_and_live_edge_unpauses(qt_app):
    transport, player = ready(qt_app)
    assert transport.can_seek and transport.can_scan and transport.behind_live
    transport.observe("seekable", False)
    assert transport.can_seek
    assert transport.seek_relative(-1000)
    assert player.commands[-1] == ["seek", 100, "absolute+exact"]
    transport.seek_relative(1000)
    assert player.commands[-1] == ["seek", 200, "absolute+exact"]
    transport.observe("pause", True)
    assert transport.jump_live()
    assert player.commands[-1] == ["seek", 200, "absolute+exact"]
    assert player.properties[-1] == ("pause", False)
    transport.observe("time-pos", 199)
    assert not transport.behind_live
    transport.close()


def test_invalid_ranges_gaps_and_channel_switch(qt_app):
    state = {
        "seekable-ranges": [
            {"start": 30, "end": 40},
            {"start": 0, "end": 10},
            {"start": 5, "end": 20},
            {"start": 5, "end": float("nan")},
            {},
            None,
            {"start": 9, "end": 4},
        ]
    }
    assert cached_ranges(state) == ((0, 20), (30, 40))
    transport, player = ready(qt_app)
    transport.observe("demuxer-cache-state", state)
    transport.seek_absolute(24)
    assert player.commands[-1][1] == 20
    assert transport.live_window == (30, 40)
    transport.observe("demuxer-cache-state", None)
    assert not transport.can_seek
    transport.prepare(True, backbuffer=True)
    transport.loaded()
    assert transport.live_window is None
    assert not transport.jump_live()
    transport.close()


def test_off_retains_legacy_transport_behavior(qt_app):
    transport, player = ready(qt_app, enabled=False)
    assert not transport.can_seek and not transport.can_scan
    assert not transport.seek_relative(-10)
    assert not transport.jump_live()
    # Legacy live inputs which mpv itself says are seekable still allow skips.
    transport.observe("seekable", True)
    assert transport.seek_relative(-5)
    assert player.commands[-1] == ["seek", -5, "relative+exact"]
    assert not transport.can_scan
    transport.close()


def test_scan_stops_at_buffer_boundary(qt_app, monkeypatch):
    transport, player = ready(qt_app)
    monkeypatch.setattr("luna_iptv.transport.time.monotonic", lambda: 100)
    transport.cycle(-1)
    monkeypatch.setattr("luna_iptv.transport.time.monotonic", lambda: 200)
    transport._scan_tick()
    assert player.commands[-1] == ["seek", 100, "absolute+keyframes"]
    assert transport.rate == 0
    transport.close()


def test_window_live_bar_buttons_and_setting_reload(window, monkeypatch):
    channel = window.store.channels()[0]
    loads, commands = [], []
    monkeypatch.setattr(window.player, "load", lambda *args, **kw: loads.append(kw))
    monkeypatch.setattr(window.player, "command", lambda command: commands.append(command))
    window.play(channel)
    assert loads[-1]["live_cache_minutes"] == 30
    window._mark_loaded()
    window.player_property("time-pos", 150)
    window.player_property("demuxer-cache-state", {"seekable-ranges": [{"start": 100, "end": 200}]})
    assert window.seek.isEnabled() and window.seek.value() == 500
    assert "CANLI" in window.time_label.text()
    assert not window.live_edge_button.isHidden()
    window.seek_back_button.click()
    assert commands[-1] == ["seek", 140, "absolute+exact"]
    window.seek.setValue(250)
    window.seek_to_slider()
    assert commands[-1] == ["seek", 125, "absolute+exact"]
    window.live_edge_button.click()
    assert commands[-2:] == [["seek", 200, "absolute+exact"], ["set", "pause", "no"]]
    window.store.set_setting("timeshift_minutes", 0)
    window._reload_live_cache()
    assert loads[-1]["live_cache_minutes"] == 0
    assert not window.transport.timeshift_enabled
    assert window.live_edge_button.isHidden()
    assert not window.seek.isEnabled()
