"""Real native-render integration; run with a working Wayland/X11 display."""

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest


class EventBackend:
    def __init__(self):
        self.events = {}
        self.observer = None
        self.commands = []

    def observe_property(self, _name, callback):
        self.observer = callback

    def event_callback(self, name):
        return lambda callback: self.events.__setitem__(name, callback) or callback

    def command_async(self, *args):
        future = Future()
        self.commands.append((args, future))
        if args[0] != "loadfile":
            future.set_result(None)
        return future

    def terminate(self):
        pass


@pytest.fixture
def event_player(monkeypatch):
    from luna_iptv.player import Player

    backend = EventBackend()
    monkeypatch.setitem(sys.modules, "mpv", SimpleNamespace(MPV=lambda **_kwargs: backend))
    player = Player()
    player._render_ready = True
    try:
        yield player, backend
    finally:
        player.shutdown()
        if player._termination:
            player._termination.join(timeout=2)


def mpv_event(**data):
    return SimpleNamespace(data=SimpleNamespace(**data))


def test_pending_render_load_keeps_the_token_returned_to_caller(event_player):
    player, backend = event_player
    player._render_ready = False

    token = player.load("https://example.test/live")
    player._ready()

    load_args, _future = next(item for item in backend.commands if item[0][0] == "loadfile")
    assert token == 1
    assert load_args[1] == "https://example.test/live"
    assert player._pending_load is None


def test_tagged_events_wait_for_loadfile_playlist_id_result(event_player):
    player, backend = event_player
    loaded = []
    properties = []
    player.playback_loaded.connect(loaded.append)
    player.playback_property_changed.connect(
        lambda token, name, value: properties.append((token, name, value))
    )

    token = player.load("https://example.test/live")
    _args, load_future = next(item for item in backend.commands if item[0][0] == "loadfile")
    backend.events["start-file"](mpv_event(playlist_entry_id=41))
    backend.observer("time-pos", 2.5)
    backend.events["file-loaded"](mpv_event())
    assert loaded == []
    assert properties == []

    load_future.set_result({"playlist_entry_id": 41})
    assert loaded == [token]
    assert properties == [(token, "time-pos", 2.5)]


def test_old_entry_failure_is_tagged_but_not_emitted_as_current_legacy_error(event_player):
    player, backend = event_player
    tagged = []
    legacy_errors = []
    legacy_ended = []
    player.playback_finished.connect(
        lambda token, reason, message: tagged.append((token, reason, message))
    )
    player.error.connect(legacy_errors.append)
    player.ended.connect(lambda: legacy_ended.append(True))

    first = player.load("https://example.test/one")
    first_future = [item for item in backend.commands if item[0][0] == "loadfile"][-1][1]
    backend.events["start-file"](mpv_event(playlist_entry_id=41))
    first_future.set_result({"playlist_entry_id": 41})

    second = player.load("https://example.test/two")
    second_future = [item for item in backend.commands if item[0][0] == "loadfile"][-1][1]
    second_future.set_result({"playlist_entry_id": 42})
    backend.events["start-file"](mpv_event(playlist_entry_id=42))

    backend.events["end-file"](mpv_event(playlist_entry_id=41, reason=4, error=-13))
    assert tagged == [(first, "error", "Yayın açılamadı veya bağlantı kesildi.")]
    assert legacy_errors == []
    assert legacy_ended == []

    backend.events["end-file"](mpv_event(playlist_entry_id=42, reason=0, error=0))
    assert tagged[-1] == (second, "eof", "")
    assert legacy_ended == [True]


def test_missing_playlist_id_disables_tracking_without_stalling_legacy_playback(event_player):
    player, backend = event_player
    lost = []
    loaded = []
    tagged = []
    player.playback_tracking_lost.connect(lost.append)
    player.file_loaded.connect(lambda: loaded.append(True))
    player.playback_loaded.connect(tagged.append)

    token = player.load("https://example.test/live")
    load_future = [item for item in backend.commands if item[0][0] == "loadfile"][-1][1]
    backend.events["start-file"](mpv_event(playlist_entry_id=41))
    backend.events["file-loaded"](mpv_event())
    load_future.set_result({})

    assert lost == [token]
    assert loaded == [True]
    assert tagged == []


