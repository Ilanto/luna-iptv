"""Personal data belongs to the selected profile, including migrated libraries."""

import sqlite3

import pytest

from luna_iptv.backup import apply_backup, export_backup
from luna_iptv.models import Channel
from luna_iptv.storage import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "profiles.sqlite3")
    value.save_source({"id": "home", "type": "m3u", "name": "Ev"})
    value.replace_channels("home", [Channel("one", "Bir", ""), Channel("two", "İki", "")])
    yield value
    value.close()


def test_profile_crud_and_persisted_selection(store):
    assert store.profile_id == 1
    assert store.profiles() == [
        dict(
            id=1, name="Ben", color="#E8B04B", kids=False, protected=False, position=0, avatar=None
        )
    ]
    child = store.create_profile("  Çocuk  ", "#123aBC", kids=True, protected=True)
    assert store.profile(child) == dict(
        id=child, name="Çocuk", color="#123aBC", kids=True, protected=True, position=1, avatar=None
    )
    store.update_profile(child, name="Genç", color="#abcdef", kids=False, protected=False)
    assert store.profile(child)["name"] == "Genç"
    assert store.profile(child)["color"] == "#abcdef"
    assert store.profile(child)["kids"] is False
    assert store.profile(child)["protected"] is False
    store.use_profile(child)
    reopened = Store(store.path)
    try:
        assert reopened.profile_id == child
    finally:
        reopened.close()
    store.delete_profile(child)
    assert store.profile(child) is None
    assert store.profile_id == store.setting("active_profile") == 1
    with pytest.raises(ValueError, match="^Son profil silinemez\\.$"):
        store.delete_profile(1)
    for action in (store.use_profile, store.update_profile, store.delete_profile):
        with pytest.raises(ValueError):
            action(999)


@pytest.mark.parametrize("name", ["", "   ", "a" * 41, "\tAd", "Ad\n", "a\x00b", "a\x7fb", " bEN "])
def test_invalid_profile_name_does_not_mutate(store, name):
    second = store.create_profile("Diğer", "#123456")
    before = store.profiles()
    with pytest.raises(ValueError):
        store.create_profile(name, "#123456")
    with pytest.raises(ValueError):
        store.update_profile(second, name=name, color="#ABCDEF")
    assert store.profiles() == before


@pytest.mark.parametrize(
    "color", ["", "red", "#abc", "#1234567", "#abcdeg", " #123456", "#123456\n"]
)
def test_invalid_profile_color_does_not_mutate(store, color):
    before = store.profiles()
    with pytest.raises(ValueError):
        store.create_profile("Yeni", color)
    with pytest.raises(ValueError):
        store.update_profile(1, color=color, name="Yeni")
    assert store.profiles() == before


def test_profile_name_unicode_casefold_and_length(store):
    first = store.create_profile("Çocuk", "#123456")
    with pytest.raises(ValueError):
        store.create_profile("ÇOCUK", "#123456")
    store.update_profile(first, name=" ÇOCUK ")
    assert store.profile(first)["name"] == "ÇOCUK"
    assert store.create_profile("a" * 40, "#123456")


@pytest.mark.parametrize("saved", [999, "missing", None, {}, []])
def test_missing_active_profile_falls_back_to_first_position(store, saved):
    other = store.create_profile("Diğer", "#123456")
    with store._db:
        store._db.execute("UPDATE profiles SET position=-1 WHERE id=?", (other,))
    store.set_setting("active_profile", saved)
    reopened = Store(store.path)
    try:
        assert reopened.profile_id == other
    finally:
        reopened.close()


def test_personal_data_isolation_and_foreign_folder_guards(store):
    folder_a = store.create_folder("Akşam")
    store.set_in_folder(folder_a, "home:one", True)
    store.save_progress("home:one", 12, 100)
    reminder_a = store.add_reminder("home:one", "Bir", 100, 200)
    other = store.create_profile("Diğer", "#123456")
    store.use_profile(other)
    assert store.favorites() == set()
    assert store.folders() == []
    assert store.folder_items(folder_a) == set()
    assert store.folders_of("home:one") == set()
    assert store.progress("home:one") == (0, 0)
    assert store.progress_map() == {}
    assert store.recent_ids() == []
    assert store.reminders() == []
    assert not store.rename_folder(folder_a, "Başka")
    store.delete_folder(folder_a)
    store.set_in_folder(folder_a, "home:two", True)
    store.set_in_folder(folder_a, "home:one", False)
    store.mark_reminder_notified(reminder_a)
    store.remove_reminder(reminder_a)
    assert store.favorites() == set()
    folder_b = store.create_folder("Akşam")
    with pytest.raises(ValueError):
        store.create_folder("AKŞAM")
    store.set_in_folder(folder_b, "home:one", True)
    store.save_progress("home:one", 35, 100)
    reminder_b = store.add_reminder("home:one", "İki", 100, 200)
    assert reminder_a != reminder_b
    assert store.add_reminder("home:one", "Tekrar", 100, 200) == reminder_b
    store.mark_reminder_notified(reminder_b)
    assert store.reminders()[0]["notified"] == 1
    store.set_favorite("home:one", False)
    assert store.folder_items(folder_b) == set()
    store.clear_history(reset_progress=True)
    assert store.progress_map() == {}
    store.use_profile(1)
    assert store.favorites() == {"home:one"}
    assert store.folders() == [(folder_a, "Akşam")]
    assert store.folder_items(folder_a) == {"home:one"}
    assert store.progress_map() == {"home:one": (12, 100)}
    assert store.recent_ids() == ["home:one"]
    assert store.reminders()[0]["id"] == reminder_a
    assert store.reminders()[0]["notified"] == 0


