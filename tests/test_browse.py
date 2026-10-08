"""Category chips and one search across live TV, films and series."""

import pytest
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow

GROUPS = {"Haber": 5, "Spor": 3, "Belgesel": 2, "Çocuk": 2, "Müzik": 2, "Yerel": 2, "Din": 2}


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    channels = [
        Channel(f"{group}{n}", f"{group} {n}", "file:///c.ts", group=group)
        for group, total in GROUPS.items()
        for n in range(total)
    ]
    channels += [Channel("rare", "Ay Kanalı", "file:///r.ts", group="Gece")]
    channels += [
        Channel("film", "Ay Işığı", "file:///f.mkv", kind="movie", group="Film"),
        Channel("show", "Ay Masalları", "file:///s", kind="series", group="Dizi", series_id="9"),
        Channel("ep", "Ay Masalları S1", "file:///e.mkv", kind="movie", series_id="9"),
    ]
    store.replace_channels("home", channels)
    value = MainWindow(store)
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def visible_ids(window):
    return {window.proxy.index(r, 0).data(256).id for r in range(window.proxy.rowCount())}


def test_busiest_categories_are_chips_and_filter_the_grid(window):
    chips = window.category_bar.buttons()
    assert list(chips)[:4] == ["", "Haber", "Spor", "Belgesel"]  # "All" then by size
    assert len(chips) == 1 + window.category_bar.limit  # the rest sit behind "Tüm kategoriler"
    assert "Gece" not in chips
    chips["Spor"].click()
    assert window.proxy.rowCount() == 3
    assert window.category.currentData() == "Spor"


def test_rare_category_is_picked_from_the_list_and_joins_the_chips(window, qt_app):
    popup = window.category_bar.open_popup()
    popup.search.setText("gec")
    visible = [
        popup.list.item(r) for r in range(popup.list.count()) if not popup.list.item(r).isHidden()
    ]
    assert [item.data(256) for item in visible] == ["Gece"]
    popup.search.returnPressed.emit()
    assert window.category.currentData() == "Gece"
    assert window.category_bar.buttons()["Gece"].isChecked()
    assert visible_ids(window) == {"home:rare"}


def test_search_reaches_every_section_and_kind_chips_narrow_it(window):
    window.set_section("live")  # Browse is no longer the startup page.
    window.category_bar.buttons()["Spor"].click()
    window.search.setText("ay")
    assert visible_ids(window) == {"home:rare", "home:film", "home:show"}  # no episodes
    assert window.kind_bar.isVisibleTo(window) and not window.category_bar.isVisibleTo(window)
    labels = {value: button.text() for value, button in window.kind_bar.buttons().items()}
    assert labels["live"] == "Canlı  1" and labels["movie"] == "Film  1"
    window.kind_bar.buttons()["movie"].click()
    assert visible_ids(window) == {"home:film"}
    assert window.channel_list.poster_mode
    window.search.clear()
    assert window.category_bar.isVisibleTo(window)
    assert window.proxy.rowCount() == 3  # back to Canlı TV · Spor
    assert not window.channel_list.poster_mode


def test_search_in_favorites_stays_in_favorites(window):
    window.store.set_favorite("home:film", True)
    window.refresh_library()
    window.set_section("favorites")
    window.search.setText("ay")
    assert visible_ids(window) == {"home:film"}


def test_chips_that_do_not_fit_wait_in_the_list_without_squeezing(qt_app):
    from luna_iptv.chips import ChipBar

    bar = ChipBar(more_label="Tüm kategoriler", limit=7)
    items = [(f"g{i}", f"Uzun kategori adı {i}", 10 - i) for i in range(7)]
    bar.resize(560, 30)
    bar.set_items(items, "g6")
    chips = bar.buttons()
    shown = [value for value, button in chips.items() if not button.isHidden()]
    assert shown[0] == "" and "g6" in shown and len(shown) < len(chips)
    assert not bar.more_button.isHidden()
    assert all(b.width() >= b.sizeHint().width() for b in chips.values() if not b.isHidden())
    bar.show()
    bar.resize(3000, 30)
    qt_app.processEvents()
    assert all(not button.isHidden() for button in chips.values())
    assert bar.more_button.isHidden()
    bar.close()