def test_missing_playlist_id_does_not_treat_previous_entry_end_as_current(event_player):
    player, backend = event_player
    legacy_ended = []
    player.ended.connect(lambda: legacy_ended.append(True))

    player.load("https://example.test/one")
    first_future = [item for item in backend.commands if item[0][0] == "loadfile"][-1][1]
    backend.events["start-file"](mpv_event(playlist_entry_id=41))
    first_future.set_result({})

    player.load("https://example.test/two")
    second_future = [item for item in backend.commands if item[0][0] == "loadfile"][-1][1]
    second_future.set_result({})
    backend.events["end-file"](mpv_event(playlist_entry_id=41, reason=4, error=-13))
    assert legacy_ended == []

    backend.events["start-file"](mpv_event(playlist_entry_id=42))
    backend.events["end-file"](mpv_event(playlist_entry_id=42, reason=0, error=0))
    assert legacy_ended == [True]


def test_loadfile_command_failure_is_reported_as_tagged_playback_failure(event_player):
    player, backend = event_player
    finished = []
    player.playback_finished.connect(
        lambda token, reason, message: finished.append((token, reason, message))
    )

    token = player.load("https://example.test/live")
    load_future = [item for item in backend.commands if item[0][0] == "loadfile"][-1][1]
    load_future.set_exception(RuntimeError("private URL must not escape"))

    assert finished == [(token, "error", "Yayın açılamadı.")]


def test_set_property_returns_async_command_completion_future():
    from PySide6.QtCore import QObject

    from luna_iptv.player import Player

    class Future:
        def __init__(self):
            self.callbacks = []

        def add_done_callback(self, callback):
            self.callbacks.append(callback)

    class Backend:
        def __init__(self):
            self.args = None
            self.future = Future()

        def command_async(self, *args):
            self.args = args
            return self.future

    player = Player.__new__(Player)
    QObject.__init__(player)
    player._closed = False
    player._mpv = Backend()

    result = player.set_property("pause", True)

    assert result is player._mpv.future
    assert player._mpv.args == ("set", "pause", "yes")


def test_native_render_playback_controls_and_teardown(tmp_path, qt_app):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.skip("Native render integration requires a desktop display")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed for generated test fixture")
    from luna_iptv.player import Player, VideoWidget

    media = tmp_path / "fixture.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "12",
            "-c:v",
            "mpeg4",
            "-c:a",
            "pcm_s16le",
            str(media),
        ],
        check=True,
    )
    app = qt_app
    player = Player()
    widget = VideoWidget(player)
    widget.resize(640, 360)
    values, failures, loaded, ended = {}, [], [], []
    player.property_changed.connect(lambda name, value: values.update({name: value}))
    player.error.connect(failures.append)
    player.file_loaded.connect(lambda: loaded.append(True))
    player.ended.connect(lambda: ended.append(True))
    widget.show()

    def wait_for(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        assert predicate(), f"timed out; values={values}; errors={failures}"

    try:
        player.load(str(media))  # Queued until the render context is ready.
        wait_for(lambda: loaded and (values.get("time-pos") or 0) > 0.3)
        player.set_property("mute", True)
        wait_for(lambda: values.get("mute") is True)
        player.pause_toggle()
        wait_for(lambda: values.get("pause") is True)
        position = values["time-pos"]
        until = time.monotonic() + 0.2
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(0.01)
        assert abs(values["time-pos"] - position) < 0.1
        player.command(["seek", 4, "absolute+exact"])
        wait_for(lambda: abs((values.get("time-pos") or 0) - 4) < 0.2)
        tracks = values.get("track-list") or []
        assert {t["type"] for t in tracks} >= {"video", "audio"}
        shot = widget.grabFramebuffer()
        assert not shot.isNull()
        colors = {
            shot.pixelColor(x, y).name()
            for x in range(40, shot.width(), 50)
            for y in range(40, shot.height(), 50)
        }
        assert len(colors) > 8, "rendered frame is blank or monochrome"
        player.stop()
        wait_for(lambda: values.get("idle-active") is True and ended)
        assert not failures
        player.load(str(tmp_path / "missing-private-token-file.mkv"))
        wait_for(lambda: failures)
        assert all("private-token" not in message for message in failures)
    finally:
        player.shutdown()
        player.shutdown()  # Idempotent window/application teardown.
        widget.close()
        app.processEvents()
