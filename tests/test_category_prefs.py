"""Personal category order, visibility and the guarded editor, without a desktop."""

import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog
from shiboken6 import isValid

from luna_iptv.category_editor import CategoryEditor
from luna_iptv.models import Channel
from luna_iptv.parental import hash_pin
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.save_source({"id": "home", "type": "m3u", "name": "Ev Listesi"})
    value.replace_channels(
        "home",
        [
            Channel("a", "Haber 1", "file:///a", group="Haber"),
            Channel("b", "Spor 1", "file:///b", group="Spor"),
            Channel("c", "Haber 2", "file:///c", group="Haber"),
            Channel("d", "Çocuk 1", "file:///d", group="Çocuk"),
            Channel("e", "Film 1", "file:///e", group="Haber", kind="movie"),
            Channel("f", "Dizi 1", "file:///f", group="Haber", kind="series"),
        ],
    )
    yield value
    value.close()


@pytest.fixture
def window(qt_app, store):
    value = MainWindow(store)
    value.set_section("live")
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def names(window):
    return [window.proxy.index(i, 0).data(Qt.UserRole).name for i in range(window.proxy.rowCount())]


def groups(editor):
    return [group for group, _ in editor.rows()]


def item(editor, group):
    return next(
        editor.list.item(i)
        for i in range(editor.list.count())
        if editor.list.item(i).data(Qt.UserRole) == group
    )


def channel(store, name):
    return next(c for c in store.channels() if c.name == name)


def other_source(store):
    store.save_source({"id": "other", "type": "m3u", "name": "Diğer Liste"})
    store.replace_channels(
        "other",
        [
            Channel("g", "Diğer Haber", "file:///g", group="Haber"),
            Channel("h", "Diğer Spor", "file:///h", group="Spor"),
        ],
    )


def test_storage_order_replace_reset_and_scope(store):
    store.save_category_prefs("home", "live", [("Spor", False), ("Haber", True)])
    store.save_category_prefs("home", "movie", [("Haber", False)])
    other_source(store)
    store.save_category_prefs("other", "live", [("Haber", True)])
    assert store.category_prefs("home") == {
        ("home", "live", "Spor"): {"hidden": False, "position": 0},
        ("home", "live", "Haber"): {"hidden": True, "position": 1},
        ("home", "movie", "Haber"): {"hidden": False, "position": 0},
    }
    store.save_category_prefs("home", "live", [("Çocuk", True)])
    assert ("home", "live", "Spor") not in store.category_prefs()
    store.reset_category_prefs("home", "live")
    assert set(store.category_prefs()) == {("home", "movie", "Haber"), ("other", "live", "Haber")}


def test_storage_profile_isolation_and_cascades(store):
    store.save_category_prefs("home", "live", [("Spor", True)])
    other = store.create_profile("Konuk", "#123456")
    store.use_profile(other)
    assert store.category_prefs() == {}
    store.save_category_prefs("home", "live", [("Haber", True)])
    store.delete_profile(other)
    assert set(store.category_prefs()) == {("home", "live", "Spor")}
    assert store._db.execute("SELECT count(*) FROM category_prefs").fetchone()[0] == 1
    store.remove_source("home")
    assert store.category_prefs() == {}
    assert store._db.execute("SELECT count(*) FROM category_prefs").fetchone()[0] == 0


def test_storage_failed_replace_rolls_back(store):
    store.save_category_prefs("home", "live", [("Spor", True)])
    before = store.category_prefs()
    with pytest.raises(sqlite3.IntegrityError):
        store.save_category_prefs("home", "live", [("Haber", False), ("Haber", True)])
    assert store.category_prefs() == before
    with pytest.raises(ValueError):
        store.save_category_prefs("home", "favorites", [])
    assert store.category_prefs() == before


