"""Online secrets, verification and backups use only disposable synthetic data."""

import json
import time

import pytest
from PySide6.QtWidgets import QLineEdit

from luna_iptv.backup import export_backup
from luna_iptv.settings import ONLINE_SECRETS
from luna_iptv.settings_dialog import SettingsDialog
from luna_iptv.storage import Store
from luna_iptv.subtitles import OpenSubtitlesClient
from luna_iptv.tmdb import TMDBClient


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    yield value
    value.close()


def test_keys_only_in_secrets_never_backups(store):
    for key in ONLINE_SECRETS:
        store.set_online_secret(key, "synthetic-" + key)
        assert store.online_secret(key) == "synthetic-" + key
        assert store.setting(key) is None
    for credentials in (False, True):
        exported = json.dumps(export_backup(store, include_credentials=credentials))
        assert "synthetic-" not in exported and "secrets" not in exported
    store.set_online_secret("tmdb_api_key", "")
    assert store.online_secret("tmdb_api_key") == ""
    with pytest.raises(ValueError):
        store.online_secret("pin_hash")


def test_settings_mask_save_toggle_and_language(qt_app, store):
    card = SettingsDialog(store)
    for key, field in card.online_fields.items():
        assert field.echoMode() == QLineEdit.Password
        field.setText("synthetic-" + key)
        assert store.online_secret(key) == "synthetic-" + key
        card.show_buttons[key].click()
        assert field.echoMode() == QLineEdit.Normal
        card.show_buttons[key].click()
        assert field.echoMode() == QLineEdit.Password
    assert card.info_language_combo.currentData() == "tr-TR"
    card.info_language_combo.setCurrentIndex(1)
    assert store.setting("info_language") == "en-US"
    card.close()
    reopened = SettingsDialog(store)
    assert reopened.online_fields["tmdb_api_key"].text() == "synthetic-tmdb_api_key"
    reopened.close()


@pytest.mark.parametrize(
    "key,client",
    [
        ("tmdb_api_key", TMDBClient),
        ("opensubtitles_api_key", OpenSubtitlesClient),
    ],
)
@pytest.mark.parametrize("success", [True, False])
def test_verification_button_uses_worker_and_shows_result(
    qt_app, store, monkeypatch, key, client, success
):
    import threading

    threads = []

    def verify(_self):
        threads.append(threading.get_ident())
        return success

    monkeypatch.setattr(client, "verify", verify)
    card = SettingsDialog(store)
    assert not card.verify_buttons[key].isEnabled()
    card.online_fields[key].setText("synthetic")
    card.verify_buttons[key].click()
    deadline = time.monotonic() + 3
    while card._online_tasks and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    assert not card._online_tasks
    assert card.verify_labels[key].text() == ("✓" if success else "✗")
    assert threads and threads[0] != threading.get_ident()
    assert card.verify_buttons[key].isEnabled()
    card.close()


def test_changed_key_does_not_show_stale_verification(qt_app, store, monkeypatch):
    tasks = []
    monkeypatch.setattr(
        "luna_iptv.online_settings.QThreadPool.start", lambda _pool, task: tasks.append(task)
    )
    card = SettingsDialog(store)
    key = "tmdb_api_key"
    card.online_fields[key].setText("first")
    card.verify_buttons[key].click()
    card.online_fields[key].setText("replacement")
    tasks[0].signals.done.emit(True)
    assert card.verify_labels[key].text() == ""
    assert card.verify_buttons[key].isEnabled()
    card.verify_buttons[key].click()
    tasks[1].signals.failed.emit("Never display server data")
    assert card.verify_labels[key].text() == "✗"
    card.close()
