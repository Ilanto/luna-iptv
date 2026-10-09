import copy
import json
import os
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from luna_iptv import backup_dialog
from luna_iptv.accounts import AccountProfile
from luna_iptv.backup import (
    MAX_FILE_SIZE,
    apply_backup,
    export_backup,
    read_backup,
    source_incomplete,
    validate_backup,
    write_backup,
)
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def stores(tmp_path):
    opened = []

    def create(name):
        store = Store(tmp_path / name / "library.sqlite3")
        opened.append(store)
        return store

    yield create
    for store in opened:
        store.close()


@pytest.fixture
def personal(stores):
    store = stores("personal")
    store.save_source(
        {
            "id": "provider",
            "name": "Ev",
            "type": "xtream",
            "location": "https://example.test",
            "username": "fixture-user",
            "password": "fixture-secret",
            "epg_url": (
                "https://example.test/xmltv.php?username=fixture-user&password=fixture-secret"
            ),
        }
    )
    store.save_source(
        {
            "id": "local",
            "name": "Yerel",
            "type": "m3u",
            "location": "/tmp/list.m3u",
        }
    )
    store.replace_channels(
        "provider",
        [
            Channel(
                "old-one",
                "Bir",
                "https://example.test/live/fixture-user/fixture-secret/1.ts",
                kind="movie",
                provider_key="movie:1",
                headers={"Authorization": "fixture-secret"},
            ),
            Channel("two", "İki", "https://example.test/2", kind="movie", provider_key="movie:2"),
            Channel("catalogue-only", "Üç", "https://example.test/3", provider_key="live:3"),
        ],
    )
    store.set_favorite("provider:old-one", True)
    folder = store.create_folder("Akşam")
    store.set_in_folder(folder, "provider:old-one", True)
    store.save_progress("provider:old-one", 42, 100)
    store.save_progress("provider:two", 9, 90, mark_recent=False)
    store.save_playback_preferences("provider", {"remember": True, "audio": {"mode": "off"}})
    store.set_setting("motion_level", "off")
    store.set_setting("custom", {"items": [1, True, None], "name": "Türkçe"})
    store.save_account_profile("provider", AccountProfile("active", None, None, 1, 2, 1700000000))
    store.save_source_health("provider", "available", 1700000000)
    return store


def snapshot(store):
    return list(store._db.iterdump())


def test_default_export_omits_secrets_catalogue_and_snapshots(personal):
    before = snapshot(personal)
    data = export_backup(personal)
    serialized = json.dumps(data)
    assert "fixture-secret" not in serialized
    assert "fixture-user" not in serialized
    assert "Authorization" not in serialized
    assert "account_snapshots" not in serialized and "source_health" not in serialized
    assert "catalogue-only" not in serialized
    assert data["format"] == "luna-iptv-backup" and data["version"] == 2
    assert set(data["sources"][0]["credentials_omitted"]) == {"username", "password", "epg_url"}
    assert data["sources"][0]["location"] == "https://example.test"
    summary = validate_backup(data)
    assert (
        summary.sources,
        summary.favorites,
        summary.favorite_folders,
        summary.folder_members,
    ) == (2, 1, 1, 1)
    assert (summary.playback_preferences, summary.app_settings, summary.history) == (1, 2, 2)
    assert not summary.credentials_present
    assert summary.incomplete_sources == 1
    assert snapshot(personal) == before


@pytest.mark.parametrize(
    "url",
    [
        "https://fixture-user:fixture-secret@example.test/list.m3u",
        "https://example.test/get.php?username=fixture-user&password=fixture-secret",
        "https://example.test/live/fixture-user/fixture-secret/1.ts",
        "https://example.test/opaque-access-token",
        "https://example.test/?unusual-key=fixture-secret",
        "rtsp://fixture-user:fixture-secret@example.test",
        "file://fixture-user:fixture-secret@example.test/video",
        "//fixture-user:fixture-secret@example.test/list.m3u",
    ],
)
def test_connection_urls_are_opt_in(personal, url):
    personal.save_source({"id": "private", "name": "Özel", "type": "direct", "location": url})
    personal.set_setting("nested", {"address": [url]})
    data = export_backup(personal)
    assert data["sources"][-1]["location"] == ""
    assert data["sources"][-1]["credentials_omitted"] == ["location"]
    assert data["app_settings"]["nested"] == {"address": [""]}
    assert url not in json.dumps(data)
    full = export_backup(personal, include_credentials=True)
    assert full["sources"][-1]["location"] == url
    assert validate_backup(full).credentials_present