def test_storage_persists_and_backup_format_is_unchanged(store):
    before = store.backup_records()
    store.save_category_prefs("home", "live", [("Spor", True)])
    assert store.backup_records() == before
    reopened = Store(store.path)
    try:
        assert reopened.category_prefs() == store.category_prefs()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "kind,name", [("live", "Haber 1"), ("movie", "Film 1"), ("series", "Dizi 1")]
)
def test_hidden_grid_chips_popup_search_and_personal_lists(window, store, kind, name):
    selected = channel(store, name)
    store.set_favorite(selected.id, True)
    store.save_progress(selected.id, 30, 300)
    store.save_category_prefs("home", kind, [("Haber", True)])
    window.refresh_library()
    window.set_section(kind)
    assert name not in names(window)
    assert "Haber" not in window.category_bar.buttons()
    assert window.category.findData("Haber") == -1
    popup = window.category_bar.open_popup()
    assert "Haber" not in [popup.list.item(i).data(Qt.UserRole) for i in range(popup.list.count())]
    assert popup.edit_button.text() == "Kategorileri düzenle…"
    popup.close()
    window.search.setText(name)
    assert names(window) == []
    assert window.proxy.search_counts()[kind] == 0
    for personal in ("favorites", "recent"):
        window.set_section(personal)
        assert name in names(window)
        window.search.setText(name)
        assert names(window) == [name]


def test_hidden_is_kind_aware_across_search_sections(window, store):
    store.save_category_prefs("home", "live", [("Haber", True)])
    window.refresh_library()
    window.search.setText("Haber")
    assert names(window) == ["Film 1", "Dizi 1"]
    assert window.proxy.search_counts() == {"live": 0, "movie": 1, "series": 1}
    window.choose_search_kind("movie")
    assert names(window) == ["Film 1"]


def test_order_new_categories_and_refresh_sort(window, store):
    store.save_category_prefs("home", "live", [("Çocuk", False), ("Haber", False)])
    window.refresh_library()
    assert list(window.category_bar.buttons()) == ["", "Çocuk", "Haber", "Spor"]
    assert names(window) == ["Çocuk 1", "Haber 1", "Haber 2", "Spor 1"]
    store.upsert_channels("home", [Channel("new", "Yeni 1", "file:///new", group="Yeni")])
    window.refresh_library()
    assert names(window)[-2:] == ["Spor 1", "Yeni 1"]
    assert list(window.category_bar.buttons())[-2:] == ["Spor", "Yeni"]
    window.choose_category("Haber")
    assert names(window) == ["Haber 1", "Haber 2"]
    window.choose_category("")
    store.save_category_prefs("home", "live", [("Spor", False), ("Haber", False)])
    window.refresh_library()
    assert names(window)[:3] == ["Spor 1", "Haber 1", "Haber 2"]
    store.reset_category_prefs("home", "live")
    window.refresh_library()
    assert names(window) == ["Haber 1", "Spor 1", "Haber 2", "Çocuk 1", "Yeni 1"]


def test_all_sources_use_own_preferences(window, store):
    other_source(store)
    store.save_category_prefs("home", "live", [("Çocuk", False), ("Spor", False), ("Haber", True)])
    store.save_category_prefs("other", "live", [("Haber", False), ("Spor", False)])
    window.refresh_library()
    assert window.proxy.source == ""
    # Each source keeps its place in the list; its own order applies inside it.
    assert names(window) == ["Çocuk 1", "Spor 1", "Diğer Haber", "Diğer Spor"]
    assert "Haber" in window.category_bar.buttons()
    window.source_combo.setCurrentIndex(window.source_combo.findData("home"))
    assert names(window) == ["Çocuk 1", "Spor 1"]
    assert "Haber" not in window.category_bar.buttons()
    window.source_combo.setCurrentIndex(window.source_combo.findData("other"))
    assert names(window) == ["Diğer Haber", "Diğer Spor"]
    assert window.hidden_categories_label.isHidden()


def test_hidden_count_numbering_profile_and_section_changes(window, store):
    store.save_category_prefs("home", "live", [("Haber", True), ("Spor", True)])
    window.refresh_library()
    assert [c.name for c in window.numbered_channels()] == ["Çocuk 1"]
    assert "2 kategori gizli" in window.hidden_categories_label.text()
    assert not window.hidden_categories_label.isHidden()
    window.set_section("movie")
    assert names(window) == ["Film 1"]
    assert window.hidden_categories_label.isHidden()
    guest = store.create_profile("Konuk", "#123456")
    window.switch_profile(guest)
    window.set_section("live")
    assert len(names(window)) == 4
    assert window.hidden_categories_label.isHidden()
    window.switch_profile(1)
    assert names(window) == ["Çocuk 1"]


