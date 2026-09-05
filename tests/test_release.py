import re
import tomllib
from pathlib import Path

from luna_iptv import __version__


def test_package_and_runtime_versions_match():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    spec = (root / "packaging/luna-iptv.spec").read_text()
    assert project["project"]["version"] == __version__
    assert re.search(r"^Version:\s*(\S+)", spec, re.M)[1] == __version__


def test_visible_version_matches_runtime(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel, QMessageBox

    from luna_iptv.storage import Store
    from luna_iptv.window import MainWindow

    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))
    try:
        assert any(__version__ in label.text() for label in window.sidebar.findChildren(QLabel))
        window.about()
        assert messages[0].startswith(f"Luna IPTV {__version__}\n")
    finally:
        window.close()
        qt_app.processEvents()
