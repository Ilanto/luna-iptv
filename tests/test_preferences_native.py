"""Real mpv track selection across files, plus the explicit resume UI."""

import shutil
import subprocess
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.theme import apply_theme
from luna_iptv.window import MainWindow


def wait(app, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Native preference state timed out"


def test_native_track_preferences_changed_ids_off_and_resume(tmp_path, qt_app):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed for local multi-track fixture")
    subtitle = tmp_path / "fixture.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:19,000\nLuna local subtitle\n")
    for name, languages in [("first", ("eng", "tur")), ("second", ("tur", "eng"))]:
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
                "sine=frequency=220:sample_rate=48000",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:sample_rate=48000",
                "-i",
                str(subtitle),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:a",
                "-map",
                "3:s",
                "-t",
                "20",
                "-c:v",
                "mpeg2video",
                "-c:a",
                "pcm_s16le",
                "-c:s",
                "srt",
                "-metadata:s:a:0",
                "language=" + languages[0],
                "-metadata:s:a:1",
                "language=" + languages[1],
                "-metadata:s:s:0",
                "language=tur",
                str(tmp_path / (name + ".mkv")),
            ],
            check=True,
        )
    store = Store(tmp_path / "library.sqlite3")
    source = store.save_source({"type": "m3u", "name": "Local tracks"})
    store.replace_channels(
        source,
        [
            Channel(name, name, str(tmp_path / (name + ".mkv")), kind="movie")
            for name in ("first", "second")
        ],
    )
    apply_theme(qt_app)
    window = MainWindow(store)
    failures = []
    window.player.error.connect(failures.append)
    window.show()
    first, second = window.model.channels
    try:
        window.play(first)
        window.player.set_property("mute", True)
        wait(qt_app, lambda: not window._loading and len(window._tracks) == 4)
        tur = next(t for t in window._tracks if t.get("type") == "audio" and t.get("lang") == "tur")
        assert tur["id"] == 2
        window.track_preferences.select("audio", tur)
        window.track_preferences.select("sub", None)
        window.play(second)
        wait(
            qt_app,
            lambda: (
                not window._loading
                and any(
                    t.get("type") == "audio"
                    and t.get("lang") == "tur"
                    and t.get("id") == 1
                    and t.get("selected")
                    for t in window._tracks
                )
            ),
        )
        assert not any(t.get("selected") for t in window._tracks if t.get("type") == "sub")
        assert store.playback_preferences(source)["audio"]["lang"] == "tr"
        backend, context = window.player._mpv, window.video.context()
        window.stop_playback()
        store.save_progress(first.id, 8, 20)
        window.request_play(first)
        assert window._resume_dialog is not None
        QTest.mouseClick(window._resume_dialog.resume_button, Qt.LeftButton)
        wait(qt_app, lambda: not window._loading and 7.8 <= window._position < 12)
        window.restart_current()
        wait(qt_app, lambda: not window._loading and window._position < 3)
        assert window.player._mpv is backend and window.video.context() is context
        assert not failures
    finally:
        window.close()
        if window.player._termination:
            window.player._termination.join(timeout=20)
        qt_app.processEvents()