def test_progress_order_and_scoped_history_reset(store):
    for _ in range(3):
        store.save_progress("home:one", 12, 100)
    other = store.create_profile("Diğer", "#123456")
    store.use_profile(other)
    store.save_progress("home:two", 20, 100)
    assert store.backup_records()["history"][0]["updated_at"] == 1
    store.save_progress("home:one", 30, 100, mark_recent=False)
    assert store.recent_ids() == ["home:two"]
    store.save_progress("home:one", 40, 100)
    assert store.recent_ids(1) == ["home:one"]
    store.clear_history("home")
    assert store.recent_ids() == []
    assert store.progress("home:one") == (40, 100)
    store.use_profile(1)
    assert store.recent_ids() == ["home:one"]


def test_delete_profile_cascades_only_personal_rows(store):
    folder = store.create_folder("Akşam")
    store.set_in_folder(folder, "home:one", True)
    store.save_progress("home:one", 12, 100)
    store.add_reminder("home:one", "Bir", 100, 200)
    other = store.create_profile("Diğer", "#123456")
    store.use_profile(other)
    store.set_favorite("home:one", True)
    store.delete_profile(1)
    for table in ("favorites", "favorite_folders", "progress", "reminders"):
        assert store._db.execute(f"SELECT * FROM {table} WHERE profile_id=1").fetchall() == []
    assert store._db.execute("SELECT * FROM favorite_folder_items").fetchall() == []
    assert store.favorites() == {"home:one"}
    assert len(store.channels()) == 2
    assert store._db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_exports_and_restores_all_profiles_without_switching_active(store):
    store.set_favorite("home:one", True)
    store.save_progress("home:one", 12, 100)
    other = store.create_profile("Diğer", "#123456")
    store.use_profile(other)
    folder = store.create_folder("Akşam")
    store.set_in_folder(folder, "home:two", True)
    store.save_progress("home:two", 35, 100)
    data = export_backup(store)
    assert data["version"] == 2
    assert data["profile_data"]["1"]["favorites"] == ["home:one"]
    assert data["profile_data"][str(other)]["favorites"] == ["home:two"]
    assert data["favorites"] == ["home:two"]
    assert [row["channel_id"] for row in data["history"]] == ["home:two"]
    assert data["favorite_folders"][0]["members"] == ["home:two"]
    assert "active_profile" not in data["app_settings"]
    target = store.create_profile("Hedef", "#123456")
    store.use_profile(target)
    data["app_settings"]["active_profile"] = 1
    apply_backup(store, data)
    apply_backup(store, data)
    assert store.profile_id == store.setting("active_profile") == target
    assert store.favorites() == set()
    assert store.progress_map() == {}
    assert store.folders() == []
    store.use_profile(other)
    assert store.favorites() == {"home:two"}
    assert store.progress_map() == {"home:two": (35, 100)}
    assert store.folder_items(store.folders()[0][0]) == {"home:two"}
    store.use_profile(1)
    assert store.favorites() == {"home:one"}
    assert store.progress_map() == {"home:one": (12, 100)}
    assert store.folders() == []


