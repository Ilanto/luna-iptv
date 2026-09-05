import pytest
from PySide6.QtWidgets import QInputDialog
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "data/library.sqlite3")
    value.save_source(
        {
            "id": "home",
            "name": "Old name",
            "type": "xtream",
            "location": "https://example.test",
            "username": "fixture-user",
            "password": "fixture-password",
            "epg_url": "https://example.test/guide.xml",
        }
    )
    value.replace_channels(
        "home", [Channel("one", "News", "https://example.test/live", group="News")]
    )
    value.set_favorite("home:one", True)
    value.save_progress("home:one", 42, 100)
    yield value
    # MainWindow owns/ closes Store in UI tests.
    value.close()


def test_rename_changes_only_display_name_and_persists(store):
    source_before = store.sources()[0]
    channels_before = store.channels()
    assert store.rename_source("home", "  Evdeki yayınlar  ")
    reopened = Store(store.path)
    try:
        assert reopened.sources() == [source_before | {"name": "Evdeki yayınlar"}]
        assert reopened.channels() == channels_before
        assert reopened.favorites() == {"home:one"}
        assert reopened.progress("home:one") == (42, 100)
        assert reopened.recent_ids() == ["home:one"]
    finally:
        reopened.close()


@pytest.mark.parametrize("name", ["", "   ", "line\nbreak", "nul\x00value", "tab\tvalue"])
def test_rename_rejects_empty_or_control_characters_without_changes(store, name):
    before = store.sources()
    with pytest.raises(ValueError):
        store.rename_source("home", name)
    assert store.sources() == before


def test_unknown_source_is_not_created(store):
    assert not store.rename_source("missing", "Name")
    assert len(store.sources()) == 1


@pytest.mark.parametrize("accepted,name", [(True, "New home"), (False, "Cancelled"), (True, " ")])
def test_rename_ui_preserves_selection_filters_and_playback(
    qt_app, store, monkeypatch, accepted, name
):
    window = MainWindow(store)
    try:
        window.source_combo.setCurrentIndex(window.source_combo.findData("home"))
        window.category.setCurrentIndex(window.category.findData("News"))
        window.search.setText("news")
        window.current = store.channels()[0]
        window._position, window._duration = 42, 100
        current = window.current
        resets, loads, stops, prompts = [], [], [], []
        window.model.modelReset.connect(lambda: resets.append(True))
        monkeypatch.setattr(window.player, "load", lambda *args: loads.append(args))
        monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))

        def answer(*args):
            prompts.append(args[-1])
            return name, accepted

        monkeypatch.setattr(QInputDialog, "getText", answer)
        window.rename_source(store.sources()[0])
        expected = "New home" if accepted and name.strip() else "Old name"
        assert prompts == ["Old name"]
        assert window.source_combo.currentText() == expected
        assert store.sources()[0]["name"] == expected
        assert window.source_combo.currentData() == "home"
        assert window.category.currentData() == "News"
        assert window.search.text() == "news"
        assert window.current is current
        assert (window._position, window._duration) == (42, 100)
        assert not resets and not loads and not stops
    finally:
        if isValid(window):
            window.close()
        qt_app.processEvents()
