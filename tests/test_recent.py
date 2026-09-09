import pytest
from PySide6.QtCore import Qt
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def window(qt_app, tmp_path):
    store = Store(tmp_path / "data" / "library.sqlite3")
    north = store.save_source(
        {
            "id": "north",
            "name": "North",
            "type": "m3u",
            "location": "https://example.test/north.m3u",
        }
    )
    south = store.save_source(
        {
            "id": "south",
            "name": "South",
            "type": "m3u",
            "location": "https://example.test/south.m3u",
        }
    )
    store.replace_channels(
        north,
        [
            Channel("a", "Zebra News", "https://example.test/a", group="News"),
            Channel("b", "Alpha News", "https://example.test/b", group="News"),
            Channel("c", "Metro Film", "https://example.test/c", group="Cinema", kind="movie"),
            Channel("u", "Unwatched News", "https://example.test/u", group="News"),
        ],
    )
    store.replace_channels(
        south,
        [
            Channel("d", "Delta News", "https://example.test/d", group="News"),
            Channel("e", "Echo Sport", "https://example.test/e", group="Sport"),
            Channel("f", "Quiet Film", "https://example.test/f", group="Cinema", kind="movie"),
        ],
    )
    for channel_id in ("north:a", "north:c", "south:d"):
        store.set_favorite(channel_id, True)
    widget = MainWindow(store)
    yield widget
    if isValid(widget):
        widget.close()
    qt_app.processEvents()


@pytest.fixture
def recent_window(window):
    # Watch order deliberately differs from both catalogue and alphabetical order.
    for channel_id in ("north:a", "north:b", "south:d", "north:c", "south:e"):
        window.store.save_progress(channel_id, 10, 100)
    window.set_section("recent")
    return window


def visible_names(window):
    model = window.channel_list.model()
    return [model.index(row, 0).data(Qt.DisplayRole) for row in range(model.rowCount())]


def test_recent_shows_newest_first_across_sources_and_content_types(recent_window):
    assert visible_names(recent_window) == [
        "Echo Sport",
        "Metro Film",
        "Delta News",
        "Alpha News",
        "Zebra News",
    ]


@pytest.mark.parametrize(
    ("filter_name", "value", "expected"),
    [
        ("search", "News", ["Delta News", "Alpha News", "Zebra News"]),
        ("source", "north", ["Metro Film", "Alpha News", "Zebra News"]),
        ("category", "News", ["Delta News", "Alpha News", "Zebra News"]),
    ],
)
def test_recent_filters_preserve_newest_first(recent_window, filter_name, value, expected):
    if filter_name == "search":
        recent_window.search.setText(value)
    else:
        combo = recent_window.source_combo if filter_name == "source" else recent_window.category
        combo.setCurrentIndex(combo.findData(value))
    assert visible_names(recent_window) == expected


def test_refresh_moves_updated_progress_to_the_top_of_recent(window):
    window.store.save_progress("north:b", 10, 100)
    window.store.save_progress("north:a", 20, 100)
    window.set_section("recent")
    assert visible_names(window) == ["Zebra News", "Alpha News"]

    window.store.save_progress("north:b", 30, 100)
    window.refresh_library()

    assert visible_names(window) == ["Alpha News", "Zebra News"]


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ("live", ["Zebra News", "Alpha News", "Unwatched News", "Delta News", "Echo Sport"]),
        ("movie", ["Metro Film", "Quiet Film"]),
        ("favorites", ["Zebra News", "Metro Film", "Delta News"]),
    ],
)
def test_leaving_recent_restores_catalogue_order(recent_window, section, expected):
    recent_window.set_section(section)
    assert visible_names(recent_window) == expected


def test_recent_with_no_watch_history_stays_empty_on_refresh(window):
    window.set_section("recent")
    assert visible_names(window) == []

    window.refresh_library()

    assert visible_names(window) == []
    assert not window.no_results.isHidden()
