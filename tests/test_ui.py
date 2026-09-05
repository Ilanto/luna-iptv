import pytest
from PySide6.QtCore import Qt
from shiboken6 import isValid

from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture(scope="session")
def app(qt_app):
    return qt_app


@pytest.fixture
def window(app, tmp_path):
    store = Store(tmp_path / "data" / "library.sqlite3")
    widget = MainWindow(store)
    yield widget
    if isValid(widget):
        widget.close()
    app.processEvents()


def test_empty_state(window):
    assert window.model.rowCount() == 0
    assert not window.favorite_button.isEnabled()
    assert "Kaynak" in window.add_button.text()


def test_import_filters_favorites_and_persistence(window):
    source = {"name": "Tests", "type": "m3u", "location": "unused"}
    from luna_iptv.models import Playlist

    window.accept_import(
        source,
        Playlist(
            [
                Channel("a", "Haber", "http://example.test/a", group="Gündem"),
                Channel("b", "Film", "http://example.test/b", group="Sinema", kind="movie"),
            ],
            [],
            [],
        ),
    )
    assert window.model.rowCount() == 2
    assert window.proxy.rowCount() == 1
    window.search.setText("bulunmayan")
    assert window.proxy.rowCount() == 0
    window.search.clear()
    window.set_section("movie")
    assert window.proxy.rowCount() == 1
    channel = window.proxy.index(0, 0).data(Qt.UserRole)
    window.current = channel
    window.toggle_favorite()
    window.set_section("favorites")
    assert window.proxy.rowCount() == 1
    assert channel.id in window.store.favorites()


def test_labels_treat_provider_text_as_plain(window):
    assert window.now_title.textFormat() == Qt.PlainText
    assert window.video_title.textFormat() == Qt.PlainText


def test_stopped_resume_retains_duration_and_play_reloads(window, monkeypatch):
    sid = window.store.save_source({"name": "test", "type": "direct", "location": "/tmp/test.mkv"})
    window.store.replace_channels(sid, [Channel("a", "Video", "/tmp/test.mkv", kind="movie")])
    window.current = window.store.channels()[0]
    window._position = 42
    window._duration = 100
    window.player_property("duration", None)
    assert window._duration == 100
    window.save_progress()
    assert window.store.progress(window.current.id) == (42, 100)
    played = []
    monkeypatch.setattr(window, "play", played.append)
    window._idle = True
    window.toggle_play()
    assert played == [window.current]


def test_refresh_updates_current_url(window):
    from luna_iptv.models import Playlist

    source = {"name": "test", "type": "m3u", "location": "https://example.test/list"}
    window.accept_import(
        source, Playlist([Channel("same", "News", "https://example.test/old")], [], [])
    )
    source = window.store.sources()[0]
    window.current = window.store.channels()[0]
    window.accept_import(
        source, Playlist([Channel("same", "News", "https://example.test/new")], [], [])
    )
    assert window.current.url == "https://example.test/new"


def test_turkish_search_dotted_and_dotless_i(window):
    from luna_iptv.models import Playlist

    window.accept_import(
        {"name": "test", "type": "m3u", "location": "unused"},
        Playlist([Channel("i", "İkinci IŞIK yayını", "https://example.test/live")], [], []),
    )
    window.search.setText("ikinci")
    assert window.proxy.rowCount() == 1
    window.search.setText("ışık")
    assert window.proxy.rowCount() == 1