def test_full_round_trip_and_repeat_import(personal, stores):
    data = export_backup(personal, include_credentials=True)
    target = stores("restored")
    trace = []
    target._db.set_trace_callback(trace.append)
    assert apply_backup(target, data) == validate_backup(data)
    assert sum(sql.startswith("BEGIN") for sql in trace) == 1
    assert sum(sql == "COMMIT" for sql in trace) == 1
    assert target.sources() == personal.sources()
    assert target.favorites() == personal.favorites()
    assert target.folders() == personal.folders()
    assert target.folder_items(1) == personal.folder_items(1)
    assert target.recent_ids() == personal.recent_ids()
    assert target.progress_map() == personal.progress_map()
    assert target.playback_preferences("provider") == personal.playback_preferences("provider")
    assert target.setting("custom") == personal.setting("custom")
    assert target.account_profile("provider") is None
    assert target.source_health("provider") is None
    assert all(not item.url and not item.headers for item in target.channels())
    assert target._db.execute("PRAGMA foreign_key_check").fetchall() == []
    before = snapshot(target)
    apply_backup(target, data)
    assert snapshot(target) == before


def test_redacted_restore_stays_incomplete_and_preserves_existing_connection(personal, stores):
    data = export_backup(personal)
    target = stores("incomplete")
    apply_backup(target, data)
    assert source_incomplete(target.sources()[0])
    assert target.sources()[0]["username"] == target.sources()[0]["password"] == ""
    # A second-generation backup must not erase a complete connection either.
    second = export_backup(target, include_credentials=True)
    before = personal.sources()
    apply_backup(personal, data)
    assert personal.sources() == before
    apply_backup(personal, second)
    assert personal.sources() == before


def test_merge_preserves_unrelated_rows_and_resolves_provider_identity(personal, stores):
    target = stores("merge")
    target.save_source(personal.sources()[0])
    target.replace_channels(
        "provider",
        [
            Channel(
                "current-one",
                "Güncel",
                "https://example.test/current",
                kind="movie",
                provider_key="movie:1",
            ),
            Channel("extra", "Ek", "https://example.test/extra", provider_key="live:9"),
        ],
    )
    target.set_favorite("provider:extra", True)
    target.save_progress("provider:extra", 15, 20)
    target.set_setting("unrelated", 123)
    target.save_playback_preferences("provider", {"sub": {"mode": "off"}})
    target.save_source(
        {"id": "extra", "name": "Ek kaynak", "type": "m3u", "location": "/tmp/extra.m3u"}
    )
    target.save_account_profile("provider", AccountProfile("expired", None, None, 0, 1, 1700000001))
    target.create_folder("Akşam")
    target.set_in_folder(1, "provider:extra", True)
    apply_backup(target, export_backup(personal))
    assert len(target.sources()) == 3
    assert target.favorites() == {"provider:current-one", "provider:extra"}
    assert target.folder_items(1) == target.favorites()
    assert target.progress("provider:current-one") == (42, 100)
    assert target.progress("provider:extra") == (15, 20)
    assert target.setting("unrelated") == 123
    assert target.playback_preferences("provider")["sub"] == {"mode": "off"}
    assert target.account_profile("provider").status == "expired"
    assert target.channels()[0].name == "Güncel"
    assert target.channels()[0].url == "https://example.test/current"


def test_refresh_reconciles_placeholder_and_keeps_personal_rows(personal, stores):
    target = stores("refresh")
    apply_backup(target, export_backup(personal))
    target.replace_channels(
        "provider",
        [
            Channel(
                "new-one", "Bir", "https://example.test/new", kind="movie", provider_key="movie:1"
            ),
            Channel("two", "İki", "https://example.test/2", kind="movie", provider_key="movie:2"),
        ],
    )
    assert target.favorites() == {"provider:old-one"}
    assert target.folder_items(1) == {"provider:old-one"}
    assert target.progress("provider:old-one") == (42, 100)
    assert target.channels()[0].url == "https://example.test/new"


