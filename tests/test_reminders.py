"""Reminder deadlines and desktop actions, without a real notification server."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from conftest import RecordingNotifications
from PySide6.QtCore import Q_ARG, QMetaObject, Qt
from PySide6.QtDBus import QDBusPendingCall
from PySide6.QtWidgets import QLabel, QPushButton

from luna_iptv import reminders as module
from luna_iptv.epg import GuideIndex
from luna_iptv.models import Channel, Programme
from luna_iptv.reminders import DesktopNotifications, ReminderService, _uint32
from luna_iptv.reminders_dialog import RemindersDialog
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

NOW = 1_800_000_000


def lead(store, reminder_id):
    """A reminder's lead time, or None once it is gone. Never kept in app settings."""
    assert not any(key.startswith("reminder_lead") for key in store_settings(store))
    row = next((r for r in store.reminders() if r["id"] == reminder_id), None)
    return None if row is None else row["lead_minutes"]


def store_settings(store):
    return [key for (key,) in store._db.execute("SELECT key FROM app_settings")]


def programme(start=NOW + 600, end=None, title="Gece Sineması"):
    return Programme(
        "xmltv-id",
        title,
        datetime.fromtimestamp(start, timezone.utc),
        datetime.fromtimestamp(end or start + 1800, timezone.utc),
        "",
    )


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.save_source({"id": "home", "name": "Ev", "type": "m3u"})
    value.replace_channels("home", [Channel("tv", "Ay TV", "file:///tv", tvg_id="xmltv-id")])
    yield value
    value.close()


@pytest.fixture
def service(qt_app, store):
    clock = [float(NOW)]
    sender = RecordingNotifications()
    played, statuses = [], []
    value = ReminderService(
        store, played.append, statuses.append, sender=sender, clock=lambda: clock[0]
    )
    yield value, sender, clock, played, statuses
    value.close()


