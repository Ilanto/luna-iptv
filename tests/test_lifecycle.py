"""Closing a window destroys native Qt resources on their owning GUI thread."""

import gc
import threading

from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def test_close_destroys_native_window_before_background_collection(qt_app, tmp_path):
    gui_thread = threading.get_ident()
    destroyed = []
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    window.destroyed.connect(lambda: destroyed.append(("window", threading.get_ident())))
    window.video.destroyed.connect(lambda: destroyed.append(("video", threading.get_ident())))
    window.show()
    window.video_stack.setCurrentIndex(1)
    qt_app.processEvents()
    try:
        window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(window), "closed window retained native GUI resources"
        assert sorted(destroyed) == [("video", gui_thread), ("window", gui_thread)]
        # Python wrappers/callback cycles may become unreachable on any worker;
        # by then their native QWidget/OpenGL objects must already be gone.
        window = None
        worker = threading.Thread(target=gc.collect)
        worker.start()
        worker.join(timeout=3)
        assert not worker.is_alive()
    finally:
        if window is not None and isValid(window):
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_worker_completion_after_close_releases_gui_signal_object(qt_app, tmp_path):
    import time

    release = threading.Event()
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    results = []
    window.run_task(lambda: release.wait(3), results.append, "pending")
    signals = next(iter(window._tasks)).signals
    destroyed_on = []
    signals.destroyed.connect(lambda: destroyed_on.append(threading.get_ident()))
    try:
        window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        release.set()
        deadline = time.monotonic() + 4
        while not destroyed_on and time.monotonic() < deadline:
            qt_app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            time.sleep(0.01)
        assert not results, "closed window must not run the UI success callback"
        assert not window._tasks, "finished task retained its closed window callbacks"
        assert destroyed_on == [threading.get_ident()]
        assert not isValid(signals)
    finally:
        release.set()
        if isValid(window):
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