def test_history_is_optional_and_does_not_clear_existing_progress(personal, stores):
    data = export_backup(personal, include_history=False)
    assert data["history"] is None
    assert len(data["channels"]) == 1
    target = stores("no-history")
    apply_backup(target, data)
    assert target.recent_ids() == [] and target.progress_map() == {}
    before = personal.progress_map()
    apply_backup(personal, data)
    assert personal.progress_map() == before


@pytest.mark.parametrize("kind", ["direct", "m3u"])
def test_other_source_types_keep_favorites_after_catalogue_refresh(stores, kind):
    original, target = stores("original"), stores("target")
    source = {"id": "source", "name": "Liste", "type": kind, "location": "/tmp/list.m3u"}
    original.save_source(source)
    original.replace_channels("source", [Channel("stable", "Bir", "https://example.test/1")])
    original.set_favorite("source:stable", True)
    apply_backup(target, export_backup(original))
    target.replace_channels("source", [Channel("stable", "Bir", "https://example.test/1")])
    assert target.favorites() == {"source:stable"}
    assert target.channels()[0].url == "https://example.test/1"


def test_direct_source_merge_reuses_the_existing_single_channel(stores):
    original, target = stores("original"), stores("target")
    source = {"id": "direct", "name": "Yayın", "type": "direct", "location": "/tmp/a.mp4"}
    for store, identity in ((original, "old"), (target, "new")):
        store.save_source(source)
        store.replace_channels("direct", [Channel(identity, "Bir", "file:///tmp/a.mp4")])
    original.set_favorite("direct:old", True)
    original.save_progress("direct:old", 5, 10)
    apply_backup(target, export_backup(original))
    assert len(target.channels()) == 1
    assert target.favorites() == {"direct:new"}
    assert target.progress("direct:new") == (5, 10)


def test_folders_merge_by_name_not_by_backup_id(personal, stores):
    target = stores("folder-ids")
    sport = target.create_folder("Spor")  # same local id as the backup's "Akşam"
    target.create_folder("Başka")
    evening = target.create_folder("akşam ")
    apply_backup(target, export_backup(personal))
    assert target.folders() == [(sport, "Spor"), (sport + 1, "Başka"), (evening, "akşam")]
    assert target.folder_items(sport) == set()
    assert target.folder_items(evening) == {"provider:old-one"}


def test_folder_with_a_new_name_goes_last_under_a_fresh_id(personal, stores):
    target = stores("folder-new")
    sport = target.create_folder("Spor")
    apply_backup(target, export_backup(personal))
    (_, first), (new_id, name) = target.folders()
    assert first == "Spor" and name == "Akşam" and new_id != sport
    assert target.folder_items(sport) == set()
    assert target.folder_items(new_id) == {"provider:old-one"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(format="other"),
        lambda d: d.update(version=3),
        lambda d: d.update(version=True),
        lambda d: d.update(created="yesterday"),
        lambda d: d.update(created="2026-01-01"),
        lambda d: d.update(account_snapshots=[]),
        lambda d: d.update(sources={}),
        lambda d: d["sources"].append(copy.deepcopy(d["sources"][0])),
        lambda d: d["sources"][0].update(type="unknown"),
        lambda d: d["sources"][0].update(name="bad\nname"),
        lambda d: d["sources"][0].update(name="bad\u202ename"),
        lambda d: d["sources"][0].update(name="bad\ud800name"),
        lambda d: d["sources"][0].update(name="x" * 513),
        lambda d: d["sources"][0].update(username=123),
        lambda d: d["sources"][0].update(username="secret", credentials_omitted=["username"]),
        lambda d: d["sources"][0].update(credentials_omitted=["unknown"]),
        lambda d: d["favorites"].append("missing"),
        lambda d: d["channels"][0].update(source_id="missing"),
        lambda d: d["channels"][0].update(kind="invalid"),
        lambda d: d["favorite_folders"][0].update(id=True),
        lambda d: d["favorite_folders"][0].update(members=["missing"]),
        lambda d: d["history"][0].update(position=float("nan")),
        lambda d: d["history"][0].update(duration=float("inf")),
        lambda d: d["history"][0].update(position=-1),
        lambda d: d["history"][0].update(updated_at=2**63),
        lambda d: d["history"][0].update(history_hidden=0),
        lambda d: d["playback_preferences"].update(missing={}),
        lambda d: d["playback_preferences"]["provider"].update(remember=1),
        lambda d: d["app_settings"].update(nested={"bad": "\x00"}),
        lambda d: d["app_settings"].update(nested={1: "bad"}),
    ],
)
def test_invalid_backup_is_rejected_before_writes(personal, mutate):
    data = export_backup(personal)
    mutate(data)
    before = snapshot(personal)
    with pytest.raises(ValueError, match="Yedek|yedek|dosya"):
        apply_backup(personal, data)
    assert snapshot(personal) == before


