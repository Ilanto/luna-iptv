"""Source replacement must respect the newer resume/preferences/privacy state."""

from luna_iptv.models import Channel, Playlist
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def test_connection_edit_and_refresh_keep_preferences_and_hidden_history(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    try:
        store.save_source(
            {"id": "one", "type": "direct", "name": "Video", "location": "file:///old.mkv"}
        )
        [channel] = store.replace_channels(
            "one", [Channel("old", "Video", "file:///old.mkv", kind="movie")]
        )
        store.save_progress(channel.id, 40, 100)
        store.clear_history()
        store.save_playback_preferences("one", {"sub": {"mode": "off"}})
        original = store.sources()[0]
        new = Channel("new", "Video", "file:///new.mkv", kind="movie")
        assert store.apply_source_connection(
            original, original | {"location": new.url}, Playlist([new], [], [])
        )
        store.replace_channels("one", [new])
        assert store.recent_ids() == []
        assert store.progress(channel.id) == (40, 100)
        assert store.playback_preferences("one") == {"sub": {"mode": "off"}}
    finally:
        store.close()


def test_removed_current_cannot_restart_from_playback_menu(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    source = store.save_source({"type": "m3u", "name": "Local"})
    [channel] = store.replace_channels(
        source, [Channel("one", "One", "file:///one.mkv", kind="movie")]
    )
    window = MainWindow(store)
    loads = []
    monkeypatch.setattr(window.player, "load", lambda *a, **kw: loads.append(a))
    try:
        window.current = channel
        window._position, window._duration = 40, 100
        store.replace_channels(source, [Channel("two", "Two", "file:///two.mkv", kind="movie")])
        window.refresh_library()
        window.loaded()
        window.restart_current()
        menu = window.build_track_menu()
        assert not menu.actions()[-1].isEnabled()
        assert not loads
        assert store.recent_ids() == []
        window.clear_history(reset_progress=True)
        window.save_progress()
    finally:
        window.close()
        qt_app.processEvents()
