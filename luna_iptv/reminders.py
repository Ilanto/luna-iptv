"""Persistent programme reminders with one deadline timer and desktop actions."""

from __future__ import annotations

import ctypes
import math
import time
from datetime import datetime
from html import escape

from PySide6.QtCore import SLOT, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusConnection,
    QDBusMessage,
    QDBusPendingCallWatcher,
)
from shiboken6 import getCppPointer

from .mpris import _strings, _variant_map

_BUS = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"
_append_uint32 = None


def _uint32(value: int) -> QDBusArgument | None:
    """Use Qt's uint overload; quietly decline when its Linux ABI is unavailable."""
    global _append_uint32
    if not 0 <= value < 2**32:
        raise OverflowError("Notification IDs must fit in uint32")
    if _append_uint32 is None:
        try:
            library = ctypes.CDLL("libQt6DBus.so.6")
            _append_uint32 = library._ZN13QDBusArgumentlsEj
        except (OSError, AttributeError):
            _append_uint32 = False
        else:
            _append_uint32.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            _append_uint32.restype = ctypes.c_void_p
    if not _append_uint32:
        return None
    argument = QDBusArgument()
    _append_uint32(getCppPointer(argument)[0], value)
    return argument


class DesktopNotifications(QObject):
    """Async Notify transport, replaceable without opening a session bus in tests."""

    action_invoked = Signal("uint", str)
    notification_closed = Signal("uint")

    def __init__(self, parent=None, *, connection=None):
        super().__init__(parent)
        self._connection = connection if connection is not None else QDBusConnection.sessionBus()
        self._closed = False
        self._subscriptions = []
        self._pending = set()
        if self._connection.isConnected():
            for name, slot in (
                ("ActionInvoked", SLOT("_action(uint,QString)")),
                ("NotificationClosed", SLOT("_dismissed(uint,uint)")),
            ):
                args = (_BUS, _PATH, _BUS, name, self, slot)
                if self._connection.connect(*args):
                    self._subscriptions.append(args)

    @Slot("uint", str)
    def _action(self, notification_id, action):
        if not self._closed:
            self.action_invoked.emit(notification_id, action)

    @Slot("uint", "uint")
    def _dismissed(self, notification_id, reason):
        if not self._closed:
            self.notification_closed.emit(notification_id)

    def send(self, title, body, callback):
        """Report the server ID, or None so the caller can show its status fallback."""
        replaces = _uint32(0)
        if (
            self._closed
            or replaces is None
            or not self._connection.isConnected()
            or len(self._subscriptions) != 2
        ):
            callback(None)
            return
        message = QDBusMessage.createMethodCall(_BUS, _PATH, _BUS, "Notify")
        message.setArguments(
            [
                "Luna IPTV",
                replaces,
                "luna-iptv",
                title,
                escape(body),
                _strings(["watch", "İzle"]),
                _variant_map({}),
                -1,
            ]
        )
        watcher = QDBusPendingCallWatcher(self._connection.asyncCall(message, 3000), self)
        self._pending.add(watcher)

        def finished():
            if watcher not in self._pending:
                return
            self._pending.remove(watcher)
            reply = watcher.reply()
            arguments = reply.arguments()
            watcher.deleteLater()
            if not self._closed:
                valid = (
                    reply.type() == QDBusMessage.ReplyMessage
                    and len(arguments) == 1
                    and isinstance(arguments[0], int)
                    and 0 < arguments[0] < 2**32
                )
                callback(arguments[0] if valid else None)

        watcher.finished.connect(finished)
        if watcher.isFinished():
            finished()

    def close(self):
        self._closed = True
        for args in self._subscriptions:
            self._connection.disconnect(*args)
        self._subscriptions.clear()
        for watcher in self._pending:
            watcher.deleteLater()
        self._pending.clear()