def test_size_depth_and_non_json_limits(personal):
    data = export_backup(personal)
    data["app_settings"] = {str(i): "x" * 16000 for i in range(2100)}
    with pytest.raises(ValueError, match="32 MB"):
        validate_backup(data)
    nested = []
    for _ in range(20):
        nested = [nested]
    data["app_settings"] = {"deep": nested}
    with pytest.raises(ValueError):
        validate_backup(data)
    data["app_settings"] = {"bad": object()}
    with pytest.raises(ValueError):
        validate_backup(data)


def test_late_sql_failure_rolls_back_every_table(personal, stores):
    target = stores("rollback")
    target.set_setting("keep", "original")
    target._db.execute("""CREATE TRIGGER fail_progress BEFORE INSERT ON progress
                          BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    before = snapshot(target)
    with pytest.raises(sqlite3.IntegrityError):
        apply_backup(target, export_backup(personal, include_credentials=True))
    assert snapshot(target) == before
    assert not target._db.in_transaction


def test_source_type_conflict_rolls_back(personal, stores):
    target = stores("conflict")
    target.save_source({"id": "local", "name": "Başka", "type": "direct", "location": "/tmp/a.mp4"})
    before = snapshot(target)
    with pytest.raises(ValueError, match="çakışıyor"):
        apply_backup(target, export_backup(personal))
    assert snapshot(target) == before


def test_file_io_is_atomic_private_and_cleans_failed_temporary(personal, tmp_path, monkeypatch):
    data = export_backup(personal)
    path = tmp_path / "test.luna-backup.json"
    write_backup(path, data)
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_backup(path) == data
    before = path.read_bytes()

    def fail_replace(source, destination):
        assert Path(source).parent == path.parent
        assert Path(source).stat().st_mode & 0o777 == 0o600
        assert Path(destination) == path
        raise OSError("test failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_backup(path, data)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".luna-backup-*")) == []


@pytest.mark.parametrize("raw", [b"not json", b"\xff", b'{"format":1,"format":2}', b"[" * 2000])
def test_invalid_json_file(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read_backup(path)


def test_file_limit_checks_bytes_before_json_parsing(tmp_path):
    path = tmp_path / "big.json"
    with path.open("wb") as stream:
        stream.truncate(MAX_FILE_SIZE + 1)
    with pytest.raises(ValueError, match="32 MB"):
        read_backup(path)


class BackupWindow(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self._busy = False
        self.messages = []
        self.message_icons = []
        self.refreshes = 0

    def status(self, message, *, icon=None):
        self.messages.append(message)
        self.message_icons.append(icon)

    def load_profile(self):
        self.refreshes += 1

    def _wake_refresh(self):
        pass


@pytest.mark.parametrize("accepted", [False, True])
def test_restore_ui_previews_before_changes_and_refreshes(
    qt_app,
    personal,
    stores,
    tmp_path,
    monkeypatch,
    accepted,
):
    path = tmp_path / "restore.luna-backup.json"
    write_backup(path, export_backup(personal))
    target = stores("ui")
    window = BackupWindow(target)
    monkeypatch.setattr(backup_dialog.QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    questions = []

    def answer(*args):
        assert target.sources() == [] and target.setting("motion_level") is None
        questions.append(args[2])
        assert args[-1] == QMessageBox.No
        return QMessageBox.Yes if accepted else QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    backup_dialog.restore_backup_dialog(window)
    assert len(questions) == 1
    assert "Kaynak: 2" in questions[0] and "Favori: 1" in questions[0]
    assert "Yok" in questions[0] and "Bağlantısı eksik kaynak: 1" in questions[0]
    assert window.refreshes == int(accepted)
    assert len(target.sources()) == (2 if accepted else 0)
    if accepted:
        assert "Seçili kaynağı yenile" in window.messages[-1]
        assert "Bağlantıyı düzenle" in window.messages[-1]
    window.close()


def test_export_ui_defaults_and_cancel(qt_app, personal, monkeypatch):
    window = BackupWindow(personal)
    dialog = backup_dialog.BackupDialog(window)
    assert not dialog.credentials.isChecked()
    assert dialog.history.isChecked()
    dialog.close()
    monkeypatch.setattr(backup_dialog.BackupDialog, "exec", lambda self: QDialog.Rejected)
    monkeypatch.setattr(
        backup_dialog.QFileDialog, "getSaveFileName", lambda *a: pytest.fail("cancelled")
    )
    backup_dialog.save_backup_dialog(window)
    assert window.messages == []
    window.close()


def test_export_ui_passes_explicit_options(qt_app, personal, tmp_path, monkeypatch):
    window = BackupWindow(personal)
    path = tmp_path / "opt-in.luna-backup.json"

    def accept(dialog):
        dialog.credentials.setChecked(True)
        dialog.history.setChecked(False)
        return QDialog.Accepted

    def choose(*args):
        assert args[2].startswith("luna-yedek-") and args[2].endswith(".luna-backup.json")
        return str(path), ""

    monkeypatch.setattr(backup_dialog.BackupDialog, "exec", accept)
    monkeypatch.setattr(backup_dialog.QFileDialog, "getSaveFileName", choose)
    backup_dialog.save_backup_dialog(window)
    data = read_backup(path)
    assert data["sources"][0]["password"] == "fixture-secret"
    assert data["history"] is None
    assert window.messages == ["Yedek kaydedildi."]
    assert window.message_icons == ["check"]
    window.close()


def test_invalid_ui_import_never_asks_for_confirmation(qt_app, personal, tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{}")
    window = BackupWindow(personal)
    warnings = []
    before = snapshot(personal)
    monkeypatch.setattr(backup_dialog.QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: pytest.fail("invalid backup"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    backup_dialog.restore_backup_dialog(window)
    assert warnings and window.refreshes == 0
    assert snapshot(personal) == before
    window.close()


def test_incomplete_source_refresh_does_not_start_network(qt_app, personal):
    window = BackupWindow(personal)
    MainWindow.import_source(window, {"type": "xtream", "location": "https://example.test"})
    assert "Bağlantıyı düzenle" in window.messages[-1]
    window.close()


def test_source_menu_backup_actions_work_without_selected_source(qt_app, stores, monkeypatch):
    window = MainWindow(stores("menu"))
    try:
        calls = []
        monkeypatch.setattr(backup_dialog, "save_backup_dialog", lambda w: calls.append("save"))
        monkeypatch.setattr(
            backup_dialog, "restore_backup_dialog", lambda w: calls.append("restore")
        )
        menu = window.build_source_menu()
        actions = {action.text(): action for action in menu.actions()}
        for text in ("Yedekle…", "Yedekten geri yükle…"):
            assert actions[text].isEnabled()
            actions[text].trigger()
        assert calls == ["save", "restore"]
        window._busy = True
        busy = {action.text(): action for action in window.build_source_menu().actions()}
        assert not busy["Yedekle…"].isEnabled() and not busy["Yedekten geri yükle…"].isEnabled()
    finally:
        window._busy = False
        window.close()


@pytest.fixture
def multi_profile(personal):
    personal.set_pin_hash("never-export-this-pin-hash")
    personal.save_category_prefs("provider", "movie", [("Gizli", True), ("Sinema", False)])
    personal.set_group_locked("provider", "Yetişkin", True)
    personal.set_group_locked("provider", "Açık", False)
    personal.set_channel_locked("provider:catalogue-only", True)
    personal.add_reminder("provider:old-one", "Akşam", 100, 200, 2.5)
    child = personal.create_profile("Çocuk", "#123456", kids=True, protected=True)
    personal.use_profile(child)
    personal.set_favorite("provider:two", True)
    folder = personal.create_folder("Çizgi")
    personal.set_in_folder(folder, "provider:two", True)
    personal.save_progress("provider:two", 25, 90)
    personal.save_category_prefs("provider", "movie", [("Sinema", True)])
    reminder = personal.add_reminder("provider:two", "Çizgi film", 300, 400, 10)
    personal.mark_reminder_notified(reminder)
    personal.use_profile(1)
    return personal


def test_v2_merges_two_profiles_records_flags_and_locks(multi_profile, stores):
    data = export_backup(multi_profile, include_credentials=True)
    assert data["version"] == 2
    assert "never-export-this-pin-hash" not in json.dumps(data)
    target = stores("multi-target")
    target.create_profile("Arada", "#AABBCC")
    child = target.create_profile("çOCUK", "#FFFFFF")
    target.set_pin_hash("keep-target-pin")
    apply_backup(target, data)
    assert len(target.profiles()) == 3
    assert target.profile_id == 1
    assert target.pin_hash() == "keep-target-pin"
    assert target.favorites() == {"provider:old-one"}
    assert target.category_prefs()[("provider", "movie", "Gizli")]["hidden"] is True
    assert target.locked_groups() == {("provider", "Yetişkin")}
    assert target.locked_channels() == {"provider:catalogue-only"}
    assert target.reminders()[0]["lead_minutes"] == 2.5
    target.use_profile(child)
    assert target.profile(child)["kids"] is True
    assert target.profile(child)["protected"] is True
    assert target.profile(child)["color"] == "#123456"
    assert target.favorites() == {"provider:two"}
    assert target.progress("provider:two") == (25, 90)
    assert target.folders()[0][1] == "Çizgi"
    assert target.folder_items(target.folders()[0][0]) == {"provider:two"}
    assert target.category_prefs()[("provider", "movie", "Sinema")]["hidden"] is True
    assert target.reminders()[0]["notified"] == 1
    before = snapshot(target)
    apply_backup(target, data)
    assert snapshot(target) == before


def test_v2_creates_missing_profile_and_omits_history_for_every_profile(multi_profile, stores):
    data = export_backup(multi_profile, include_history=False)
    target = stores("new-profiles")
    apply_backup(target, data)
    child = next(p for p in target.profiles() if p["name"] == "Çocuk")
    target.use_profile(child["id"])
    assert target.favorites() == {"provider:two"}
    assert target.progress_map() == {}
    assert target.reminders()[0]["title"] == "Çizgi film"
    target.use_profile(1)
    assert target.progress_map() == {}


def test_v1_still_imports_into_active_profile(personal, stores):
    data = export_backup(personal)
    for key in ("profiles", "profile_data", "backup_profile_id", "group_locks", "channel_locks"):
        data.pop(key, None)
    data["version"] = 1
    target = stores("legacy")
    child = target.create_profile("Başka", "#123456")
    target.use_profile(child)
    apply_backup(target, data)
    assert target.favorites() == {"provider:old-one"}
    target.use_profile(1)
    assert target.favorites() == set()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["profiles"][0].update(kids=1),
        lambda d: d["profiles"][0].update(protected="yes"),
        lambda d: d["profiles"][0].update(color="red"),
        lambda d: d["profiles"][0].update(position=-1),
        lambda d: d["profiles"][0].update(name="x" * 41),
        lambda d: d["profiles"][0].update(pin_hash="bad"),
        lambda d: d["profiles"][1].update(name="BEN"),
        lambda d: d["profile_data"].update({"999": d["profile_data"]["1"]}),
        lambda d: d["profile_data"]["1"]["reminders"][0].update(end=10),
        lambda d: d["profile_data"]["1"]["reminders"][0].update(lead_minutes=-1),
        lambda d: d["profile_data"]["1"]["reminders"][0].update(notified=1),
        lambda d: d["profile_data"]["1"]["category_prefs"][0].update(source_id="unknown"),
        lambda d: d["profile_data"]["1"]["category_prefs"][0].update(hidden=1),
        lambda d: d["profile_data"]["1"]["category_prefs"][0].update(position=-1),
        lambda d: d["group_locks"][0].update(locked=1),
        lambda d: d["channel_locks"].append("missing"),
    ],
)
def test_v2_malformed_fields_leave_database_unchanged(multi_profile, mutate):
    data = export_backup(multi_profile)
    assert data["version"] == 2
    mutate(data)
    before = snapshot(multi_profile)
    with pytest.raises(ValueError):
        apply_backup(multi_profile, data)
    assert snapshot(multi_profile) == before


def test_v2_late_failure_rolls_back_profiles_and_locks(multi_profile, stores):
    target = stores("multi-rollback")
    target._db.execute("""CREATE TRIGGER fail_locks BEFORE INSERT ON channel_locks
                         BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    before = snapshot(target)
    with pytest.raises(sqlite3.IntegrityError):
        apply_backup(target, export_backup(multi_profile))
    assert snapshot(target) == before
    assert not target._db.in_transaction


def test_v2_avatar_round_trip_when_storage_has_column(personal, stores):
    target = stores("avatar")
    for store in (personal, target):
        with store._db:
            store._db.execute("ALTER TABLE profiles ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
    with personal._db:
        personal._db.execute("UPDATE profiles SET avatar='moon' WHERE id=1")
    data = export_backup(personal)
    assert data["profiles"][0]["avatar"] == "moon"
    apply_backup(target, data)
    assert target._db.execute("SELECT avatar FROM profiles WHERE id=1").fetchone()[0] == "moon"
    data["profiles"][0]["avatar"] = 1
    before = snapshot(target)
    with pytest.raises(ValueError):
        apply_backup(target, data)
    assert snapshot(target) == before


def test_pin_fields_in_settings_are_never_exported_even_with_credentials(personal):
    personal.set_setting("pin_hash", "top-secret-pin")
    personal.set_setting("nested", {"pin": "nested-secret-pin", "safe": True})
    data = export_backup(personal, include_credentials=True)
    encoded = json.dumps(data)
    assert "top-secret-pin" not in encoded
    assert "nested-secret-pin" not in encoded
    assert data["app_settings"]["nested"] == {"safe": True}
    data["app_settings"]["pin_hash"] = "malicious-pin"
    before = snapshot(personal)
    with pytest.raises(ValueError):
        apply_backup(personal, data)
    assert snapshot(personal) == before


def test_v2_rejects_invalid_root_compatibility_copy(personal):
    data = json.loads(json.dumps(export_backup(personal)))
    # JSON equality treats 0 and False as equal, but only a boolean is valid here.
    data["history"][0]["history_hidden"] = int(data["history"][0]["history_hidden"])
    before = snapshot(personal)
    with pytest.raises(ValueError):
        apply_backup(personal, data)
    assert snapshot(personal) == before


def test_v2_rejects_unrepresentable_reminder_dates(multi_profile):
    data = export_backup(multi_profile)
    data["profile_data"]["1"]["reminders"][0].update(
        start=253_402_214_400, end=253_402_214_401, lead_minutes=4_223_370_240
    )
    before = snapshot(multi_profile)
    with pytest.raises(ValueError):
        apply_backup(multi_profile, data)
    assert snapshot(multi_profile) == before


def test_restore_reloads_profile_and_arms_imported_reminders(
    qt_app, personal, stores, tmp_path, monkeypatch, private_auto_refresh
):
    import time

    from luna_iptv.auto_refresh import RefreshScheduler

    monkeypatch.setattr(RefreshScheduler, "start", private_auto_refresh)

    personal.update_profile(1, color="#123456", kids=True)
    now = int(time.time())
    personal.add_reminder("provider:old-one", "Yakında", now + 3600, now + 7200)
    path = tmp_path / "reload.json"
    write_backup(path, export_backup(personal))
    target = stores("reload-window")
    target.set_setting("auto_refresh", "off")
    window = MainWindow(target)
    try:
        window.refresh_scheduler._timer.stop()
        assert not window.reminder_service._timer.isActive()
        monkeypatch.setattr(
            backup_dialog.QFileDialog, "getOpenFileName", lambda *a: (str(path), "")
        )
        monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
        backup_dialog.restore_backup_dialog(window)
        assert window.reminder_service._timer.isActive()
        assert window.refresh_scheduler._timer.isActive()
        assert window.profile_button.profile["color"] == "#123456"
        assert window.profile_button.profile["kids"] is True
        assert len(window.reminder_service.reminders()) == 1
    finally:
        window.close()
