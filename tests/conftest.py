"""Keep one QApplication alive across all Qt/player tests in this process."""

import os
import threading

import pytest

_qt_application = None


@pytest.fixture(scope="session")
def qt_app():
    global _qt_application
    if not any(os.environ.get(key) for key in ("WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORM")):
        pytest.skip("Qt integration requires a desktop display or explicit Qt platform")
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    _qt_application = app
    yield app
    # Windows close before this fixture. Let their bounded workers and native
    # player shutdown callbacks finish before Python releases the application.
    QThreadPool.globalInstance().waitForDone(25000)
    for worker in threading.enumerate():
        if worker.name == "mpv-shutdown":
            worker.join(timeout=25)
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    _qt_application = None


@pytest.fixture(autouse=True)
def flush_gui_deferred_deletes():
    yield
    if _qt_application is not None:
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        _qt_application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class RecordingMpris:
    """Stand-in for MprisService: window tests never appear in the desktop's media controls."""

    def __init__(self, controller, parent=None, **_):
        self.controller = controller
        self.updates = []
        self.position = 0
        self.seeks = []

    active = False

    def update(self, **state):
        self.updates.append(state)

    def set_position(self, position_us):
        self.position = position_us

    def seeked(self, position_us):
        self.position = position_us
        self.seeks.append(position_us)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def private_media_controls(monkeypatch):
    import luna_iptv.window

    monkeypatch.setattr(luna_iptv.window, "MprisService", RecordingMpris)


class RecordingNotifications:
    """Window tests record notifications without contacting the desktop server."""

    def __init__(self, parent=None):
        from PySide6.QtCore import QObject, Signal

        class Events(QObject):
            action_invoked = Signal("uint", str)
            notification_closed = Signal("uint")

        self._events = Events(parent)
        self.action_invoked = self._events.action_invoked
        self.notification_closed = self._events.notification_closed
        self.sent = []
        self.closed = False
        self.available = True
        self.defer = False
        self.callbacks = []

    def send(self, title, body, callback):
        self.sent.append((title, body))
        self.callbacks.append(callback)
        if not self.defer:
            callback(len(self.sent) if self.available else None)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def private_reminder_notifications(monkeypatch):
    import luna_iptv.reminders

    monkeypatch.setattr(luna_iptv.reminders, "DesktopNotifications", RecordingNotifications)


@pytest.fixture(autouse=True)
def private_auto_refresh(monkeypatch):
    """Unrelated window tests never launch real background catalogue requests.

    Scheduler tests drive the real service explicitly with controlled workers.
    Return start so its startup path can also be exercised in isolation.
    """
    from luna_iptv.auto_refresh import RefreshScheduler

    start = RefreshScheduler.start
    monkeypatch.setattr(RefreshScheduler, "start", RefreshScheduler.close)
    return start
