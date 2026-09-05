from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture(scope="session")
def app(qt_app) -> QApplication:
    return qt_app


@pytest.fixture
def provider_server():
    state = {"token": "one", "channel": "news"}
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=10)).strftime("%Y%m%d%H%M%S +0000")
    middle = (now + timedelta(minutes=20)).strftime("%Y%m%d%H%M%S +0000")
    end = (now + timedelta(minutes=50)).strftime("%Y%m%d%H%M%S +0000")
    guide = (
        f'<tv><programme channel="news" start="{start}" stop="{middle}">'
        "<title>Current bulletin</title></programme>"
        f'<programme channel="news" start="{middle}" stop="{end}">'
        "<title>Next bulletin</title></programme></tv>"
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/redirect.m3u":
                self.send_response(302)
                self.send_header("Location", "/nested/list.m3u")
                self.end_headers()
                return
            if parsed.path == "/nested/list.m3u":
                channel_id = state["channel"]
                body = (
                    '#EXTM3U url-tvg="guide.xml"\n'
                    f'#EXTINF:-1 tvg-id="{channel_id}" group-title="News",Test {channel_id.title()}\n'
                    f"stream.ts?token={state['token']}\n"
                ).encode()
            elif parsed.path == "/nested/guide.xml":
                body = guide
            elif parsed.path == "/malformed":
                body = b"<!doctype html><html><body>provider error</body></html>"
            elif parsed.path == "/player_api.php":
                action = query.get("action", [""])[0]
                payload = {
                    "": {"user_info": {"auth": 1}},
                    "get_live_categories": [{"category_id": "1", "category_name": "News"}],
                    "get_vod_categories": [],
                    "get_series_categories": [{"category_id": "2", "category_name": "Drama"}],
                    "get_live_streams": [
                        {
                            "stream_id": 11,
                            "name": "Provider News",
                            "category_id": "1",
                            "epg_channel_id": "news",
                        }
                    ],
                    "get_vod_streams": [],
                    "get_series": [{"series_id": 42, "name": "The Test", "category_id": "2"}],
                    "get_series_info": {
                        "episodes": {
                            "1": [
                                {
                                    "id": 101,
                                    "title": "Pilot",
                                    "episode_num": 1,
                                    "container_extension": "mkv",
                                }
                            ],
                            "2": [
                                {
                                    "id": 201,
                                    "title": "Return",
                                    "episode_num": 1,
                                    "container_extension": "mp4",
                                }
                            ],
                        }
                    },
                }[action]
                body = json.dumps(payload).encode()
            elif parsed.path == "/xmltv.php":
                body = guide
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def window(app: QApplication, tmp_path):
    widget = MainWindow(Store(tmp_path / "data" / "library.sqlite3"))
    yield widget
    if isValid(widget):
        widget.close()
    app.processEvents()


def wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("Timed out waiting for the asynchronous workflow")


def m3u_source(location: str) -> dict[str, str]:
    return {
        "name": "Redirected M3U",
        "type": "m3u",
        "location": location,
        "username": "",
        "password": "",
        "epg_url": "",
    }


def test_async_m3u_redirect_refresh_and_malformed_response_preserve_library(
    app: QApplication, window: MainWindow, provider_server
) -> None:
    base, state = provider_server
    source = m3u_source(base + "/redirect.m3u")
    window.import_source(source)
    wait_until(app, lambda: not window._busy and window.model.rowCount() == 1)
    wait_until(app, lambda: not window._tasks)

    channel = window.model.channels[0]
    assert channel.url == base + "/nested/stream.ts?token=one"
    stored_source = window.store.sources()[0]
    assert stored_source["epg_url"] == base + "/nested/guide.xml"
    window.store.set_favorite(channel.id, True)
    window.store.save_progress(channel.id, 25, 100)

    state["token"] = "two"
    window.import_source(stored_source)
    wait_until(app, lambda: not window._busy and "token=two" in window.model.channels[0].url)
    wait_until(app, lambda: not window._tasks)
    refreshed = window.model.channels[0]
    assert refreshed.id == channel.id
    assert window.store.favorites() == {channel.id}
    assert window.store.progress(channel.id) == (25.0, 100.0)

    invalid = dict(stored_source, location=base + "/malformed")
    window.import_source(invalid)
    wait_until(app, lambda: not window._busy)
    assert window.model.channels == [refreshed]
    assert window.store.sources()[0]["location"] == base + "/redirect.m3u"
    assert "Önceki liste korundu" in window.message.text()


def test_xtream_catalog_episode_seasons_and_movie_filter(
    app: QApplication, window: MainWindow, provider_server
) -> None:
    base, _state = provider_server
    window.import_source(
        {
            "name": "Fixture Xtream",
            "type": "xtream",
            "location": base,
            "username": "fixture-user",
            "password": "fixture-pass",
            "epg_url": "",
        }
    )
    wait_until(app, lambda: not window._busy and window.model.rowCount() == 2)
    wait_until(app, lambda: not window._tasks)

    window.set_section("series")
    assert window.proxy.rowCount() == 1
    series = window.proxy.index(0, 0).data(Qt.UserRole)
    assert series.name == "The Test"
    window.open_series(series)
    wait_until(app, lambda: not window._busy and window._episodes_title == "The Test")

    assert window.proxy.rowCount() == 2
    assert {window.proxy.index(row, 0).data(Qt.UserRole).group for row in range(2)} == {
        "Sezon 1",
        "Sezon 2",
    }
    assert window.category.count() == 3
    episodes = [
        window.proxy.index(row, 0).data(Qt.UserRole) for row in range(window.proxy.rowCount())
    ]
    favorite_episode = episodes[0]
    window.store.set_favorite(favorite_episode.id, True)
    window.store.save_progress(favorite_episode.id, 35, 60)

    stored_source = window.store.sources()[0]
    window.import_source(stored_source)
    wait_until(app, lambda: not window._busy)
    wait_until(app, lambda: not window._tasks)
    assert favorite_episode.id in {channel.id for channel in window.store.channels()}
    assert favorite_episode.id in window.store.favorites()
    assert window.store.progress(favorite_episode.id) == (35.0, 60.0)

    window.set_section("movie")
    assert window.proxy.rowCount() == 0


def test_epg_now_next_updates_the_selected_channel(
    app: QApplication, window: MainWindow, provider_server
) -> None:
    base, _state = provider_server
    window.import_source(m3u_source(base + "/redirect.m3u"))
    wait_until(app, lambda: not window._busy and window.model.rowCount() == 1)
    wait_until(app, lambda: bool(window._guide_data))

    window.current = window.model.channels[0]
    window.update_guide()
    assert "Current bulletin" in window.now_title.text()
    assert "Next bulletin" in window.next_title.text()


def test_confirmed_source_removal_cleans_ui_and_persistence(
    app: QApplication, window: MainWindow, provider_server, monkeypatch
) -> None:
    base, _state = provider_server
    window.import_source(m3u_source(base + "/redirect.m3u"))
    wait_until(app, lambda: not window._busy and window.model.rowCount() == 1)
    wait_until(app, lambda: not window._tasks)
    source = window.store.sources()[0]
    channel = window.model.channels[0]
    window.store.set_favorite(channel.id, True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)

    window.remove_source(source)

    assert window.store.sources() == []
    assert window.store.channels() == []
    assert window.store.favorites() == set()
    assert window.model.rowCount() == 0
    assert "Kaynak kaldırıldı" in window.message.text()


def test_refresh_removing_current_channel_clears_it_before_database_delete(
    app: QApplication, window: MainWindow, provider_server
) -> None:
    base, state = provider_server
    window.import_source(m3u_source(base + "/redirect.m3u"))
    wait_until(app, lambda: not window._busy and window.model.rowCount() == 1)
    wait_until(app, lambda: not window._tasks)
    removed = window.model.channels[0]
    window.current = removed
    window._position = 20
    window._duration = 100

    state["channel"] = "replacement"
    window.import_source(window.store.sources()[0])
    wait_until(app, lambda: not window._busy and window.model.channels[0].tvg_id == "replacement")
    wait_until(app, lambda: not window._tasks)

    assert window.current is None
    assert removed.id not in {channel.id for channel in window.store.channels()}
    assert window.close()


def test_fullscreen_chrome_transitions_without_opening_a_display_surface(
    window: MainWindow, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(window, "showFullScreen", lambda: calls.append("full"))
    monkeypatch.setattr(window, "showNormal", lambda: calls.append("normal"))

    window.toggle_fullscreen()
    assert window._fullscreen is True
    assert window.sidebar.isHidden() and window.library.isHidden()
    window.leave_fullscreen()
    assert window._fullscreen is False
    assert not window.sidebar.isHidden() and not window.library.isHidden()
    assert calls == ["full", "normal"]