def test_storage_upgrade_duplicates_cleanup_and_persistence(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = Store(path)
    old.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE reminders")
    value = Store(path)
    first = value.add_reminder("home:tv", "İlk", 200, 300, 10)
    assert value.add_reminder("home:tv", "Tekrar", 200, 300) == first
    second = value.add_reminder("other:tv", "Diğer", 100, 150)
    value.mark_reminder_notified(first)
    value.close()
    value = Store(path)
    try:
        assert [row["id"] for row in value.reminders()] == [second, first]
        assert value.reminders()[1]["notified"] == 1
        assert lead(value, first) == 10
        assert value.drop_expired_reminders(150) == 1
        assert lead(value, second) is None
        value.remove_reminder(first)
        value.remove_reminder(first)
        assert not value.reminders()
        assert lead(value, first) is None
    finally:
        value.close()


def test_one_timer_rearms_for_earliest_deadline_and_cancel(service, store):
    value, sender, clock, _, _ = service
    changes = []
    value.changed.connect(lambda: changes.append(True))
    channel = store.channels()[0]
    later = value.add(channel, programme(NOW + 1800))
    earlier = value.add(channel, programme())
    assert value._timer.isSingleShot()
    assert value._timer.interval() == 300_000
    assert value.add(channel, programme()) == earlier
    assert len(value.reminders()) == 2
    value.remove(earlier)
    assert value._timer.interval() == 1_500_000
    value.remove(later)
    assert not value._timer.isActive()
    assert not sender.sent
    assert len(changes) == 5


def test_due_once_actions_are_scoped_and_closed_ids_are_forgotten(service, store):
    value, sender, clock, played, statuses = service
    channel = store.channels()[0]
    first = value.add(channel, programme())
    value.add(channel, programme(NOW + 601, title="İkinci"))
    clock[0] = NOW + 299
    value._due()
    assert not sender.sent
    clock[0] = NOW + 301
    value._due()
    value._due()
    assert len(sender.sent) == 2
    assert "Ay TV · Gece Sineması" in sender.sent[0][1]
    assert all(item["notified"] for item in store.reminders())
    assert not statuses
    sender.action_invoked.emit(999, "watch")
    sender.action_invoked.emit(1, "unknown")
    assert not played
    sender.action_invoked.emit(1, "watch")
    sender.action_invoked.emit(1, "watch")
    assert played == [channel.id]
    sender.notification_closed.emit(2)
    sender.action_invoked.emit(2, "watch")
    assert played == [channel.id]
    value.remove(first)


def test_unavailable_notifications_fall_back_once(service, store):
    value, sender, clock, _, statuses = service
    sender.available = False
    value.add(store.channels()[0], programme(NOW + 60))
    value._due()
    value._due()
    assert len(statuses) == 1
    assert statuses[0].startswith("Hatırlatıcı: Ay TV · Gece Sineması,")
    assert store.reminders()[0]["notified"] == 1


def test_startup_drops_missed_deadlines_and_keeps_custom_lead(qt_app, store):
    expired = store.add_reminder("home:tv", "Bitti", NOW - 1000, NOW)
    missed = store.add_reminder("home:tv", "Kaçtı", NOW + 60, NOW + 1200)
    custom = store.add_reminder("home:tv", "Özel", NOW + 1200, NOW + 2400, 10)
    boundary = store.add_reminder("home:tv", "Tam şimdi", NOW + 300, NOW + 900)
    notified = store.add_reminder("home:tv", "Bildirildi", NOW + 30, NOW + 600)
    store.mark_reminder_notified(notified)
    sender = RecordingNotifications()
    value = ReminderService(store, lambda _: None, lambda _: None, sender=sender, clock=lambda: NOW)
    try:
        assert {item["id"] for item in value.reminders()} == {custom, boundary, notified}
        assert lead(store, expired) is None
        assert lead(store, missed) is None
        assert not sender.sent
        value._due()
        assert len(sender.sent) == 1
        # The next event is the notified programme's expiry at +600; custom is due then too.
        assert value._timer.interval() == 600_000
    finally:
        value.close()


def test_custom_lead_survives_service_restart(qt_app, store):
    channel = store.channels()[0]
    for iteration in range(2):
        value = ReminderService(
            store,
            lambda _: None,
            lambda _: None,
            sender=RecordingNotifications(),
            clock=lambda: NOW,
        )
        if iteration == 0:
            value.add(channel, programme(NOW + 1800), lead_minutes=20)
        assert value._timer.interval() == 600_000
        value.close()


def test_sleep_past_end_never_shows_late_notification(service, store):
    value, sender, clock, _, _ = service
    value.add(store.channels()[0], programme())
    clock[0] = NOW + 3000
    value._due()
    assert not sender.sent
    assert not store.reminders()
    assert not value._timer.isActive()


def test_expiry_and_cancel_invalidate_actions(service, store):
    value, sender, clock, played, _ = service
    channel = store.channels()[0]
    first = value.add(channel, programme(NOW + 100))
    value.add(channel, programme(NOW + 200, end=NOW + 300))
    value._due()
    value.remove(first)
    sender.action_invoked.emit(1, "watch")
    clock[0] = NOW + 300
    sender.action_invoked.emit(2, "watch")
    value._due()
    assert not played
    assert not store.reminders()
    assert not value._timer.isActive()


def test_late_async_reply_after_cancel_or_close_is_ignored(service, store):
    value, sender, _, played, statuses = service
    sender.defer = True
    first = value.add(store.channels()[0], programme(NOW + 100))
    value._due()
    value.remove(first)
    sender.callbacks[0](1)
    sender.action_invoked.emit(1, "watch")
    assert not played
    value.add(store.channels()[0], programme(NOW + 200))
    value._due()
    value.close()
    sender.callbacks[1](None)
    assert not statuses
    assert sender.closed
    assert not value._timer.isActive()


def test_removed_channel_is_not_notified(service, store):
    value, sender, clock, _, _ = service
    value.add(store.channels()[0], programme())
    store.remove_source("home")
    clock[0] = NOW + 300
    value._due()
    assert not sender.sent
    assert not value.reminders()


@pytest.mark.parametrize(
    "start,end,lead",
    [
        (NOW, NOW + 50, 5),
        (NOW + 50, NOW + 10, 5),
        (NOW + 50, NOW + 100, -1),
        (NOW + 50, NOW + 100, float("nan")),
    ],
)
def test_invalid_programmes_are_rejected(service, store, start, end, lead):
    with pytest.raises(ValueError):
        service[0].add(store.channels()[0], programme(start, end), lead)
    assert not store.reminders()


class RecordingBus:
    def __init__(self, connected=True, error=False):
        self.connected = connected
        self.error = error
        self.subscriptions = []
        self.released = []
        self.messages = []

    def isConnected(self):
        return self.connected

    def connect(self, *args):
        self.subscriptions.append(args)
        return True

    def disconnect(self, *args):
        self.released.append(args)
        return True

    def asyncCall(self, message, timeout):
        self.messages.append(message)
        reply = (
            message.createErrorReply("org.test.Unavailable", "unavailable")
            if self.error
            else message.createReply()
        )
        if not self.error:
            reply.setArguments([42])
        return QDBusPendingCall.fromCompletedCall(reply)


def test_notify_wire_arguments_and_unsigned_signal_dispatch(qt_app):
    connection = RecordingBus()
    sender = DesktopNotifications(connection=connection)
    ids, actions, closed = [], [], []
    sender.action_invoked.connect(lambda *args: actions.append(args))
    sender.notification_closed.connect(closed.append)
    try:
        sender.send("Başlık", "<Program> & kanal", ids.append)
        qt_app.processEvents()
        assert ids == [42]
        message = connection.messages[0]
        assert message.service() == "org.freedesktop.Notifications"
        assert message.member() == "Notify"
        args = message.arguments()
        signature = "".join(
            item.currentSignature()
            if hasattr(item, "currentSignature")
            else "s"
            if isinstance(item, str)
            else "i"
            for item in args
        )
        assert signature == "susssasa{sv}i"
        assert args[4] == "&lt;Program&gt; &amp; kanal"
        assert args[-1] == -1
        assert len(connection.subscriptions) == 2
        assert QMetaObject.invokeMethod(
            sender, "_action", Qt.DirectConnection, Q_ARG("uint", 2**32 - 1), Q_ARG(str, "watch")
        )
        assert actions == [(2**32 - 1, "watch")]
        assert QMetaObject.invokeMethod(
            sender, "_dismissed", Qt.DirectConnection, Q_ARG("uint", 2**32 - 1), Q_ARG("uint", 2)
        )
        assert closed == [2**32 - 1]
    finally:
        sender.close()
        sender.close()
    assert connection.released == connection.subscriptions


@pytest.mark.parametrize("failure", ["bus", "bridge", "reply"])
def test_transport_failures_are_quiet(qt_app, monkeypatch, capfd, failure):
    connection = RecordingBus(connected=failure != "bus", error=failure == "reply")
    if failure == "bridge":
        monkeypatch.setattr(module, "_append_uint32", False)
    sender = DesktopNotifications(connection=connection)
    ids = []
    try:
        sender.send("Hatırlatıcı", "Program", ids.append)
        qt_app.processEvents()
        assert ids == [None]
        assert not capfd.readouterr().err
    finally:
        sender.close()


def test_uint32_bounds_and_missing_symbol(monkeypatch):
    assert _uint32(0).currentSignature() == "u"
    assert _uint32(2**32 - 1).currentSignature() == "u"
    for value in (-1, 2**32):
        with pytest.raises(OverflowError):
            _uint32(value)
    monkeypatch.setattr(module, "_append_uint32", None)
    monkeypatch.setattr(module.ctypes, "CDLL", lambda _: object())
    assert _uint32(0) is None


def test_dialog_refresh_remove_and_plain_text(service, store):
    value, _, _, _, _ = service
    first = value.add(store.channels()[0], programme(title="<b>Film</b>"))
    dialog = RemindersDialog(value, store)
    try:
        labels = dialog.findChildren(QLabel)
        assert any(
            label.text() == "<b>Film</b>" and label.textFormat() == Qt.PlainText for label in labels
        )
        assert any("Ay TV\n" in label.text() for label in labels)
        button = next(
            button for button in dialog.findChildren(QPushButton) if button.text() == "Kaldır"
        )
        button.click()
        assert not value.reminders()
        assert any(
            label.text() == "Henüz bir hatırlatıcı yok." for label in dialog.findChildren(QLabel)
        )
        assert lead(store, first) is None
    finally:
        dialog.close()


def test_window_menu_confirmation_watch_and_cancel(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "window.sqlite3")
    store.save_source({"id": "home", "name": "Ev", "type": "m3u"})
    channel = store.replace_channels(
        "home", [Channel("tv", "Ay TV", "file:///tv", tvg_id="xmltv-id")]
    )[0]
    window = MainWindow(store)
    played, raised = [], []
    monkeypatch.setattr(window, "request_play", played.append)
    monkeypatch.setattr(window, "raise_", lambda: raised.append("raise"))
    monkeypatch.setattr(window, "activateWindow", lambda: raised.append("activate"))
    try:
        now = datetime.now(timezone.utc)
        future = Programme(
            "xmltv-id", "Sıradaki", now + timedelta(minutes=10), now + timedelta(hours=1), ""
        )
        window._guide_index["home"] = GuideIndex([future])
        menu = window.build_channel_menu(channel)
        submenu = next(
            action.menu() for action in menu.actions() if action.text() == "Hatırlatıcı kur"
        )
        submenu.actions()[0].trigger()
        item = store.reminders()[0]
        assert (
            window.mini_status.text()
            == f"Hatırlatıcı kuruldu: Sıradaki, {future.start.astimezone():%H:%M}"
        )
        value = window.reminder_service
        value._clock = lambda: future.start.timestamp() - 299
        value._due()
        value._sender.action_invoked.emit(1, "watch")
        assert played == [channel]
        assert raised == ["raise", "activate"]
        dialog = window.open_reminders()
        assert window.open_reminders() is dialog
        window.cancel_reminder(item["id"])
        assert not store.reminders()
        assert isinstance(value._sender, RecordingNotifications)
    finally:
        window.close()
    assert value._sender.closed


def test_event_loop_delivers_once_and_duplicate_does_not_reset_notified(service, store, qt_app):
    value, sender, _, _, _ = service
    item = programme(NOW + 60)
    value.add(store.channels()[0], item)
    qt_app.processEvents()
    assert len(sender.sent) == 1
    value.add(store.channels()[0], item)
    qt_app.processEvents()
    assert len(sender.sent) == 1
    assert store.reminders()[0]["notified"] == 1
    assert value._timer.interval() == 1_860_000


def test_zero_lead_tolerates_event_loop_latency(service, store):
    value, sender, clock, _, _ = service
    value.add(store.channels()[0], programme(), lead_minutes=0)
    assert value._timer.interval() == 600_000
    clock[0] = NOW + 600.01
    value._due()
    assert len(sender.sent) == 1


def test_long_deadlines_are_bounded_without_polling(service, store):
    value, sender, clock, _, _ = service
    start = NOW + 60 * 86400
    value.add(store.channels()[0], programme(start))
    assert value._timer.interval() == 2**31 - 1
    clock[0] = start - 3600
    value._due()
    assert value._timer.interval() == 3_300_000
    assert not sender.sent


def test_readding_cancelled_programme_ignores_its_old_async_reply(service, store):
    value, sender, _, played, _ = service
    sender.defer = True
    channel = store.channels()[0]
    item = programme(NOW + 60)
    old = value.add(channel, item)
    value._due()
    value.remove(old)
    value.add(channel, item)
    value._due()
    sender.callbacks[0](1)
    sender.action_invoked.emit(1, "watch")
    assert not played
    sender.callbacks[1](2)
    sender.action_invoked.emit(2, "watch")
    assert played == [channel.id]


def test_all_profiles_are_scheduled_but_dialog_stays_scoped(service, store):
    value, sender, clock, _, _ = service
    home = store.profile_id
    ece = store.create_profile("Ece", "#aa5577")
    store.use_profile(ece)
    other = value.add(store.channels()[0], programme(title="Ece'nin filmi"))
    store.use_profile(home)
    value.reload()
    assert value._timer.interval() == 300_000
    assert value.reminders() == []
    dialog = RemindersDialog(value, store)
    try:
        assert not any("Ece'nin filmi" in label.text() for label in dialog.findChildren(QLabel))
        clock[0] = NOW + 301
        value._due()
        value._due()
        assert len(sender.sent) == 1
        assert sender.sent[0][1].startswith("Ece için: Ay TV · Ece'nin filmi,")
        assert store.profile_id == home
        assert store.all_reminders()[0]["profile_id"] == ece
        assert store.all_reminders()[0]["notified"] == 1
        # Active-profile removal must not cancel another profile's reminder.
        value.remove(other)
        assert len(store.all_reminders()) == 1
    finally:
        dialog.close()


@pytest.mark.parametrize("deferred", [False, True])
def test_profile_reload_preserves_valid_notification_actions(qt_app, store, deferred):
    home = store.profile_id
    ece = store.create_profile("Ece", "#aa5577")
    sender = RecordingNotifications()
    sender.defer = deferred
    played = []

    def switch(profile_id):
        store.use_profile(profile_id)
        value.reload()
        return True

    value = ReminderService(
        store, lambda channel: played.append((store.profile_id, channel)), lambda _: None,
        sender=sender, clock=lambda: NOW, switch_profile=switch,
    )
    try:
        reminder = value.add(store.channels()[0], programme(NOW + 100))
        value._due()
        store.use_profile(ece)
        value.reload()
        value.remove(reminder)  # A different profile cannot invalidate this action.
        if deferred:
            sender.callbacks[0](1)
        sender.action_invoked.emit(1, "watch")
        assert played == [(home, "home:tv")]
    finally:
        value.close()


def test_startup_cleans_missed_deadlines_for_other_profiles(qt_app, store):
    home = store.profile_id
    ece = store.create_profile("Ece", "#aa5577")
    store.use_profile(ece)
    store.add_reminder("home:tv", "Kaçtı", NOW + 30, NOW + 500)
    pending = store.add_reminder("home:tv", "Sonra", NOW + 600, NOW + 900)
    store.use_profile(home)
    value = ReminderService(
        store, lambda _: None, lambda _: None, sender=RecordingNotifications(), clock=lambda: NOW
    )
    try:
        store.use_profile(ece)
        assert [item["id"] for item in store.reminders()] == [pending]
        assert value._timer.interval() == 300_000
    finally:
        value.close()


@pytest.mark.parametrize("allow_pin", [False, True])
def test_other_profile_watch_uses_window_switch_and_respects_pin(
    qt_app, store, monkeypatch, allow_pin
):
    home = store.profile_id
    ece = store.create_profile("Ece", "#aa5577", protected=True)
    window = MainWindow(store)
    played, prompts = [], []
    monkeypatch.setattr(
        window, "request_play", lambda channel: played.append((store.profile_id, channel.id))
    )
    monkeypatch.setattr(window, "guard", lambda prompt: prompts.append(prompt) or allow_pin)
    try:
        value = window.reminder_service
        value._clock = lambda: NOW
        store.use_profile(ece)
        value.add(store.channels()[0], programme(NOW + 100))
        store.use_profile(home)
        value.reload()
        value._due()
        value._sender.action_invoked.emit(1, "watch")
        assert len(prompts) == 1
        assert "Ece" in prompts[0]
        assert played == ([(ece, "home:tv")] if allow_pin else [])
        assert store.profile_id == (ece if allow_pin else home)
    finally:
        window.close()


@pytest.mark.parametrize("invalidate", ["remove", "delete_profile", "remove_channel"])
def test_reload_discards_invalid_outstanding_notifications(service, store, invalidate):
    value, sender, _, played, _ = service
    other = store.create_profile("Ece", "#aa5577")
    store.use_profile(other)
    reminder = value.add(store.channels()[0], programme(NOW + 100))
    value._due()
    if invalidate == "remove":
        store.remove_reminder(reminder)
    elif invalidate == "delete_profile":
        store.delete_profile(other)
    else:
        store.remove_source("home")
    value.reload()
    sender.action_invoked.emit(1, "watch")
    assert not played


def test_reminder_writes_require_the_matching_profile(store):
    home = store.profile_id
    reminder = store.add_reminder("home:tv", "Film", NOW + 100, NOW + 500)
    ece = store.create_profile("Ece", "#aa5577")
    store.use_profile(ece)
    store.mark_reminder_notified(reminder)
    store.remove_reminder(reminder)
    assert store.all_reminders()[0]["notified"] == 0
    store.mark_reminder_notified(reminder, profile_id=home)
    assert store.all_reminders()[0]["notified"] == 1
    assert store.profile_id == ece
    store.remove_reminder(reminder, profile_id=home)
    assert store.all_reminders() == []


def test_cross_profile_watch_without_switch_callback_does_not_play(service, store):
    value, sender, _, played, _ = service
    value.add(store.channels()[0], programme(NOW + 100))
    value._due()
    ece = store.create_profile("Ece", "#aa5577")
    store.use_profile(ece)
    sender.action_invoked.emit(1, "watch")
    assert played == []
    assert store.profile_id == ece
