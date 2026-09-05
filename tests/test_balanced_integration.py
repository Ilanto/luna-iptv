from PySide6.QtWidgets import QInputDialog

from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def test_rename_and_account_actions_coexist_and_keep_snapshot(qt_app, tmp_path, monkeypatch):
    from luna_iptv.accounts import AccountProfile

    store = Store(tmp_path / "library.sqlite3")
    store.save_source(
        {"id": "home", "name": "Old", "type": "xtream", "location": "https://example.test"}
    )
    profile = AccountProfile("active", 1_700_000_000, 1_900_000_000, 1, 2, 1_800_000_000)
    store.save_account_profile("home", profile)
    w = MainWindow(store)
    opened = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Home TV", True))
    monkeypatch.setattr(w, "open_account", lambda source: opened.append(source))
    try:
        w.source_combo.setCurrentIndex(w.source_combo.findData("home"))
        actions = {action.text(): action for action in w.build_source_menu().actions()}
        assert actions["Seçili kaynağı yeniden adlandır"].isEnabled()
        assert actions["Hesap durumu"].isEnabled()
        actions["Seçili kaynağı yeniden adlandır"].trigger()
        assert w.source_combo.currentText() == "Home TV"
        assert store.account_profile("home") == profile
        fresh_actions = {action.text(): action for action in w.build_source_menu().actions()}
        fresh_actions["Hesap durumu"].trigger()
        assert opened[0]["id"] == "home"
        assert opened[0]["name"] == "Home TV"
    finally:
        w.close()
        qt_app.processEvents()
