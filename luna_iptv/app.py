"""Desktop entry point. Keep version/help usable without loading a GUI stack."""

import argparse
import os
from pathlib import Path

from . import __version__
from .gc_guard import MainThreadCollector
from .intro import IntroOverlay


def main():
    parser = argparse.ArgumentParser(description="Luna IPTV — kişisel Linux IPTV istemcisi")
    parser.add_argument("file", nargs="?", help="M3U listesi veya video dosyası")
    parser.add_argument("--version", action="version", version=f"Luna IPTV {__version__}")
    parser.add_argument("--data-dir", type=Path, help="Ayrı kütüphane dizini")
    args = parser.parse_args()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import theme
    from .storage import Store
    from .window import MainWindow

    # Let Qt select native Wayland on Wayland sessions; explicit user choice wins.
    if "QT_QPA_PLATFORM" not in os.environ and os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    app = QApplication([])
    # Qt objects in Python garbage must die on this thread, not in a download worker.
    collector = MainThreadCollector(app)  # noqa: F841 - lives as long as the app
    # Ignored close events hide to tray; accepted closes still exit normally.
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("Luna IPTV")
    app.setOrganizationName("Luna")
    app.setDesktopFileName("luna-iptv")
    icon = Path(__file__).resolve().parents[1] / "assets" / "logo" / "icon-256.png"
    app.setWindowIcon(QIcon(str(icon)) if icon.exists() else QIcon.fromTheme("luna-iptv"))
    data_dir = (
        args.data_dir
        or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "luna-iptv"
    )
    try:
        store = Store(data_dir / "library.sqlite3")
    except (RuntimeError, OSError):
        theme.apply_theme(app)
        QMessageBox.critical(
            None,
            "Kütüphane açılamadı",
            "Yerel veritabanı okunamadı. Veri dizinindeki izinleri ve boş disk alanını kontrol edin.",
        )
        return 1
    theme.apply_theme(app, store)
    window = MainWindow(store, ask_profile=True)
    window.show()
    IntroOverlay.play(window)  # a moment of the eclipse, with full motion only
    if args.file:
        path = str(Path(args.file).expanduser().resolve())
        QTimer.singleShot(100, lambda: window.add_source(location=path))
    return app.exec()
