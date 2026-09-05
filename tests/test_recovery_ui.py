from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import replace
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
        self.commands = []

    def observe_property(self, _name, _callback) -> None:
        pass

    def event_callback(self, name):
        return lambda callback: self.events.__setitem__(name, callback) or callback

    def command_async(self, *args):
        self.commands.append(args)
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


def make_immediate_backend_window(qt_app, tmp_path, monkeypatch, backend, *, kind="live"):
    monkeypatch.setitem(sys.modules, "mpv", SimpleNamespace(MPV=lambda **_kwargs: backend))
    window, channel, loads = make_window(
        qt_app,
        tmp_path,
        monkeypatch,
        kind=kind,
        stub_player=False,
    )
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
        assert window.recovery.state == "untracked-connecting"
        assert window.recovery._timer.interval() == 12_000
        fire_recovery_timer(window)
        assert window.recovery.state == "untracked-failed"
        assert window._playback_active is False
        assert window._loading is False
        assert window._idle is True
        assert window.message.text() == "Yayın başlatılamadı."
        assert [args[0] for args in backend.commands].count("loadfile") == 1
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_untracked_vod_timeout_cleans_up_and_manual_retry_preserves_progress(
    qt_app, tmp_path, monkeypatch
) -> None:
    backend = ImmediateLoadBackend(result={})
    window, channel, _loads = make_immediate_backend_window(
        qt_app,
        tmp_path,
        monkeypatch,
        backend,
        kind="movie",
    )
    window.store.save_progress(channel.id, 19, 90)
    try:
        window.play(channel)
        fire_recovery_timer(window)

        assert window.recovery.state == "untracked-failed"
        assert window._playback_active is False
        assert window._loading is False
        assert window._idle is True
        assert [args[0] for args in backend.commands].count("stop") == 1
        assert not window.retry_button.isHidden()

        window.retry_button.click()

        load_commands = [args for args in backend.commands if args[0] == "loadfile"]
        assert len(load_commands) == 2
        assert "start=19.0" in load_commands[-1][4]
        assert window.store.progress(channel.id) == (19.0, 90.0)
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_missing_player_backend_is_an_immediate_terminal_load_failure(
    qt_app, tmp_path, monkeypatch
) -> None:
    def fail_backend(**_kwargs):
        raise OSError("fixture backend unavailable")

    monkeypatch.setitem(sys.modules, "mpv", SimpleNamespace(MPV=fail_backend))
    window, channel, _loads = make_window(qt_app, tmp_path, monkeypatch, stub_player=False)
    try:
        window.play(channel)

        assert window._playback_token == 1
        assert window._playback_active is False
        assert window._loading is False
        assert window._idle is True
        assert window.recovery.state == "idle"
        assert window.recovery._timer.isActive() is False
        assert "motoru" in window.message.text().lower()
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


@pytest.mark.parametrize(
    ("changes", "expected_message"),
    [
        ({"url": "https://example.test/a\x00b"}, "Geçerli bir yayın adresi seçin."),
        ({"headers": {"X-Test": "bad\nvalue"}}, "Yayın HTTP başlıkları geçersiz."),
    ],
)
def test_invalid_vod_request_is_an_immediate_terminal_failure(
    qt_app,
    tmp_path,
    monkeypatch,
    changes,
    expected_message,
) -> None:
    backend = ImmediateLoadBackend(result={"playlist_entry_id": 41})
    window, channel, _loads = make_immediate_backend_window(
        qt_app,
        tmp_path,
        monkeypatch,
        backend,
        kind="movie",
    )
    channel = replace(channel, **changes)
    window.store.save_progress(channel.id, 19, 90)
    try:
        window.play(channel)

        assert window._playback_active is False
        assert window._loading is False
        assert window._idle is True
        assert window.recovery.state == "idle"
        assert window.recovery._timer.isActive() is False
        assert window.store.progress(channel.id) == (19.0, 90.0)
        assert window.message.text() == expected_message
        assert window.retry_button.isHidden()
        assert [args for args in backend.commands if args[0] == "loadfile"] == []
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_legacy_loaded_event_cancels_untracked_connect_watchdog(
    qt_app, tmp_path, monkeypatch
) -> None:
    backend = ImmediateLoadBackend(result={})
    window, channel, _loads = make_immediate_backend_window(qt_app, tmp_path, monkeypatch, backend)
    try:
        window.play(channel)
        backend.events["start-file"](SimpleNamespace(data=SimpleNamespace(playlist_entry_id=41)))
        backend.events["file-loaded"](SimpleNamespace(data=None))

        assert window.recovery.state == "playing"
        assert window.recovery._timer.isActive() is False
        assert window._loading is False
        assert window._idle is False
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