def test_hiding_selected_category_returns_to_all(window, store):
    window.choose_category("Haber")
    store.save_category_prefs("home", "live", [("Haber", True)])
    window.refresh_library()
    assert window.category.currentData() == "" and window.proxy.group == ""
    assert names(window) == ["Spor 1", "Çocuk 1"]


def test_editor_search_move_save_and_reset(qt_app, store):
    editor = CategoryEditor(store, "home", "live")
    assert editor.subtitle.text() == "Ev Listesi · Canlı TV"
    assert editor.source_combo.isHidden()
    assert editor.list.dragDropMode() == QAbstractItemView.InternalMove
    assert groups(editor) == ["Haber", "Spor", "Çocuk"]
    assert "2" in item(editor, "Haber").text()
    item(editor, "Haber").setCheckState(Qt.Unchecked)
    editor.list.setCurrentItem(item(editor, "Çocuk"))
    editor.up_button.click()
    assert groups(editor) == ["Haber", "Çocuk", "Spor"]
    editor.down_button.click()
    assert groups(editor) == ["Haber", "Spor", "Çocuk"]
    editor.search.setText("cocuk")
    assert item(editor, "Haber").isHidden() and not item(editor, "Çocuk").isHidden()
    assert not editor.up_button.isEnabled() and not editor.down_button.isEnabled()
    editor.search.clear()
    editor.up_button.click()
    editor.save_button.click()
    assert editor.result() == QDialog.Accepted
    assert store.category_prefs()["home", "live", "Haber"]["hidden"]
    assert store.category_prefs()["home", "live", "Çocuk"]["position"] == 1
    again = CategoryEditor(store, "home", "live")
    again.show_all_button.click()
    assert all(not hidden for _, hidden in again.rows())
    again.reset_button.click()
    assert groups(again) == ["Haber", "Spor", "Çocuk"]
    assert store.category_prefs()  # reset is staged until Save
    again.save_button.click()
    assert store.category_prefs() == {}


def test_editor_multiple_source_drafts_and_cancel(qt_app, store):
    other_source(store)
    editor = CategoryEditor(store, "", "live")
    assert not editor.source_combo.isHidden()
    item(editor, "Haber").setCheckState(Qt.Unchecked)
    editor.source_combo.setCurrentIndex(1)
    item(editor, "Spor").setCheckState(Qt.Unchecked)
    editor.source_combo.setCurrentIndex(0)
    assert item(editor, "Haber").checkState() == Qt.Unchecked
    editor.save()
    assert store.category_prefs()["home", "live", "Haber"]["hidden"]
    assert store.category_prefs()["other", "live", "Spor"]["hidden"]
    before = store.category_prefs()
    cancel = CategoryEditor(store, "home", "live")
    cancel.reset()
    cancel.reject()
    assert store.category_prefs() == before


def test_editor_pin_gate_and_save_refresh(window, store, monkeypatch):
    store.set_pin_hash(hash_pin("2468"))
    asked, opened = [], []
    answers = [False, True, True, True]

    def ask(_store, reason, parent=None):
        asked.append(reason)
        return answers.pop(0)

    def edit(dialog):
        opened.append(dialog.kind)
        item(dialog, "Haber").setCheckState(Qt.Unchecked)
        dialog.save()
        return QDialog.Accepted

    monkeypatch.setattr("luna_iptv.window.ask_pin", ask)
    monkeypatch.setattr(CategoryEditor, "exec", edit)
    window.category_bar.edit_button.click()
    assert opened == [] and "Haber 1" in names(window)
    window.category_bar.edit_button.click()
    assert opened == ["live"] and "Haber 1" not in names(window)
    window.hidden_categories_label.linkActivated.emit("edit")
    popup = window.category_bar.open_popup()
    popup.edit_button.click()
    assert len(opened) == 3
    assert asked == ["Kategori düzenini değiştirmek için PIN gir."] * 4


def test_kids_editor_excludes_locked_categories(window, store):
    store.set_pin_hash(hash_pin("2468"))
    store.set_group_locked("home", "Haber", True)
    kid = store.create_profile("Çocuk", "#123456", kids=True)
    window.switch_profile(kid)
    window.set_section("live")
    editor = CategoryEditor(store, "home", "live")
    assert groups(editor) == ["Spor", "Çocuk"]
    editor.show_all()
    editor.save()
    window.refresh_library()
    assert "Haber 1" not in names(window)
    assert "Haber" not in window.category_bar.buttons()


