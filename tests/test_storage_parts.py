"""Personal and shared data keep the same boundaries across storage mixins."""

import subprocess
import sys
from datetime import date
from typing import Any, get_type_hints

import pytest

from luna_iptv import storage as storage_module
from luna_iptv.models import Channel
from luna_iptv.storage import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.save_source({"id": "test", "name": "Test", "type": "m3u"})
    value.replace_channels("test", [Channel("one", "Bir", "", group="Haber")])
    yield value
    value.close()


def test_profile_switch_keeps_personal_rows_and_shared_recordings(store):
    first = store.profile_id
    second = store.create_profile("İkinci", "#112233")
    channel = store.channels()[0]
    folder = store.create_folder("Akşam")
    store.set_in_folder(folder, channel.id, True)
    store.save_category_prefs("test", "live", [("Haber", True)])
    store.save_progress(channel.id, 12, 120)
    reminder = store.add_reminder(channel.id, "Haberler", 100, 200)
    recording = store.add_recording(channel, "Haberler", 100, 200, 0)

    store.use_profile(second)
    assert store.favorites() == set()
    assert store.folder_items(folder) == set()
    assert store.category_prefs() == {}
    assert store.progress(channel.id) == (0, 0)
    assert store.reminders() == []
    assert [row["id"] for row in store.recordings()] == [recording]
    store.remove_reminder(reminder)

    store.use_profile(first)
    assert store.favorites() == {channel.id}
    assert store.folder_items(folder) == {channel.id}
    assert store.category_prefs() == {("test", "live", "Haber"): {"hidden": True, "position": 0}}
    assert store.progress(channel.id) == (12, 120)
    assert [row["id"] for row in store.reminders()] == [reminder]
    assert [row["id"] for row in store.recordings()] == [recording]


def test_category_transaction_rolls_back_after_duplicate_group(store):
    store.save_category_prefs("test", "live", [("Haber", True)])
    with pytest.raises(storage_module.sqlite3.IntegrityError):
        store.save_category_prefs("test", "live", [("Yeni", False), ("Yeni", True)])
    assert store.category_prefs() == {("test", "live", "Haber"): {"hidden": True, "position": 0}}


def test_statistics_resolves_date_from_storage_facade_at_call_time(store, monkeypatch):
    channel = store.channels()[0]
    store.add_watch_time(channel.id, "2026-10-09", 60)

    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 9)

    monkeypatch.setattr(storage_module, "date", Today)
    result = store.watch_statistics()
    assert result["week_seconds"] == 60
    assert result["days"][-1] == ("2026-10-09", 60)
    assert result["top"] == [("Bir", 60)]


def test_reminder_annotations_remain_resolvable():
    assert Store.reminders.__annotations__["return"] == "list[dict[str, Any]]"
    assert get_type_hints(Store.reminders)["return"] == list[dict[str, Any]]


def test_storage_mixin_can_be_imported_before_facade_in_fresh_interpreter():
    result = subprocess.run(
        [sys.executable, "-c", "from luna_iptv.storage_parts.library import LibraryMixin"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
