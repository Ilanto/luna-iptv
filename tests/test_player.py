"""Real native-render integration; run with a working Wayland/X11 display."""

import os
import shutil
import subprocess
import time

import pytest


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