def test_home_keeps_favorites_continue_and_recent(window, store):
    for name in ("Haber 1", "Film 1"):
        selected = channel(store, name)
        store.set_favorite(selected.id, True)
        store.save_progress(selected.id, 30, 300)
    store.save_progress(channel(store, "Haber 2").id, 30, 300)
    for kind in ("live", "movie"):
        store.save_category_prefs("home", kind, [("Haber", True)])
    window.refresh_library()
    window.set_section("home")
    for key in ("favorite_live", "favorite_vod", "continue", "recent_live"):
        assert window.home_view.rows[key].model.rowCount() == 1
    assert window.home_view.summary.text() == "2 canlı kanal · 0 film · 1 dizi"


def test_editor_internal_move_survives_source_switch(qt_app, store):
    from PySide6.QtCore import QModelIndex

    other_source(store)
    editor = CategoryEditor(store, "", "live")
    model = editor.list.model()
    assert model.moveRow(QModelIndex(), 2, QModelIndex(), 0)
    assert groups(editor) == ["Çocuk", "Haber", "Spor"]
    editor.source_combo.setCurrentIndex(1)
    editor.source_combo.setCurrentIndex(0)
    assert groups(editor) == ["Çocuk", "Haber", "Spor"]
    editor.save()
    assert store.category_prefs()["home", "live", "Çocuk"]["position"] == 0


def test_editor_filtered_keyboard_move_preserves_other_rows(qt_app, store):
    editor = CategoryEditor(store, "home", "live")
    editor.search.setText("r")  # Haber and Spor; Çocuk stays out of the search.
    editor.list.setCurrentItem(item(editor, "Spor"))
    editor.up_button.click()
    assert groups(editor) == ["Spor", "Haber", "Çocuk"]
    assert item(editor, "Çocuk").isHidden()
    editor.save()
    assert len(store.category_prefs()) == 3


def test_editor_empty_source_and_unnamed_category(qt_app, store):
    editor = CategoryEditor(store, "home", "series")
    editor.reset()
    editor.save()
    store.save_source({"id": "empty", "type": "m3u", "name": "Boş Liste"})
    empty = CategoryEditor(store, "empty", "live")
    assert empty.rows() == []
    empty.show_all()
    empty.reset()
    empty.save()
    store.upsert_channels("home", [Channel("blank", "Adsız", "file:///blank", group="")])
    unnamed = CategoryEditor(store, "home", "live")
    assert "Kategorisiz" in item(unnamed, "").text()
    item(unnamed, "").setCheckState(Qt.Unchecked)
    unnamed.save()
    assert store.category_prefs()["home", "live", ""]["hidden"]


def test_unordered_chips_use_counts_but_grid_keeps_catalogue_order(window, store):
    store.upsert_channels(
        "home",
        [
            Channel("s2", "Spor 2", "file:///s2", group="Spor"),
            Channel("h3", "Haber 3", "file:///h3", group="Haber"),
        ],
    )
    store.save_category_prefs("home", "live", [("Çocuk", False)])
    window.refresh_library()
    assert list(window.category_bar.buttons()) == ["", "Çocuk", "Haber", "Spor"]
    assert names(window) == ["Çocuk 1", "Haber 1", "Spor 1", "Haber 2", "Spor 2", "Haber 3"]
    window.search.setText("1")
    # Search keeps the chosen category order too: the list itself is ordered.
    assert names(window) == ["Çocuk 1", "Haber 1", "Spor 1", "Film 1", "Dizi 1"]
    window.search.clear()
    assert names(window)[0] == "Çocuk 1"


def test_ordering_is_one_list_sort_not_view_comparisons():
    from luna_iptv.library import ordered_channels

    channels = [
        Channel("a:1", "A1", "u", group="X"),
        Channel("b:1", "B1", "u", group="Y"),
        Channel("a:2", "A2", "u", group="Y"),
        Channel("a:3", "A3", "u", group="X"),
        Channel("a:m", "AM", "u", kind="movie", group="Y"),
    ]
    prefs = {("a", "live", "Y"): {"hidden": False, "position": 0}}
    names = [c.name for c in ordered_channels(channels, prefs)]
    # Source b and the film keep their slots; source a's live channels reorder in place.
    assert names == ["A2", "B1", "A1", "A3", "AM"]
    assert ordered_channels(channels, {}) is channels