# Deliberately independent of Store's schema: this is the pre-profile database.
OLD_SCHEMA = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, location TEXT NOT NULL,
    username TEXT NOT NULL, password TEXT NOT NULL, epg_url TEXT NOT NULL
);
CREATE TABLE channels (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    name TEXT NOT NULL, url TEXT NOT NULL, group_name TEXT NOT NULL, tvg_id TEXT NOT NULL,
    logo TEXT NOT NULL, kind TEXT NOT NULL, series_id TEXT NOT NULL, headers TEXT NOT NULL,
    provider_key TEXT NOT NULL DEFAULT ''
);
CREATE TABLE favorites (
    channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE
);
CREATE TABLE favorite_folders (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL
);
CREATE TABLE favorite_folder_items (
    folder_id INTEGER REFERENCES favorite_folders(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL, PRIMARY KEY(folder_id, channel_id)
);
CREATE TRIGGER favorite_folder_cleanup AFTER DELETE ON favorites BEGIN
    DELETE FROM favorite_folder_items WHERE channel_id = OLD.channel_id;
END;
CREATE TABLE progress (
    channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    position REAL NOT NULL, duration REAL NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0,
    history_hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL, title TEXT NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL, notified INTEGER NOT NULL DEFAULT 0,
    lead_minutes REAL NOT NULL DEFAULT 5, UNIQUE(channel_id, start)
);
"""


def test_old_schema_migrates_all_rows_and_reopening_is_noop(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA)
        db.execute("INSERT INTO sources VALUES('home','Ev','m3u','','','','')")
        for identity in ("one", "two"):
            db.execute(
                "INSERT INTO channels VALUES(?, 'home', ?, '', '', '', '', 'live', '', '{}', '')",
                (f"home:{identity}", identity),
            )
            db.execute("INSERT INTO favorites VALUES(?)", (f"home:{identity}",))
        db.executemany(
            "INSERT INTO favorite_folders VALUES(?,?,?)", [(7, "Akşam", 3), (9, "Film", 5)]
        )
        db.executemany(
            "INSERT INTO favorite_folder_items VALUES(?,?)", [(7, "home:one"), (9, "home:two")]
        )
        db.executemany(
            "INSERT INTO progress VALUES(?,?,?,?,?)",
            [("home:one", 12.5, 100, 8, 0), ("home:two", 35, 200, 9, 1)],
        )
        db.executemany(
            "INSERT INTO reminders VALUES(?,?,?,?,?,?,?)",
            [(4, "home:one", "Bir", 100, 200, 1, 2.5), (8, "home:two", "İki", 300, 400, 0, 5)],
        )
        tables = ("favorites", "favorite_folders", "progress", "reminders")
        old = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    store = Store(path)
    try:
        assert store.profile_id == 1
        for table in tables:
            columns = [row[1] for row in store._db.execute(f"PRAGMA table_info({table})")]
            legacy_columns = ",".join(column for column in columns if column != "profile_id")
            assert (
                store._db.execute(
                    f"SELECT {legacy_columns} FROM {table} WHERE profile_id=1"
                ).fetchall()
                == old[table]
            )
        assert store.folder_items(7) == {"home:one"}
        assert store.folder_items(9) == {"home:two"}
        assert store._db.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert store._db.execute("PRAGMA foreign_key_check").fetchall() == []
        snapshot = list(store._db.iterdump())
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert list(reopened._db.iterdump()) == snapshot
    finally:
        reopened.close()


def test_failed_migration_rolls_back_schema_and_personal_rows(tmp_path, monkeypatch):
    path = tmp_path / "rollback.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA)
        db.execute("INSERT INTO sources VALUES('home','Ev','m3u','','','','')")
        db.execute(
            "INSERT INTO channels VALUES('home:one','home','Bir','','','','','live','','{}','')"
        )
        db.execute("INSERT INTO favorites VALUES('home:one')")
        db.execute("INSERT INTO favorite_folders VALUES(7,'Akşam',0)")
        db.execute("INSERT INTO favorite_folder_items VALUES(7,'home:one')")
        db.execute("INSERT INTO progress VALUES('home:one',12,100,8,0)")
        db.execute("INSERT INTO reminders VALUES(4,'home:one','Bir',100,200,1,2.5)")
        before = list(db.iterdump())

    def fail_after_rebuild(self):
        assert self._db.in_transaction
        assert self._db.execute("SELECT profile_id FROM favorites").fetchone() == (1,)
        assert self._db.execute("PRAGMA foreign_keys").fetchone() == (0,)
        raise sqlite3.OperationalError("forced migration failure")

    with monkeypatch.context() as patch:
        patch.setattr(Store, "_backfill_all_provider_keys", fail_after_rebuild)
        with pytest.raises(RuntimeError, match="forced migration failure"):
            Store(path)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
    reopened = Store(path)
    try:
        assert reopened.favorites() == {"home:one"}
        assert reopened.folder_items(7) == {"home:one"}
        assert reopened.progress("home:one") == (12, 100)
        assert reopened.reminders()[0]["id"] == 4
    finally:
        reopened.close()


def test_profile_one_is_not_recreated_after_deletion_and_reopen(store):
    other = store.create_profile("Diğer", "#123456")
    store.delete_profile(1)
    reopened = Store(store.path)
    try:
        assert reopened.profile_id == other
        assert reopened.profile(1) is None
        assert [item["id"] for item in reopened.profiles()] == [other]
    finally:
        reopened.close()
