from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel
from shiboken6 import isValid

from luna_iptv.models import Channel, Playlist
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


class ImmediateLoadBackend:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.events = {}

    def observe_property(self, _name, _callback) -> None:
        pass

    def event_callback(self, name):
        return lambda callback: self.events.__setitem__(name, callback) or callback

    def command_async(self, *args):
        future = Future()
        if args[0] == "loadfile" and self.error is not None:
            future.set_exception(self.error)
        else:
            future.set_result(self.result if args[0] == "loadfile" else None)
        return future

    def terminate(self) -> None:
        pass


def make_window(qt_app, tmp_path, monkeypatch, *, kind="live", stub_player=True):
    store = Store(tmp_path / "library.sqlite3")
    source_id = store.save_source(
        {"name": "Yerel", "type": "m3u", "location": "https://example.test/list"}
    )
    store.replace_channels(
        source_id,
        [Channel("item", "Yerel test", "https://example.test/stream", kind=kind)],
    )
    window = MainWindow(store)
    channel = store.channels(source_id)[0]
    tokens = iter(range(10, 30))
    loads = []
    reserved = []

    def reserve_load():
        token = next(tokens)
        reserved.append(token)
        return token

    def load(*args, **kwargs):
        token = reserved.pop()
        loads.append((token, args, kwargs))
        return token

    if stub_player:
        monkeypatch.setattr(window.player, "reserve_load", reserve_load)
        monkeypatch.setattr(window.player, "load", load)
        monkeypatch.setattr(window.player, "stop", lambda: None)
    return window, channel, loads


def fire_recovery_timer(window):
    window.recovery._timer.stop()
    window.recovery._timer.timeout.emit()


def make_immediate_backend_window(qt_app, tmp_path, monkeypatch, backend):
    monkeypatch.setitem(sys.modules, "mpv", SimpleNamespace(MPV=lambda **_kwargs: backend))
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch, stub_player=False)
    window.player._render_ready = True
    return window, channel, loads


