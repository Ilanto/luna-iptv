"""Favorite folders stay subsets of favorites across storage, search and menus."""

import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from shiboken6 import isValid

from luna_iptv.chips import ChipBar
from luna_iptv.library import ChannelFilter, ChannelModel
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.save_source({"id": "home", "name": "Ev", "type": "m3u"})
    value.replace_channels(
        "home",
        [
            Channel("news", "Ay Haber", "file:///news.ts", group="Haber"),
            Channel("sport", "Ay Spor", "file:///sport.ts", group="Spor"),
            Channel("film", "Ay Filmi", "file:///film.mkv", kind="movie"),
        ],
    )
    yield value
    value.close()


def test_folder_crud_order_and_persistence(store):
    first = store.create_folder("  Haber  ")
    second = store.create_folder("Çocuk")
    assert store.folders() == [(first, "Haber"), (second, "Çocuk")]
    assert store.rename_folder(first, " Gündem ")
    assert not store.rename_folder(-1, "Yok")
    store.set_in_folder(first, "home:news", True)
    reopened = Store(store.path)
    try:
        assert reopened.folders() == [(first, "Gündem"), (second, "Çocuk")]
        assert reopened.folder_items(first) == {"home:news"}
        assert reopened.folders_of("home:news") == {first}
    finally:
        reopened.close()
    store.delete_folder(first)
    third = store.create_folder("Spor")
    assert store.folders() == [(second, "Çocuk"), (third, "Spor")]
    assert store.folder_items(first) == set()
    assert store.favorites() == {"home:news"}


@pytest.mark.parametrize("name", ["", "  ", "a\nb", "\tHaber", "a\x00b", "a\x7fb"])
def test_invalid_names_are_rejected_without_changes(store, name):
    folder = store.create_folder("Haber")
    with pytest.raises(ValueError):
        store.create_folder(name)
    with pytest.raises(ValueError):
        store.rename_folder(folder, name)
    assert store.folders() == [(folder, "Haber")]


def test_duplicate_names_use_unicode_casefold(store):
    first = store.create_folder("Çocuk")
    second = store.create_folder("Spor")
    with pytest.raises(ValueError):
        store.create_folder(" ÇOCUK ")
    with pytest.raises(ValueError):
        store.rename_folder(second, "çocuk")
    assert store.rename_folder(first, "ÇOCUK")
    assert store.folders() == [(first, "ÇOCUK"), (second, "Spor")]


def test_membership_and_favorite_removal_are_atomic(store):
    first = store.create_folder("Haber")
    second = store.create_folder("Spor")
    for folder in (first, second):
        store.set_in_folder(folder, "home:news", True)
        store.set_in_folder(folder, "home:news", True)
    assert store.favorites() == {"home:news"}
    assert store.folders_of("home:news") == {first, second}
    store.set_in_folder(first, "home:news", False)
    assert store.favorites() == {"home:news"}
    assert store.folders_of("home:news") == {second}
    store.set_favorite("home:news", False)
    assert store.folders_of("home:news") == set()
    assert store.favorites() == set()
    with pytest.raises(sqlite3.IntegrityError):
        store.set_in_folder(-1, "home:sport", True)
    with pytest.raises(sqlite3.IntegrityError):
        store.set_in_folder(first, "missing", True)
    assert store.favorites() == set()
    assert store.folder_items(first) == set()


def test_channel_removal_cleans_memberships(store):
    folder = store.create_folder("Haber")
    store.set_in_folder(folder, "home:news", True)
    store.replace_channels("home", [])
    assert store.favorites() == set()
    assert store.folder_items(folder) == set()


def test_folder_filter_composes_with_favorites_search_source_and_kind(store, qt_app):
    model = ChannelModel()
    model.reset(store.channels(), {"home:news", "home:film"})
    proxy = ChannelFilter()
    proxy.setSourceModel(model)
    assert proxy.folder_ids is None
    proxy.folder_ids = {"home:news", "home:sport"}
    proxy.section = "favorites"
    proxy.refresh()
    assert proxy.rowCount() == 1
    proxy.query = "ay"
    proxy.refresh()
    assert proxy.rowCount() == 1
    assert proxy.search_counts() == {"live": 1, "movie": 0, "series": 0}
    proxy.kind = "movie"
    proxy.refresh()
    assert proxy.rowCount() == 0
    proxy.kind = ""
    proxy.source = "away"
    proxy.refresh()
    assert proxy.rowCount() == 0
    proxy.source = ""
    proxy.folder_ids = set()
    proxy.refresh()
    assert proxy.rowCount() == 0
    proxy.folder_ids = None
    proxy.refresh()
    assert proxy.rowCount() == 2
    proxy.folder_ids = set()
    proxy.section = "live"
    proxy.refresh()
    assert proxy.rowCount() == 3
    proxy.query = ""
    proxy.refresh()
    assert proxy.rowCount() == 2


