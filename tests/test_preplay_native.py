"""First native mpv selection must honor preferences without a second stream."""

import os
import shutil
import subprocess
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def test_native_first_tracks_single_connection_and_per_file_reset(tmp_path, qt_app):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.skip("Native render integration requires a desktop display")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed for generated multitrack fixture")
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from shiboken6 import isValid

    from luna_iptv.player import Player, VideoWidget
    from luna_iptv.preferences import TrackPreferences
    from luna_iptv.storage import Store

    english = tmp_path / "english.srt"
    turkish = tmp_path / "turkish.srt"
    english.write_text("1\n00:00:00,000 --> 00:00:11,000\nEnglish subtitle\n", encoding="utf-8")
    turkish.write_text("1\n00:00:00,000 --> 00:00:11,000\nTürkçe altyazı\n", encoding="utf-8")
    media = tmp_path / "languages.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-i",
            str(english),
            "-i",
            str(turkish),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-map",
            "3:s",
            "-map",
            "4:s",
            "-t",
            "12",
            "-c:v",
            "mpeg4",
            "-c:a",
            "pcm_s16le",
            "-c:s",
            "srt",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:1",
            "language=tur",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:1",
            "language=tur",
            "-disposition:a:0",
            "default",
            "-disposition:a:1",
            "0",
            "-disposition:s:0",
            "default+forced",
            "-disposition:s:1",
            "0",
            "-live",
            "1",
            str(media),
        ],
        check=True,
    )
    payload = media.read_bytes()
    requests = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests[self.path] += 1
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            requests["HEAD"] += 1
            self.send_error(405)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = Store(tmp_path / "preferences.sqlite3")
    source = store.save_source({"name": "Synthetic", "type": "m3u"})
    player = Player()
    preferences = TrackPreferences(store, player)
    widget = VideoWidget(player)
    widget.setAttribute(Qt.WA_DeleteOnClose)
    widget.resize(480, 270)
    tracks, first, failures, positions = {}, {}, [], {}

    def property_changed(token, name, value):
        if name == "track-list":
            tracks[token] = value
        elif name == "time-pos":
            positions[token] = value

    def loaded(token):
        # Capture the authoritative snapshot at the first loaded callback. Do
        # not apply preferences after loading: that would hide initial errors.
        first[token] = {
            track["type"]: track.get("lang")
            for track in tracks.get(token, [])
            if track.get("selected") and track.get("type") in ("audio", "sub")
        }

    player.playback_property_changed.connect(property_changed)
    player.playback_loaded.connect(loaded)
    player.error.connect(failures.append)
    widget.show()

    def wait_for(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qt_app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        assert predicate(), f"first={first}; positions={positions}; errors={failures}"

    cases = [
        ("turkish", "tur", "tur", {"audio": "tur", "sub": "tur"}),
        ("english", "eng", "eng", {"audio": "eng", "sub": "eng"}),
        ("mixed", "eng", "tur", {"audio": "eng", "sub": "tur"}),
        ("missing", "deu", "deu", {"audio": "eng"}),
        ("off", "tur", "off", {"audio": "tur"}),
        ("automatic", "auto", "auto", None),
    ]
    render_context = None
    try:
        for name, audio, sub, expected in cases:

            def choice(language):
                return (
                    {"mode": language}
                    if language in ("off", "auto")
                    else {"mode": "track", "lang": language}
                )

            options = preferences.begin(
                source,
                {"audio": choice(audio), "sub": choice(sub), "remember": False},
            )
            path = f"/{name}.mkv"
            before = sum(requests.values())
            token = player.load(
                f"http://127.0.0.1:{server.server_port}{path}", track_options=options
            )
            wait_for(
                lambda current=token: current in first and (positions.get(current) or 0) > 0.15
            )
            if expected is None:
                assert first[token]["audio"] == "eng"
            else:
                assert first[token] == expected
            assert requests[path] == 1
            assert sum(requests.values()) == before + 1, "Extra probe or stream connection"
            assert not failures
            if render_context is None:
                render_context = widget._render
                assert render_context is not None
            else:
                assert widget._render is render_context
            assert not widget.grabFramebuffer().isNull()
            if name == "off":
                # A manual numeric selection in this file must not leak into
                # the following automatic load.
                player.set_property("aid", 2)
                player.set_property("sid", 2)
    finally:
        player.shutdown()
        widget.close()
        qt_app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(widget)
        if player._termination:
            player._termination.join(timeout=20)
        store.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
