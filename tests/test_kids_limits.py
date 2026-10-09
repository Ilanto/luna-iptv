"""Children's daily watch budgets, overnight bedtime and parent-authorized extensions."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTime

from luna_iptv.kids_limits import KidsLimits, bedtime_active, profile_limits
from luna_iptv.models import Channel
from luna_iptv.parental import hash_pin
from luna_iptv.profiles_ui import ProfileEditor
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def state(tmp_path, qt_app):
    store = Store(tmp_path / "limits.sqlite3")
    store.save_source({"id": "a", "name": "A", "type": "m3u"})
    channels = store.replace_channels(
        "a", [Channel("one", "One", "file:///one.ts"), Channel("two", "Two", "file:///two.ts")]
    )
    adult = store.profile_id
    kid = store.create_profile("Çocuk", "#AABBCC", kids=True)
    store.use_profile(kid)
    store.set_setting(f"kids_limits:{kid}", {"minutes": 30, "start": None, "end": "07:00"})
    now = [datetime(2026, 10, 9, 12)]
    flushes, stopped, panels, toasts = [], [], [], []
    service = KidsLimits(
        store,
        lambda: flushes.append(True),
        lambda: stopped.append(True),
        panels.append,
        toasts.append,
        clock=lambda: now[0],
    )
    service.reload()
    yield SimpleNamespace(
        store=store,
        adult=adult,
        kid=kid,
        channels=channels,
        now=now,
        service=service,
        flushes=flushes,
        stopped=stopped,
        panels=panels,
        toasts=toasts,
    )
    service.close()
    store.close()


def test_watch_log_sums_today_for_active_profile_and_warns_once(state):
    s = state
    s.store.add_watch_time(s.channels[0].id, "2026-10-08", 90000)
    s.store.add_watch_time(s.channels[0].id, "2026-10-09", 90000, profile_id=s.adult)
    s.store.add_watch_time(s.channels[0].id, "2026-10-09", 900)
    s.store.add_watch_time(s.channels[1].id, "2026-10-09", 600)
    assert s.service.remaining() == 300
    assert s.service.check() and s.service.check()
    assert s.toasts == ["5 dakikan kaldı"] and not s.stopped
    assert len(s.flushes) == 3
    # Switching/restarting the checker cannot repeat today's warning.
    s.service.reload()
    assert s.toasts == ["5 dakikan kaldı"]
    s.store.add_watch_time(s.channels[1].id, "2026-10-09", 300)
    assert not s.service.check() and not s.service.check()
    assert s.stopped == [True] and s.panels == ["limit"]
    s.now[0] += timedelta(days=1)
    assert s.service.check() and s.service.remaining() == 1800
    assert s.panels[-1] is None


@pytest.mark.parametrize(
    "at,expected",
    [
        ("20:59", False),
        ("21:00", True),
        ("23:59", True),
        ("00:00", True),
        ("06:59", True),
        ("07:00", False),
    ],
)
def test_bedtime_spans_midnight(at, expected):
    assert bedtime_active(datetime.fromisoformat(f"2026-10-09T{at}"), "21:00", "07:00") is expected


def test_bedtime_stop_and_parent_extension_expires(state):
    s = state
    s.store.set_pin_hash(hash_pin("1234"))
    s.store.set_setting(f"kids_limits:{s.kid}", {"minutes": 0, "start": "21:00", "end": "07:00"})
    s.now[0] = datetime(2026, 10, 9, 21)
    assert not s.service.check() and s.panels[-1] == "bedtime"
    assert s.service.extend(15, lambda: True)
    assert s.service.check()
    s.now[0] += timedelta(minutes=15)
    assert not s.service.check()
    s.now[0] = datetime(2026, 10, 10, 7)
    assert s.service.check()


def test_extension_requires_pin_adds_today_and_persists(state):
    s = state
    s.store.add_watch_time(s.channels[0].id, "2026-10-09", 1800)
    assert not s.service.extend(15, lambda: True)  # no PIN is not authorization
    s.store.set_pin_hash(hash_pin("1234"))
    assert not s.service.extend(15, lambda: False)
    assert not s.service.extend(12, lambda: True)
    assert s.service.remaining() == 0
    for minutes in (15, 30, 60):
        assert s.service.extend(minutes, lambda: True)
    assert s.service.remaining() == 105 * 60
    reopened = Store(s.store.path)
    assert reopened.setting(f"kids_extra:{s.kid}")["minutes"] == 105
    reopened.close()
    s.now[0] += timedelta(days=1)
    assert s.service.remaining() == 1800


def test_non_kids_profiles_never_run_checks_or_block(state):
    s = state
    s.store.use_profile(s.adult)
    s.store.set_setting(f"kids_limits:{s.adult}", {"minutes": 30, "start": "00:00", "end": "23:59"})
    s.store.add_watch_time(s.channels[0].id, "2026-10-09", 99999)
    s.flushes.clear()
    s.service.reload()
    assert s.service.check() and not s.service._timer.isActive()
    assert not s.flushes and not s.stopped and not s.toasts
    s.store.use_profile(s.kid)
    s.service.reload()
    assert s.service._timer.isActive() and s.service._timer.interval() == 30000


def test_profile_editor_saves_limits_and_hides_for_adults(state):
    s = state
    editor = ProfileEditor(s.store, s.store.profile(s.kid))
    assert not editor.limits_box.isHidden()
    editor.daily_limit.setCurrentIndex(editor.daily_limit.findData(90))
    editor.bedtime.setCurrentIndex(1)
    editor.bedtime_start.setTime(QTime(21, 0))
    editor.bedtime_end.setTime(QTime(7, 0))
    assert editor.save()
    assert profile_limits(s.store, s.kid) == {"minutes": 90, "start": "21:00", "end": "07:00"}
    editor = ProfileEditor(s.store, s.store.profile(s.adult))
    assert editor.limits_box.isHidden()
    editor.reject()


def test_window_stops_and_refuses_all_play_paths_and_pin_gates_extension(state, monkeypatch):
    s = state
    window = MainWindow(Store(s.store.path))
    window.kids_limits.clock = lambda: s.now[0]
    loaded = []
    monkeypatch.setattr(window.player, "load", lambda *args, **kw: loaded.append(args))
    monkeypatch.setattr(window.player, "set_property", lambda *args: None)
    try:
        window.request_play(s.channels[0])
        assert window.current is not None and len(loaded) == 1
        window.store.add_watch_time(s.channels[0].id, "2026-10-09", 1800)
        assert not window.kids_limits.check()
        assert window.current is None and not window.kids_limit_panel.isHidden()
        assert window.kids_limit_panel.heading.text() == "Süren doldu"
        window.request_play(s.channels[0], approved=True)
        window.play(s.channels[0])
        window.play_next_episode(s.channels[0])
        assert len(loaded) == 1
        window.store.set_pin_hash(hash_pin("1234"))
        monkeypatch.setattr("luna_iptv.window.ask_pin", lambda *args: False)
        monkeypatch.setattr("luna_iptv.window.QInputDialog.getItem", lambda *args: ("15 dk", True))
        window.extend_kids_time()
        assert not window.kids_limits.check()
        assert not window.switch_profile(s.adult)
        monkeypatch.setattr("luna_iptv.window.ask_pin", lambda *args: True)
        window.extend_kids_time()
        assert window.kids_limits.check() and window.kids_limit_panel.isHidden()
        window.request_play(s.channels[0])
        assert len(loaded) == 2
        assert window.switch_profile(s.adult)
        assert not window.kids_limits._timer.isActive()
    finally:
        window.close()


def test_deleted_profile_does_not_pass_limits_to_reused_identity(state):
    s = state
    s.store.set_setting(f"kids_extra:{s.kid}", {"day": "2026-10-09", "minutes": 60})
    s.store.set_setting(f"kids_warning:{s.kid}", "2026-10-09")
    s.store.delete_profile(s.kid)
    for key in ("kids_limits", "kids_extra", "kids_warning"):
        assert s.store.setting(f"{key}:{s.kid}") is None
    new = s.store.create_profile("Yeni çocuk", "#AABBCC", kids=True)
    assert profile_limits(s.store, new)["minutes"] == 0


def test_check_flushes_real_tracker_before_counting_budget(state):
    from luna_iptv.statistics import WatchTracker

    s = state
    monotonic = [0.0]
    tracker = WatchTracker(s.store, monotonic=lambda: monotonic[0], clock=lambda: s.now[0])
    tracker.begin(s.channels[0].id)
    tracker.set_running(True)
    s.service.flush = tracker.flush
    monotonic[0] = 1800
    s.now[0] += timedelta(minutes=30)
    assert not s.service.check()
    assert s.store.watch_seconds("2026-10-09") == 1800
    tracker.finish()


def test_bedtime_warns_five_minutes_before_even_with_unlimited_budget(state):
    s = state
    s.store.set_setting(f"kids_limits:{s.kid}", {"minutes": 0, "start": "21:00", "end": "07:00"})
    s.now[0] = datetime(2026, 10, 9, 20, 55)
    assert s.service.check() and s.service.check()
    assert s.toasts == ["5 dakikan kaldı"]
    s.now[0] += timedelta(minutes=5)
    assert not s.service.check() and s.panels[-1] == "bedtime"


def test_extension_cannot_follow_profile_switch_during_pin_dialog(state):
    s = state
    s.store.set_pin_hash(hash_pin("1234"))

    def authorize():
        s.store.use_profile(s.adult)
        return True

    assert not s.service.extend(30, authorize)
    assert s.store.setting(f"kids_extra:{s.kid}") is None
