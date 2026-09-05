"""Boundaries between recovery, source edits, privacy and compact controls."""

from PySide6.QtWidgets import QDialog
from test_recovery_ui import fire_recovery_timer, make_window

from luna_iptv.models import Channel, Playlist


def test_reconnect_does_not_repopulate_cleared_history(qt_app, tmp_path, monkeypatch):
    window, channel, loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_loaded(10)
        window.clear_history(reset_progress=True)
        window.playback_finished(10, "error", "Connection interrupted")
        fire_recovery_timer(window)
        assert len(loads) == 2
        window.playback_loaded(11)
        window.player_property("time-pos", 40)
        assert window.store.recent_ids() == []
        assert window.store.progress(channel.id) == (0, 0)
        window.play(channel)
        window.playback_loaded(12)
        assert window.store.recent_ids() == [channel.id]
    finally:
        window.close()
        qt_app.processEvents()


def test_edit_keeps_stream_but_suppresses_old_connection_retry(qt_app, tmp_path, monkeypatch):
    window, channel, _loads = make_window(qt_app, tmp_path, monkeypatch)
    try:
        window.play(channel)
        window.playback_loaded(10)
        source = window.store.sources()[0]
        candidate = source | {"location": "https://example.test/new-list"}

        class Accepted:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return QDialog.Accepted

            def source(self):
                return candidate

        monkeypatch.setattr("luna_iptv.window.SourceDialog", Accepted)
        monkeypatch.setattr(
            window,
            "run_task",
            lambda _f, success, *a, **kw: success(
                Playlist(
                    [Channel(channel.id, channel.name, "https://example.test/new-stream")], [], []
                )
            ),
        )
        window.edit_source(source)
        assert window._playback_active and not window._idle
        assert window.current.url == "https://example.test/new-stream"
        window.playback_finished(10, "error", "Old connection ended")
        assert window._idle and not window._loading
        assert not window.recovery._timer.isActive()
    finally:
        window.close()
        qt_app.processEvents()


def test_mini_player_exposes_cancel_for_connection_wait(qt_app, tmp_path, monkeypatch):
    window, channel, _loads = make_window(qt_app, tmp_path, monkeypatch)
    stops = []
    monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))
    try:
        window.play(channel)
        window.mini_player.enter()
        assert not window.mini_status_row.isHidden()
        assert not window.mini_cancel_button.isHidden()
        assert window.mini_cancel_button.parent() is window.mini_status_row
        window.mini_cancel_button.click()
        assert stops == [True]
        assert window._idle and not window._loading
        assert not window.recovery._timer.isActive()
        assert window.mini_cancel_button.isHidden()
    finally:
        window.close()
        qt_app.processEvents()