class ReminderService(QObject):
    """Schedule stored reminders; play receives the persistent channel ID."""

    changed = Signal()

    def __init__(self, store, play, status, parent=None, *, sender=None, clock=time.time):
        super().__init__(parent)
        self._store = store
        self._play = play
        self._status = status
        self._clock = clock
        self._closed = False
        self._notifications = {}
        self._deliveries = {}
        self._sender = sender if sender is not None else DesktopNotifications(self)
        self._sender.action_invoked.connect(self._watch)
        self._sender.notification_closed.connect(self._dismiss)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._due)
        now = self._clock()
        self._store.drop_expired_reminders(now)
        for reminder in self._store.reminders():
            if not reminder["notified"] and self._deadline(reminder) < now:
                self._store.remove_reminder(reminder["id"])
        self._arm()

    def reload(self):
        """Another profile became active: forget pending notices and re-arm from the store."""
        if self._closed:
            return
        self._notifications = {}
        self._deliveries = {}
        self._store.drop_expired_reminders(self._clock())
        self._arm()
        self.changed.emit()

    @staticmethod
    def _deadline(reminder):
        return reminder["start"] - reminder["lead_minutes"] * 60

    def add(self, channel, programme, lead_minutes=5):
        if self._closed:
            return None
        if (
            programme.start.utcoffset() is None
            or programme.end.utcoffset() is None
            or not math.isfinite(lead_minutes)
            or lead_minutes < 0
        ):
            raise ValueError("Geçerli saat dilimi ve hatırlatma süresi gerekli.")
        start, end = int(programme.start.timestamp()), int(programme.end.timestamp())
        if start <= self._clock() or end <= start:
            raise ValueError("Hatırlatıcı yalnızca gelecekteki programlar için kurulabilir.")
        reminder_id = self._store.add_reminder(
            channel.id, programme.title, start, end, lead_minutes
        )
        self._arm()
        self.changed.emit()
        return reminder_id

    def remove(self, reminder_id):
        if self._closed:
            return
        self._store.remove_reminder(reminder_id)
        self._deliveries.pop(reminder_id, None)
        self._notifications = {
            key: value for key, value in self._notifications.items() if value["id"] != reminder_id
        }
        self._arm()
        self.changed.emit()

    def reminders(self):
        if self._closed:
            return []
        now = self._clock()
        return [item for item in self._store.reminders() if item["end"] > now]

    def _arm(self):
        self._timer.stop()
        if self._closed:
            return
        deadlines = [
            item["end"] if item["notified"] else min(self._deadline(item), item["end"])
            for item in self._store.reminders()
        ]
        if deadlines:
            delay = math.ceil(max(0, min(deadlines) - self._clock()) * 1000)
            self._timer.start(min(delay, 2**31 - 1))

    def _due(self):
        if self._closed:
            return
        now = self._clock()
        changed = bool(self._store.drop_expired_reminders(now))
        self._notifications = {
            key: item for key, item in self._notifications.items() if item["end"] > now
        }
        self._deliveries = {
            key: item for key, item in self._deliveries.items() if item["end"] > now
        }
        channels = {channel.id: channel.name for channel in self._store.channels()}
        for item in self._store.reminders():
            if item["notified"]:
                continue
            if item["channel_id"] not in channels:
                self._store.remove_reminder(item["id"])
                changed = True
            elif self._deadline(item) <= now:
                self._store.mark_reminder_notified(item["id"])
                changed = True
                moment = datetime.fromtimestamp(item["start"]).strftime("%H:%M")
                body = f"{channels[item['channel_id']]} · {item['title']}, {moment}"
                self._deliveries[item["id"]] = item
                self._sender.send(
                    "Program hatırlatıcısı",
                    body,
                    lambda notification_id, item=item, body=body: self._sent(
                        item, body, notification_id
                    ),
                )
        self._arm()
        if changed:
            self.changed.emit()

    def _sent(self, item, body, notification_id):
        if self._closed or self._deliveries.get(item["id"]) is not item:
            return
        self._deliveries.pop(item["id"])
        if not any(
            all(row[key] == item[key] for key in ("id", "channel_id", "start", "end"))
            for row in self.reminders()
        ):
            return
        if notification_id is None:
            self._status(f"Hatırlatıcı: {body}")
        else:
            self._notifications[notification_id] = item

    def _watch(self, notification_id, action):
        if self._closed or action != "watch":
            return
        item = self._notifications.pop(notification_id, None)
        if item is not None and item["end"] > self._clock():
            self._play(item["channel_id"])

    def _dismiss(self, notification_id):
        self._notifications.pop(notification_id, None)

    def close(self):
        self._closed = True
        self._timer.stop()
        self._notifications.clear()
        self._deliveries.clear()
        self._sender.close()
