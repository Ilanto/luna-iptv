"""Clearing recent history is independent of favorites and resume positions."""

import sqlite3
from contextlib import closing

from luna_iptv.models import Channel
from luna_iptv.storage import Store


def populate(store, name):
    source = store.save_source({"name": name, "type": "m3u"})
    store.replace_channels(source, [Channel("one", name, "file:///fixture.mkv", kind="movie")])
    channel = store.channels(source)[0]
    store.set_favorite(channel.id, True)
    store.save_progress(channel.id, 42, 100)
    return source, channel


def test_clear_history_keeps_resume_favorites_and_other_sources(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    source, one = populate(store, "One")
    _, two = populate(store, "Two")
    store.clear_history(source)
    assert store.recent_ids() == [two.id]
    assert store.progress(one.id) == (42, 100)
    assert store.favorites() == {one.id, two.id}
    store.save_progress(one.id, 48, 100, mark_recent=False)
    assert store.recent_ids() == [two.id]
    assert store.progress(one.id) == (48, 100)
    store.save_progress(one.id, 50, 100)
    assert store.recent_ids() == [one.id, two.id]
    store.close()


def test_clear_all_history_with_reset_persists_after_reopen(tmp_path):
    path = tmp_path / "library.sqlite3"
    store = Store(path)
    _, one = populate(store, "One")
    _, two = populate(store, "Two")
    store.clear_history(reset_progress=True)
    store.close()
    store = Store(path)
    assert store.recent_ids() == []
    assert store.progress(one.id) == (0, 0)
    assert store.progress(two.id) == (0, 0)
    assert store.favorites() == {one.id, two.id}
    assert len(store.sources()) == 2
    store.close()


def test_legacy_progress_migration_keeps_recent_visibility(tmp_path):
    path = tmp_path / "library.sqlite3"
    store = Store(path)
    _, one = populate(store, "One")
    store.close()
    with closing(sqlite3.connect(path)) as db, db:
        if any(row[1] == "history_hidden" for row in db.execute("PRAGMA table_info(progress)")):
            db.execute("ALTER TABLE progress DROP COLUMN history_hidden")
    store = Store(path)
    assert store.recent_ids() == [one.id]
    store.clear_history()
    assert store.recent_ids() == []
    assert store.progress(one.id) == (42, 100)
    store.close()