def test_ordered_chips_use_explicit_total_for_overlapping_folders(qt_app):
    bar = ChipBar(limit=None, show_counts=True, sort_by_count=False)
    bar.set_items([("1", "Z", 1), ("2", "A", 3)], total=3)
    assert list(bar.buttons()) == ["", "1", "2"]
    assert bar.buttons()[""].text() == "Tümü  3"
    bar.set_current("1")
    assert bar.buttons()[""].text() == "Tümü  3"
    bar.deleteLater()


@pytest.fixture
def window(qt_app, store):
    value = MainWindow(store)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def visible_ids(window):
    return {
        window.proxy.index(row, 0).data(Qt.UserRole).id for row in range(window.proxy.rowCount())
    }


def test_folder_chips_search_and_leaving_favorites(window):
    first = window.store.create_folder("Z Haber")
    second = window.store.create_folder("A Genel")
    window.store.set_in_folder(first, "home:news", True)
    window.store.set_in_folder(second, "home:news", True)
    window.store.set_in_folder(second, "home:film", True)
    window.refresh_library()
    window.choose_category("Spor")
    window.set_section("favorites")
    assert not window.category_bar.isVisibleTo(window)
    assert window.folder_bar.isVisibleTo(window)
    chips = window.folder_bar.buttons()
    assert list(chips) == ["", str(first), str(second)]
    assert chips[""].text() == "Tümü  2"
    assert visible_ids(window) == {"home:news", "home:film"}
    chips[str(first)].click()
    window.search.setText("ay")
    assert window.folder_bar.isVisibleTo(window)
    assert visible_ids(window) == {"home:news"}
    window.set_section("live")
    assert window.proxy.folder_ids is None
    assert not window.folder_bar.isVisibleTo(window)
    window.set_section("favorites")
    assert visible_ids(window) == {"home:news", "home:film"}


def test_channel_menu_membership_updates_model_star_and_active_folder(window):
    folder = window.store.create_folder("Haber")
    channel = next(c for c in window.model.channels if c.id == "home:news")
    window.current = channel
    menu = window.build_channel_menu(channel)
    assert menu.actions()[0].text() == "Favorilere ekle"
    submenu = menu.actions()[1].menu()
    action = submenu.actions()[0]
    assert action.isCheckable() and not action.isChecked()
    action.trigger()
    assert window.store.folder_items(folder) == {channel.id}
    assert window.model.index(0).data(Qt.UserRole + 1)
    assert window.favorite_button.text() == "★"
    window.set_section("favorites")
    window.folder_bar.buttons()[str(folder)].click()
    menu = window.build_channel_menu(channel)
    assert menu.actions()[0].text() == "Favorilerden çıkar"
    menu.actions()[0].trigger()
    assert not window.model.index(0).data(Qt.UserRole + 1)
    assert window.favorite_button.text() == "☆"
    assert window.store.folder_items(folder) == set()
    assert visible_ids(window) == set()
    assert window.folder_bar.buttons()[str(folder)].text() == "Haber  0"
    assert window.channel_list.contextMenuPolicy() == Qt.CustomContextMenu
    window.channel_context_menu(QPoint(-1, -1))


def test_create_rename_delete_folder_through_ui(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Haber", True))
    window.new_folder_button.click()
    folder = window.store.folders()[0][0]
    channel = window.model.channels[0]
    window.set_folder_membership(folder, channel, True)
    window.set_section("favorites")
    window.folder_bar.buttons()[str(folder)].click()
    menu = window.build_folder_menu(folder)
    assert [a.text() for a in menu.actions()] == ["Yeniden adlandır…", "Sil"]
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Gündem", True))
    menu.actions()[0].trigger()
    assert window.store.folders() == [(folder, "Gündem")]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No)
    menu.actions()[1].trigger()
    assert window.store.folders() == [(folder, "Gündem")]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    menu.actions()[1].trigger()
    assert window.store.folders() == []
    assert window.proxy.folder_ids is None
    assert visible_ids(window) == {channel.id}
    assert window.store.favorites() == {channel.id}


def test_membership_toggle_preserves_current_card_and_favorite_when_unchecked(window):
    folder = window.store.create_folder("Haber")
    index = window.proxy.index(0, 0)
    channel = index.data(Qt.UserRole)
    window.channel_list.setCurrentIndex(index)
    window.set_folder_membership(folder, channel, True)
    assert window.channel_list.currentIndex().data(Qt.UserRole).id == channel.id
    assert window.channel_list.selectionModel().selectedIndexes() == [index]
    action = window.build_channel_menu(channel).actions()[1].menu().actions()[0]
    assert action.isChecked()
    action.trigger()
    assert window.store.folder_items(folder) == set()
    assert channel.id in window.model.favorites
    assert window.channel_list.currentIndex().data(Qt.UserRole).id == channel.id


def test_new_folder_from_channel_menu_and_invalid_or_cancelled_names(window, monkeypatch):
    channel = window.model.channels[0]
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Spor", True))
    window.build_channel_menu(channel).actions()[1].menu().actions()[-1].trigger()
    folder = window.store.folders()[0][0]
    assert window.store.folder_items(folder) == {channel.id}
    assert channel.id in window.model.favorites
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    window.create_folder()
    assert len(warnings) == 1
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("İptal", False))
    window.create_folder()
    assert window.store.folders() == [(folder, "Spor")]
