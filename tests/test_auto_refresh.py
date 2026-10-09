"""Background refresh uses local calendar deadlines and never overlaps occupied sources."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from luna_iptv.accounts import AccountProfile
from luna_iptv.storage import Store


def stamp(value, zone="Europe/Berlin"):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(zone)).timestamp()


def test_daily_rollover_and_dst():
    from luna_iptv.auto_refresh import next_refresh_due

    zone = ZoneInfo("Europe/Berlin")
    for last, expected in [
        ("2026-10-09T05:00", "2026-10-10T05:00"),
        ("2026-03-28T05:00", "2026-03-29T05:00"),
        ("2026-10-24T05:00", "2026-10-25T05:00"),
    ]:
        assert next_refresh_due(stamp(last), stamp(last), "daily", "05:00", zone) == stamp(expected)
    # Missing local time is normalized forward; fall-back time occurs once per day.
    assert next_refresh_due(stamp("2026-03-28T03:00"), 0, "daily", "02:30", zone) == stamp(
        "2026-03-29T03:30"
    )
    assert next_refresh_due(stamp("2026-10-25T02:30"), 0, "daily", "02:30", zone) == stamp(
        "2026-10-26T02:30"
    )


def test_six_hours_is_elapsed_time_and_off_has_no_deadline():
    from luna_iptv.auto_refresh import next_refresh_due

    now = stamp("2026-03-29T00:00")
    assert next_refresh_due(now, now, "6h") == now + 21600
    assert next_refresh_due(None, now, "daily") == now
    assert next_refresh_due(None, now, "off") is None


@pytest.fixture
def scheduler(tmp_path, qt_app):
    from luna_iptv.auto_refresh import RefreshScheduler

    store = Store(tmp_path / "data.sqlite3")
    store.save_source(
        {
            "id": "a",
            "name": "A",
            "type": "xtream",
            "location": "https://example.test",
            "username": "u",
            "password": "p",
        }
    )
    store.save_source({"id": "b", "name": "B", "type": "m3u", "location": "/missing.m3u"})
    now = [stamp("2026-10-09T12:00")]
    calls, notices, blocked = [], [], set()

    def refresh(source, **kwargs):
        calls.append((source, kwargs))
        return True

    service = RefreshScheduler(
        store, refresh, lambda s: s["id"] not in blocked, notices.append, clock=lambda: now[0]
    )
    state = SimpleNamespace(
        store=store, now=now, calls=calls, notices=notices, blocked=blocked, service=service
    )
    yield state
    service.close()
    store.close()


def test_serial_quiet_refresh_and_persisted_success(scheduler):
    s = scheduler
    s.service.check()
    assert len(s.calls) == 1 and s.calls[0][1]["quiet"] is True
    s.service.check()
    assert len(s.calls) == 1
    s.calls[0][1]["on_finished"](True, True)
    assert s.store.setting("auto_refresh_last")["a"] == s.now[0]
    assert s.notices == ["Kaynaklar güncellendi"]
    s.service.check()
    assert s.calls[1][0]["id"] == "b"
    s.calls[1][1]["on_finished"](True, False)
    s.service.check()
    assert len(s.calls) == 2
    assert len(s.notices) == 1
    assert s.service._timer.isSingleShot() and s.service._timer.interval() > 1000
    path = s.store.path
    reopened = Store(path)
    try:
        assert reopened.setting("auto_refresh_last") == {"a": s.now[0], "b": s.now[0]}
    finally:
        reopened.close()


def test_blocked_and_incomplete_sources_do_not_spin(scheduler):
    s = scheduler
    s.blocked.add("a")
    source = s.store.sources()[1]
    source["location"] = ""
    s.store.save_source(source)
    s.service.check()
    assert not s.calls
    assert s.service._timer.interval() > 1000
    s.blocked.clear()
    s.service.check()
    assert [row[0]["id"] for row in s.calls] == ["a"]


def test_failed_refresh_not_marked_and_backed_off(scheduler):
    s = scheduler
    s.blocked.add("b")
    s.service.check()
    s.calls[0][1]["on_finished"](False, False)
    s.service.check()
    assert len(s.calls) == 1
    assert not s.store.setting("auto_refresh_last", {})
    s.now[0] += 1800
    s.service.check()
    assert len(s.calls) == 2


def test_expiry_once_per_day_even_when_refresh_disabled(scheduler):
    s = scheduler
    s.store.set_setting("auto_refresh", "off")
    s.store.save_account_profile(
        "a", AccountProfile("active", None, int(s.now[0] + 3 * 86400), 0, 1, int(s.now[0]))
    )
    s.service.check()
    s.service.check()
    assert s.notices == ["A aboneliği 3 gün içinde bitiyor"]
    assert not s.calls
    s.now[0] += 86400
    s.service.check()
    assert s.notices[-1] == "A aboneliği 2 gün içinde bitiyor"
    assert len(s.notices) == 2


def test_window_skips_busy_importing_and_playing_source():
    from luna_iptv.window import MainWindow

    w = SimpleNamespace(
        _closed=False,
        _busy=False,
        _importing=False,
        _tasks=set(),
        current=None,
        _playback_active=False,
        _loading=False,
        recovery=SimpleNamespace(state="idle"),
    )
    source = {"id": "a"}
    assert MainWindow._auto_refresh_available(w, source)
    for flag in ("_busy", "_importing"):
        setattr(w, flag, True)
        assert not MainWindow._auto_refresh_available(w, source)
        setattr(w, flag, False)
    w.current = SimpleNamespace(id="a:1")
    for state in ("connecting", "waiting", "buffering", "untracked-connecting"):
        w.recovery.state = state
        assert not MainWindow._auto_refresh_available(w, source)
    w.recovery.state = "playing"
    w._playback_active = True
    assert not MainWindow._auto_refresh_available(w, source)
    assert MainWindow._auto_refresh_available(w, {"id": "b"})


def test_update_settings_defaults_and_persistence(tmp_path, qt_app):
    from PySide6.QtCore import QTime

    from luna_iptv.settings_dialog import SettingsDialog

    store = Store(tmp_path / "settings.sqlite3")
    dialog = SettingsDialog(store)
    try:
        assert dialog.refresh_combo.currentText() == "Her gün"
        assert dialog.refresh_time.time() == QTime(5, 0)
        dialog.refresh_time.setTime(QTime(6, 45))
        dialog.refresh_combo.setCurrentIndex(dialog.refresh_combo.findData("6h"))
        assert store.setting("auto_refresh") == "6h"
        assert store.setting("auto_refresh_time") == "06:45"
        assert not dialog.refresh_time.isEnabled()
    finally:
        dialog.close()
        store.close()


@pytest.fixture
def refresh_window(tmp_path, qt_app, monkeypatch):
    from luna_iptv.models import Channel
    from luna_iptv.player import Player
    from luna_iptv.window import MainWindow

    monkeypatch.setattr(Player, "load", lambda *args, **kwargs: None)
    store = Store(tmp_path / "window.sqlite3")
    store.set_setting("auto_refresh", "off")
    store.set_setting("motion_level", "off")
    store.save_source({"id": "a", "name": "A", "type": "m3u", "location": "/a.m3u"})
    store.replace_channels("a", [Channel("one", "One", "https://example.test/1")])
    window = MainWindow(store)
    qt_app.processEvents()
    yield window
    window.close()


def test_quiet_import_preserves_navigation_and_reports_actual_changes(refresh_window, monkeypatch):
    import luna_iptv.window as module
    from luna_iptv.models import Channel, Playlist

    w = refresh_window
    source = w.store.sources()[0]
    w.source_combo.setCurrentIndex(0)
    w.set_section("movie")
    selection = w.source_combo.currentData()
    section = w.library_pages.currentWidget()
    results, notices, jobs = [], [], []
    monkeypatch.setattr(w, "status", notices.append)
    monkeypatch.setattr(
        module,
        "load_m3u",
        lambda _: Playlist([Channel("one", "One", "https://example.test/1")], [], []),
    )
    monkeypatch.setattr(
        w, "run_task", lambda function, success, *a, **k: jobs.append((function, success, k))
    )
    assert w.import_source(source, quiet=True, on_finished=lambda *args: results.append(args))
    function, success, _ = jobs.pop()
    success(function())
    assert results == [(True, False)] and not notices
    assert w.source_combo.currentData() == selection
    assert w.library_pages.currentWidget() is section
    monkeypatch.setattr(
        module,
        "load_m3u",
        lambda _: Playlist([Channel("one", "Renamed", "https://example.test/1")], [], []),
    )
    w.import_source(source, quiet=True, on_finished=lambda *args: results.append(args))
    function, success, _ = jobs.pop()
    success(function())
    assert results[-1] == (True, True)
    assert w.store.channels()[0].name == "Renamed"
    assert not notices


def test_quiet_import_error_uses_toast_and_completes(refresh_window, monkeypatch):
    w = refresh_window
    notices, results, jobs = [], [], []
    monkeypatch.setattr(w.toast, "show_message", notices.append)
    monkeypatch.setattr(w, "run_task", lambda *args, **kwargs: jobs.append(kwargs))
    w.import_source(
        w.store.sources()[0], quiet=True, on_finished=lambda *args: results.append(args)
    )
    jobs[0]["failure"]("Kaynağa ulaşılamadı.")
    assert notices == ["Kaynağa ulaşılamadı."]
    assert results == [(False, False)]
    assert not w._importing


def test_quiet_import_waits_for_configured_guide(refresh_window, monkeypatch):
    import luna_iptv.window as module
    from luna_iptv.models import Channel, Playlist

    w = refresh_window
    source = w.store.sources()[0]
    source["epg_url"] = "/guide.xml"
    w.store.save_source(source)
    jobs, guides, results = [], [], []
    monkeypatch.setattr(
        module,
        "load_m3u",
        lambda _: Playlist([Channel("one", "One", "https://example.test/1")], [], []),
    )
    monkeypatch.setattr(
        w, "run_task", lambda function, success, *a, **k: jobs.append((function, success))
    )
    monkeypatch.setattr(w, "load_guide", lambda source, **kwargs: guides.append(kwargs))
    w.import_source(source, quiet=True, on_finished=lambda *args: results.append(args))
    function, success = jobs.pop()
    success(function())
    assert not results and guides[0]["quiet"] is True
    guides[0]["on_finished"](True, True)
    assert results == [(True, True)]


def test_partial_failure_does_not_replace_error_with_success(scheduler):
    s = scheduler
    s.service.check()
    s.calls[0][1]["on_finished"](False, True)
    assert not s.notices
    assert not s.store.setting("auto_refresh_last", {})


def test_out_of_range_saved_timestamp_is_treated_as_due():
    from luna_iptv.auto_refresh import next_refresh_due

    assert next_refresh_due(2**63 - 1, 1000) == 1000


def test_playback_start_during_download_defers_apply(refresh_window, monkeypatch):
    from luna_iptv.models import Channel, Playlist

    w = refresh_window
    results, jobs = [], []
    monkeypatch.setattr(w, "run_task", lambda function, success, *a, **k: jobs.append(success))
    w.import_source(
        w.store.sources()[0], quiet=True, on_finished=lambda *args: results.append(args)
    )
    w.current = w.store.channels()[0]
    w._loading = True
    jobs[0](Playlist([Channel("two", "Two", "https://example.test/2")], [], []))
    assert results == [(False, False)]
    assert w.store.channels()[0].name == "One"


def test_quiet_guide_updates_data_and_cache_without_status(refresh_window, monkeypatch, tmp_path):
    w = refresh_window
    source = w.store.sources()[0]
    path = tmp_path / "guide.xml"
    path.write_text(
        '<tv><programme start="20261009050000 +0000" stop="20261009060000 +0000" channel="one"><title>Sabah</title></programme></tv>'
    )
    source["epg_url"] = str(path)
    w.store.save_source(source)
    notices, results, jobs = [], [], []
    monkeypatch.setattr(w, "status", notices.append)
    monkeypatch.setattr(
        w, "run_task", lambda function, success, *a, **k: jobs.append((function, success, k))
    )
    for _ in range(2):
        w.load_guide(source, quiet=True, on_finished=lambda *args: results.append(args))
        function, success, options = jobs.pop()
        assert options["busy"] is True
        success(function())
    assert results == [(True, True), (True, False)]
    assert w._guide_data["a"][0].title == "Sabah"
    assert (w.store.path.parent / "epg-a.xml").read_bytes() == path.read_bytes()
    assert not notices


def test_expiry_notification_persists_across_scheduler_restart(scheduler):
    from luna_iptv.auto_refresh import RefreshScheduler

    s = scheduler
    s.store.set_setting("auto_refresh", "off")
    s.store.save_account_profile(
        "a", AccountProfile("active", None, int(s.now[0] + 86400), 0, 1, int(s.now[0]))
    )
    s.service.check()
    s.service.close()
    replacement = RefreshScheduler(
        s.store, lambda *a, **k: None, lambda _: True, s.notices.append, clock=lambda: s.now[0]
    )
    try:
        replacement.check()
        assert s.notices == ["A aboneliği 1 gün içinde bitiyor"]
    finally:
        replacement.close()


def test_startup_checks_immediately(scheduler, qt_app, private_auto_refresh):
    private_auto_refresh(scheduler.service)
    qt_app.processEvents()
    assert len(scheduler.calls) == 1
    assert scheduler.calls[0][1]["quiet"] is True


def test_manual_import_counts_as_recent_refresh(refresh_window, monkeypatch):
    from luna_iptv.models import Channel, Playlist

    w = refresh_window
    monkeypatch.setattr(w, "status", lambda *args: None)
    w.accept_import(
        w.store.sources()[0], Playlist([Channel("one", "One", "https://example.test/1")], [], [])
    )
    assert w.store.setting("auto_refresh_last", {}).get("a", 0) > 0


def test_two_expiring_accounts_each_get_visible_toast(scheduler, qt_app):
    from PySide6.QtWidgets import QWidget

    from luna_iptv.auto_refresh import RefreshScheduler
    from luna_iptv.toast import Toast

    s = scheduler
    second = s.store.sources()[1]
    second.update(type="xtream", username="u", password="p")
    s.store.save_source(second)
    for source_id in ("a", "b"):
        s.store.save_account_profile(
            source_id, AccountProfile("active", None, int(s.now[0] + 86400), 0, 1, int(s.now[0]))
        )
    s.store.set_setting("auto_refresh", "off")
    parent = QWidget()
    parent.resize(600, 300)
    toast = Toast(parent)
    parent.show()
    service = RefreshScheduler(
        s.store, lambda *a, **k: None, lambda _: True, toast.show_message, clock=lambda: s.now[0]
    )
    try:
        service.check()
        qt_app.processEvents()
        assert toast.text == "A aboneliği 1 gün içinde bitiyor"
        assert set(s.store.setting("auto_refresh_expiry_notified")) == {"a"}
        service.check()
        assert toast.text.startswith("A aboneliği")
        s.now[0] += 7
        service.check()
        qt_app.processEvents()
        assert toast.text == "B aboneliği 1 gün içinde bitiyor"
        assert set(s.store.setting("auto_refresh_expiry_notified")) == {"a", "b"}
    finally:
        service.close()
        parent.close()


def test_background_notice_stays_visible_during_other_source_recovery(refresh_window):
    w = refresh_window
    w.recovery_cancel_button.setHidden(False)
    w.refresh_scheduler._status("A aboneliği 2 gün içinde bitiyor")
    assert w.toast.text == "A aboneliği 2 gün içinde bitiyor"
