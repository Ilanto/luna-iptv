"""Desktop entry point. Keep version/help usable without loading a GUI stack."""

import argparse
import os
from pathlib import Path

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Luna IPTV — kişisel Linux IPTV istemcisi")
    parser.add_argument("file", nargs="?", help="M3U listesi veya video dosyası")
    parser.add_argument("--version", action="version", version=f"Luna IPTV {__version__}")
    parser.add_argument("--data-dir", type=Path, help="Ayrı kütüphane dizini")
    args = parser.parse_args()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .storage import Store
    from .theme import apply_theme
    from .window import MainWindow

    # Let Qt select native Wayland on Wayland sessions; explicit user choice wins.
    if "QT_QPA_PLATFORM" not in os.environ and os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    app = QApplication([])
    app.setApplicationName("Luna IPTV")
    app.setOrganizationName("Luna")
    app.setDesktopFileName("luna-iptv")
    icon = Path(__file__).resolve().parents[1] / "assets" / "luna-iptv.svg"
    app.setWindowIcon(QIcon(str(icon)) if icon.exists() else QIcon.fromTheme("luna-iptv"))
    apply_theme(app)
    data_dir = (
        args.data_dir
        or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "luna-iptv"
    )
    try:
        store = Store(data_dir / "library.sqlite3")
    except (RuntimeError, OSError):
        QMessageBox.critical(
            None,
            "Kütüphane açılamadı",
            "Yerel veritabanı okunamadı. Veri dizinindeki izinleri ve boş disk alanını kontrol edin.",
        )
        return 1
    window = MainWindow(store)
    window.show()
    if args.file:
        path = str(Path(args.file).expanduser().resolve())
        QTimer.singleShot(100, lambda: window.add_source(location=path))
    return app.exec()
