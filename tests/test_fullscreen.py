"""Real compositor transitions: late Wayland configure must not undo Escape."""

import time

from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def test_rapid_fullscreen_exit_settles_to_user_request(qt_app, tmp_path):
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    window.show()

    def wait(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.01)
            if predicate():
                return
        assert predicate(), f"fullscreen={window.isFullScreen()}, desired={window._fullscreen}"

    try:
        wait(lambda: window.windowHandle().isExposed())
        for _ in range(3):
            window.toggle_fullscreen()
            wait(window.isFullScreen)
            window.leave_fullscreen()
            wait(lambda: not window.isFullScreen())
            # Consume delayed compositor acknowledgements after Qt's immediate state.
            until = time.monotonic() + 0.3
            while time.monotonic() < until:
                qt_app.processEvents()
                time.sleep(0.01)
            assert not window.isFullScreen()
            assert window.sidebar.isVisible() and window.library.isVisible()
    finally:
        window.close()
        qt_app.processEvents()
