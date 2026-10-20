"""Favourite-series checks, persistent unread badges and guarded desktop actions."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import RecordingNotifications
from PySide6.QtCore import Qt

from luna_iptv.library import NEW_EPISODES_ROLE, ChannelModel
from luna_iptv.media_details import MediaDetails
from luna_iptv.models import Channel
from luna_iptv.new_episodes import NewEpisodeService
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def episode(identity, series="1"):
    return Channel(
        identity,
        f"Bölüm {identity}",
        f"file:///{identity}.mp4",
        kind="movie",
        series_id=series,
        provider_key=f"episode:{series}:{identity}",
    )


@pytest.fixture
def state(tmp_path, qt_app):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source(
        {
            "id": "a",
            "name": "A",
            "type": "xtream",
            "location": "https://fixture.invalid",
            "username": "test",
            "password": "test",
        }
    )
    series = store.replace_channels(
        "a",
        [
            Channel("s1", "Dark", "", kind="series", series_id="1"),
            Channel("s2", "Other", "", kind="series", series_id="2"),
            Channel("live", "Live", "file:///live.ts"),
        ],
    )
    store.set_favorite(series[0].id, True)
    store.set_favorite(series[2].id, True)
    store.save_source({"id": "b", "name": "B", "type": "m3u"})
    other = store.replace_channels("b", [Channel("s", "M3U", "", kind="series", series_id="9")])[0]
    store.set_favorite(other.id, True)
    calls, pending, played, toasts = [], [], [], []
    now = [datetime(2026, 10, 9, 12)]
    responses = {"1": MediaDetails(episodes=[episode("1"), episode("2")]), "2": MediaDetails()}

    class Client:
        def media_details(self, channel):
            calls.append(channel.series_id)
            response = responses[channel.series_id]
            if isinstance(response, Exception):
                raise response
            return response

    def task(function, success, message, **kwargs):
        assert not kwargs["busy"] and message == ""
        pending.append((function, success, kwargs["failure"]))

    def complete():
        function, success, failed = pending.pop(0)
        try:
            result = function()
        except Exception as error:
            failed(error)
        else:
            success(result)

    def switch(profile_id):
        store.use_profile(profile_id)
        return True

    sender = RecordingNotifications()
    service = NewEpisodeService(
        store,
        task,
        played.append,
        switch,
        toasts.append,
        client_factory=lambda s: Client(),
        sender=sender,
        clock=lambda: now[0],
    )
    yield SimpleNamespace(
        store=store,
        service=service,
        now=now,
        calls=calls,
        pending=pending,
        played=played,
        toasts=toasts,
        sender=sender,
        complete=complete,
        responses=responses,
        series=series,
    )
    service.close()
    store.close()


def next_request(s):
    s.service._timer.stop()
    s.service._next()


def test_new_vs_known_only_favourites_and_daily_persistence(state):
    s = state
    s.store.upsert_channels("a", [episode("1")])
    s.service.refresh()
    s.service.refresh()  # a second refresh never overlaps the first request
    assert len(s.pending) == 1
    s.complete()
    next_request(s)
    assert s.calls == ["1"] and not s.pending
    assert s.toasts == ["Dark: 1 yeni bölüm"]
    assert s.sender.sent == [("Yeni bölümler", "Dark: 1 yeni bölüm")]
    assert {c.provider_key for c in s.store.channels("a")} >= {"episode:1:1", "episode:1:2"}
    reopened = Store(s.store.path)
    assert reopened.episode_check_day("a:s1") == "2026-10-09"
    assert reopened.unseen_series() == {"a:s1"}
    reopened.close()
    s.now[0] += timedelta(days=1)
    s.service.refresh()
    s.complete()
    assert s.calls == ["1", "1"] and len(s.sender.sent) == 1


def test_empty_baseline_notifies_and_provider_key_prevents_duplicates(state):
    s = state
    s.service.refresh()
    s.complete()
    assert s.toasts == ["Dark: 2 yeni bölüm"]
    s.now[0] += timedelta(days=1)
    renamed = episode("renamed")
    renamed.provider_key = "episode:1:2"
    s.responses["1"] = MediaDetails(episodes=[renamed])
    next_request(s)
    s.service.refresh()
    s.complete()
    assert len(s.toasts) == 1


def test_errors_stop_round_and_disabled_setting_prevents_requests(state):
    s = state
    s.store.set_favorite("a:s2", True)
    s.responses["1"] = RuntimeError("offline")
    s.service.refresh()
    s.complete()
    next_request(s)
    assert s.calls == ["1"] and not s.pending and not s.toasts
    assert s.store.episode_check_day("a:s2") is None
    s.store.set_setting("new_episode_notifications", "off")
    s.service.refresh()
    assert not s.pending


def test_checks_wait_between_series_and_stop_after_close(state):
    s = state
    s.store.set_favorite("a:s2", True)
    s.service.refresh()
    assert len(s.pending) == 1
    s.complete()
    assert not s.pending and s.service._timer.isActive()
    assert s.service._timer.interval() >= 250
    next_request(s)
    assert len(s.pending) == 1
    s.service.close()
    s.complete()
    assert not s.service._timer.isActive()


def test_unseen_is_per_profile_and_notification_switch_is_guarded(state):
    s = state
    original = s.store.profile_id
    other = s.store.create_profile("Other", "#AABBCC")
    s.store.use_profile(other)
    s.store.set_favorite("a:s1", True)
    s.service.refresh()
    s.complete()
    assert len(s.sender.sent) == 2
    s.store.see_series("a:s1")
    assert not s.store.unseen_series()
    s.store.use_profile(original)
    assert s.store.unseen_series() == {"a:s1"}
    s.service.switch_profile = lambda pid: False
    s.sender.action_invoked.emit(2, "watch")
    assert not s.played and s.store.profile_id == original
    s.store.use_profile(other)
    s.service.switch_profile = lambda pid: s.store.use_profile(pid) or True
    s.sender.action_invoked.emit(1, "watch")
    assert s.store.profile_id == original and s.played[0].provider_key == "episode:1:1"


def test_badge_role_and_open_details_clears_only_active_profile(state, monkeypatch):
    s = state
    s.service.refresh()
    s.complete()
    model = ChannelModel()
    model.reset(s.store.channels(), s.store.favorites())
    model.set_unseen_series(s.store.unseen_series())
    index = model.index(model.row_of("a:s1"), 0)
    assert index.data(NEW_EPISODES_ROLE)
    assert "yeni bölüm" in index.data(Qt.AccessibleTextRole)
    other = s.store.create_profile("Other", "#AABBCC")
    episodes = [c for c in s.store.channels() if c.provider_key == "episode:1:1"]
    s.store.mark_new_episodes("a:s1", episodes, [other])
    # A separate handle lets window.close own its store lifecycle.
    window = MainWindow(Store(s.store.path))
    monkeypatch.setattr(window, "run_task", lambda *a, **kw: None)
    try:
        window.details.open(s.series[0])
        assert not window.store.unseen_series()
        assert not window.model.index(window.model.row_of("a:s1"), 0).data(NEW_EPISODES_ROLE)
        window.store.use_profile(other)
        assert window.store.unseen_series() == {"a:s1"}
    finally:
        window.close()


def test_removed_favourite_or_source_ignores_late_result(state):
    s = state
    s.service.refresh()
    s.store.set_favorite("a:s1", False)
    s.complete()
    assert not s.toasts and not s.store.unseen_series()


def test_automatic_refresh_signal_starts_episode_round(state):
    from luna_iptv.auto_refresh import RefreshScheduler

    s = state
    completions = []
    scheduler = RefreshScheduler(
        s.store,
        lambda source, **kw: completions.append(kw["on_finished"]),
        lambda source: source["id"] == "a",
        lambda message: None,
        clock=lambda: s.now[0].timestamp(),
    )
    scheduler.refreshed.connect(s.service.refresh)
    try:
        scheduler.check()
        completions[0](True, False)
        assert len(s.pending) == 1
    finally:
        scheduler.close()


def test_notification_setting_defaults_on_and_is_persistent(state):
    from luna_iptv.settings_dialog import SettingsDialog

    s = state
    dialog = SettingsDialog(s.store, tray_available=False)
    assert dialog.episode_combo.currentText() == "Açık"
    dialog.episode_combo.setCurrentIndex(dialog.episode_combo.findData("off"))
    assert s.store.setting("new_episode_notifications") == "off"
    s.service.refresh()
    assert not s.pending
    dialog.reject()


def test_inflight_result_is_ignored_after_connection_changes(state):
    s = state
    s.service.refresh()
    source = s.store.sources()[0]
    s.store.save_source(dict(source, location="https://changed.invalid"))
    s.complete()
    assert not s.toasts and not s.store.unseen_series()
    assert s.store.episode_check_day("a:s1") == "2026-10-09"


def test_notification_play_checks_parent_series_lock_before_normal_play(state, monkeypatch):
    from luna_iptv.parental import hash_pin

    s = state
    s.service.refresh()
    s.complete()
    window = MainWindow(Store(s.store.path))
    requested = []
    monkeypatch.setattr(window, "request_play", requested.append)
    first = next(c for c in window.store.channels() if c.provider_key == "episode:1:1")
    try:
        window.store.set_pin_hash(hash_pin("1234"))
        window.store.set_channel_locked("a:s1", True)
        window.apply_locks()
        monkeypatch.setattr("luna_iptv.window.ask_pin", lambda *args: False)
        window.watch_new_episode(first)
        assert not requested
        monkeypatch.setattr("luna_iptv.window.ask_pin", lambda *args: True)
        window.watch_new_episode(first)
        assert requested == [first]
        requested.clear()
        window.store.update_profile(window.store.profile_id, kids=True)
        window.load_profile()
        window.watch_new_episode(first)
        assert not requested
    finally:
        window.close()