def test_vod_load_failure_preserves_resume_position_for_manual_retry(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch, kind="movie")
    window.store.save_progress(channel.id, 19, 90)
    try:
        window.play(channel)
        assert loads[-1][2]["start"] == 19

        window.playback_finished(10, "error", "Yayın açılamadı.")

        assert window.store.progress(channel.id) == (19.0, 90.0)
        assert window._idle is True
        assert not window.retry_button.isHidden()
        window.retry_button.click()
        assert loads[-1][2]["start"] == 19
        assert len(loads) == 2
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_untracked_vod_end_before_loaded_preserves_resume_position(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch, kind="movie")
    window.store.save_progress(channel.id, 19, 90)
    try:
        window.play(channel)
        window.playback_tracking_lost(10)

        window.ended()

        assert window.store.progress(channel.id) == (19.0, 90.0)
        window.toggle_play()
        assert loads[-1][2]["start"] == 19
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_final_connect_watchdog_failure_allows_visible_manual_retry(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    stops = []
    monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))
    try:
        window.play(channel)
        for expected_loads in (2, 3, 4):
            fire_recovery_timer(window)  # connect watchdog
            fire_recovery_timer(window)  # retry delay
            assert len(loads) == expected_loads
        fire_recovery_timer(window)  # final connect watchdog

        assert window.recovery.state == "failed"
        assert window._loading is False
        assert window._playback_active is False
        assert stops == [True]
        assert not window.retry_button.isHidden()
        window.playback_loaded(13)
        assert window._idle is True
        assert window.info_button.isEnabled() is False
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


def test_current_playback_still_cleans_up_after_source_refresh_suppresses_retries(
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
        assert window.recovery.state == "playing"
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


def test_source_refresh_while_connecting_keeps_a_bounded_nonretrying_watchdog(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        source = window.source_for(channel)
        window.accept_import(
            source,
            Playlist([Channel(channel.id, channel.name, channel.url, kind="live")], [], []),
        )

        assert window.recovery.state == "untracked-connecting"
        assert window.recovery._timer.interval() == 12_000
        window.playback_property(10, "pause", True)
        window.playback_property(10, "paused-for-cache", True)
        window.playback_property(10, "paused-for-cache", False)
        assert window.recovery.state == "untracked-connecting"
        assert window.recovery._timer.interval() == 12_000
        fire_recovery_timer(window)

        assert len(loads) == 1
        assert window.recovery.state == "untracked-failed"
        assert window._playback_active is False
        assert window._loading is False
        assert window._idle is True
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()


def test_source_refresh_during_retry_wait_still_reaches_terminal_cleanup(
    qt_app, tmp_path, monkeypatch
) -> None:
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    stops = []
    monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))
    try:
        window.play(channel)
        fire_recovery_timer(window)
        assert window.recovery.state == "waiting"
        source = window.source_for(channel)
        window.accept_import(
            source,
            Playlist([Channel(channel.id, channel.name, channel.url, kind="live")], [], []),
        )

        assert window.recovery.state == "untracked-connecting"
        window.playback_property(10, "pause", True)
        window.playback_property(10, "paused-for-cache", True)
        assert window.recovery.state == "untracked-connecting"
        fire_recovery_timer(window)

        assert len(loads) == 1
        assert window.recovery.state == "untracked-failed"
        assert window._playback_active is False
        assert window._loading is False
        assert window._idle is True
        assert stops == [True]
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