def test_synchronous_missing_load_id_is_registered_before_tracking_lost(
    qt_app, tmp_path, monkeypatch
) -> None:
    backend = ImmediateLoadBackend(result={})
    window, channel, _loads = make_immediate_backend_window(qt_app, tmp_path, monkeypatch, backend)
    try:
        window.play(channel)

        assert window._playback_token == 1
        assert window._untracked_playback_token == 1
        assert window.recovery.state == "idle"
        assert window.recovery._timer.isActive() is False
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_synchronous_load_failure_is_registered_before_retry_is_scheduled(
    qt_app, tmp_path, monkeypatch
) -> None:
    backend = ImmediateLoadBackend(error=RuntimeError("fixture failure"))
    window, channel, _loads = make_immediate_backend_window(qt_app, tmp_path, monkeypatch, backend)
    try:
        window.play(channel)

        assert window._playback_token == 1
        assert window.recovery.state == "waiting"
        assert window.recovery.attempt == 1
        assert window.recovery._timer.interval() == 1000
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_live_failure_retries_same_channel_and_ignores_old_end(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        assert loads[0][0] == window.recovery.current_token == 10
        assert not window.recovery_cancel_button.isHidden()
        assert "bağlanılıyor" in window.message.text().lower()

        window.playback_finished(10, "error", "Yayın açılamadı.")
        assert window.recovery.state == "waiting"
        assert "1 sn sonra" in window.message.text()
        fire_recovery_timer(window)

        assert [item[0] for item in loads] == [10, 11]
        assert window.recovery.current_token == 11
        window.playback_finished(10, "eof", "")
        assert window.recovery.current_token == 11
        assert window.recovery.state == "connecting"
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_stop_and_cancel_button_prevent_delayed_reload(qt_app, tmp_path, monkeypatch) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_finished(10, "error", "Yayın açılamadı.")
        window.recovery_cancel_button.click()
        fire_recovery_timer(window)

        assert len(loads) == 1
        assert window.recovery.state == "idle"
        assert window.message.text() == "Yayın durduruldu."
        assert window.recovery_cancel_button.isHidden()
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_generic_command_error_does_not_end_or_retry_current_playback(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_error("Oynatıcı komutu uygulanamadı.")

        assert window.current is channel
        assert window.recovery.current_token == 10
        assert window.recovery.state == "connecting"
        assert len(loads) == 1
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_tagged_progress_buffer_and_loaded_events_drive_visible_recovery_state(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, _loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_loaded(10)
        assert window.recovery.state == "playing"
        assert window.message.text() == "Yayın oynatılıyor."

        window.playback_property(10, "paused-for-cache", True)
        assert window.recovery.state == "buffering"
        assert "arabelleğe" in window.message.text().lower()
        window.playback_property(10, "paused-for-cache", False)
        assert window.recovery.state == "playing"

        window.playback_property(9, "paused-for-cache", True)
        assert window.recovery.state == "playing"
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_vod_eof_ends_without_automatic_retry(qt_app, tmp_path, monkeypatch) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch, kind="movie")
    try:
        window.play(channel)
        window.playback_loaded(10)
        window.playback_finished(10, "eof", "")
        fire_recovery_timer(window)

        assert len(loads) == 1
        assert window.recovery.state == "idle"
        assert window._idle is True
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_final_connect_watchdog_failure_allows_visible_manual_retry(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        for expected_loads in (2, 3, 4):
            fire_recovery_timer(window)  # connect watchdog
            fire_recovery_timer(window)  # retry delay
            assert len(loads) == expected_loads
        fire_recovery_timer(window)  # final connect watchdog

        assert window.recovery.state == "failed"
        assert window._loading is False
        assert not window.retry_button.isHidden()
        window.retry_button.click()
        assert len(loads) == 5
        assert window.recovery.attempt == 0
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_untracked_current_failure_before_loaded_does_not_leave_loading_state(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, _loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_tracking_lost(10)
        window.ended()

        assert window._loading is False
        assert window._idle is True
        assert window.recovery.state == "idle"
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_status_is_mirrored_when_compact_player_status_exists(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, _channel, _loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.mini_status_label = QLabel(window)
        window.status("Canlı yayın arabelleğe alınıyor…")

        assert window.mini_status_label.text() == "Canlı yayın arabelleğe alınıyor…"
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_current_playback_still_cleans_up_after_source_refresh_cancels_recovery(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_loaded(10)
        source = window.source_for(channel)
        window.accept_import(
            source,
            Playlist(
                [
                    Channel(
                        channel.id,
                        channel.name,
                        channel.url,
                        kind=channel.kind,
                    )
                ],
                [],
                [],
            ),
        )
        assert window.recovery.state == "idle"
        assert window._idle is False

        window.playback_finished(10, "error", "Yayın açılamadı.")

        assert len(loads) == 1
        assert window._idle is True
        assert window._loading is False
        assert window.info_button.isEnabled() is False
        assert window.message.text() == "Yayın açılamadı."
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_native_live_recovery_reuses_backend_after_local_connection_failure(
    qt_app, tmp_path
) -> None:
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or not (
        os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
    ):
        pytest.skip("Native recovery integration requires a desktop display")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed for generated recovery fixture")

    media = tmp_path / "live.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-t",
            "6",
            "-c:v",
            "mpeg4",
            "-f",
            "mpegts",
            str(media),
        ],
        check=True,
    )
    payload = media.read_bytes()

    class FlakyHandler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            if type(self).requests == 1:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except BrokenPipeError:
                pass

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    store = Store(tmp_path / "native-library.sqlite3")
    source_id = store.save_source(
        {
            "name": "Yerel",
            "type": "m3u",
            "location": "https://example.test/list",
        }
    )
    store.replace_channels(
        source_id,
        [
            Channel(
                "live",
                "Yerel kesintili yayın",
                f"http://127.0.0.1:{server.server_port}/live.ts",
                kind="live",
            )
        ],
    )
    window = MainWindow(store)
    channel = store.channels(source_id)[0]
    backend = window.player._mpv
    errors = []
    window.player.error.connect(errors.append)
    window.show()

    def wait_for(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qt_app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        assert predicate(), (
            f"timed out; requests={FlakyHandler.requests}; "
            f"state={window.recovery.state}; errors={errors}"
        )

    try:
        window.play(channel)
        wait_for(lambda: FlakyHandler.requests >= 2 and window.recovery.state == "playing")

        assert window.player._mpv is backend
        assert window.recovery.attempt == 1
        assert window._loading is False
        assert window._idle is False
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
