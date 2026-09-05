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
