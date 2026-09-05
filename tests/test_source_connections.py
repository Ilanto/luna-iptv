from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtWidgets import QDialog
from shiboken6 import isValid

from luna_iptv.accounts import AccountProfile
from luna_iptv.dialogs import SourceDialog
from luna_iptv.models import Channel, Playlist
from luna_iptv.network import XtreamClient
from luna_iptv.source_connections import (
    HealthResult,
    check_connection,
    retarget_cached_episodes,
    validate_candidate,
)
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def xtream_source(base: str, *, user: str = "old-user", password: str = "old-pass"):
    return {
        "id": "provider",
        "name": "Provider",
        "type": "xtream",
        "location": base,
        "username": user,
        "password": password,
        "epg_url": "",
    }


@pytest.fixture
def provider_server():
    requests: list[tuple[str, str, int]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            requests.append(("HEAD", self.path, 0))
            if self.path == "/head-unsupported":
                self.send_response(405)
            else:
                self.send_response(204)
            self.end_headers()

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            action = query.get("action", [""])[0]
            if parsed.path == "/list.m3u":
                body = b"#EXTM3U\n#EXTINF:-1,One\nhttp://stream.invalid/one.ts\n" + b"x" * 9000
            elif parsed.path == "/player_api.php":
                payload = {
                    "": {"user_info": {"auth": 1, "status": "Active"}},
                    "get_live_categories": [{"category_id": "1", "category_name": "News"}],
                    "get_vod_categories": [],
                    "get_series_categories": [{"category_id": "2", "category_name": "Shows"}],
                    "get_live_streams": [{"stream_id": 11, "name": "One", "category_id": "1"}],
                    "get_vod_streams": [],
                    "get_series": [{"series_id": 42, "name": "Show", "category_id": "2"}],
                    "get_series_info": {
                        "episodes": {
                            "1": [
                                {
                                    "id": 101,
                                    "title": "Pilot",
                                    "episode_num": 1,
                                    "container_extension": "mkv",
                                }
                            ]
                        }
                    },
                }[action]
                body = json.dumps(payload).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            requests.append(("GET", self.path, len(body)))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_xtream_provider_identity_is_independent_of_host_and_credentials(provider_server):
    base, _requests = provider_server
    first = XtreamClient(base, "first-user", "first-pass")
    second = XtreamClient(base + "/", "second-user", "second-pass")

    first_catalog = first.catalog().channels
    second_catalog = second.catalog().channels
    assert [(c.id, c.provider_key) for c in first_catalog] == [
        (c.id, c.provider_key) for c in second_catalog
    ]
    assert {c.provider_key for c in first_catalog} == {"live:11", "series:42"}

    first_episode = first.episodes("42")[0]
    second_episode = second.episodes("42")[0]
    assert (first_episode.id, first_episode.provider_key) == (
        second_episode.id,
        second_episode.provider_key,
    )
    assert first_episode.provider_key == "episode:42:101"
    assert "first-user" in first_episode.url
    assert "second-user" in second_episode.url


def test_atomic_connection_edit_preserves_legacy_ids_favorite_progress_and_episodes(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    old = xtream_source("https://old.invalid")
    store.save_source(old)
    legacy_parent = Channel("legacy-series", "Show", "", kind="series", series_id="42")
    legacy_episode = Channel(
        "legacy-episode",
        "Pilot",
        "https://old.invalid/series/old-user/old-pass/101.mkv",
        kind="movie",
        series_id="42",
    )
    store.replace_channels("provider", [legacy_parent, legacy_episode])
    store.set_favorite("provider:legacy-episode", True)
    store.save_progress("provider:legacy-episode", 33, 60)
    store.save_source_health("provider", "available", 1_700_000_000)

    candidate = xtream_source("https://new.invalid", user="new-user", password="new-pass")
    playlist = Playlist(
        [
            Channel(
                "new-parent",
                "Show updated",
                "",
                kind="series",
                series_id="42",
                provider_key="series:42",
            )
        ],
        [],
        [],
        AccountProfile("active", None, None, 1, 2, 1_700_000_001),
    )
    assert store.apply_source_connection(old, candidate, playlist)
    assert store.source_health("provider") is None
    assert store.account_profile("provider") == AccountProfile(
        "active", None, None, 1, 2, 1_700_000_001
    )

    channels = store.channels("provider")
    assert {channel.id for channel in channels} == {
        "provider:legacy-series",
        "provider:legacy-episode",
    }
    episode = next(c for c in channels if c.kind == "movie")
    assert episode.provider_key == "episode:42:101"
    assert episode.url == "https://new.invalid/series/new-user/new-pass/101.mkv"
    assert "old-user" not in episode.url and "old-pass" not in episode.url
    assert store.favorites() == {"provider:legacy-episode"}
    assert store.progress("provider:legacy-episode") == (33, 60)

    # A later catalogue refresh and episode fetch continue to map provider IDs.
    store.replace_channels(
        "provider",
        [
            Channel(
                "another-parent",
                "Show refreshed",
                "",
                kind="series",
                series_id="42",
                provider_key="series:42",
            ),
            episode,
        ],
    )
    stored_episode = store.upsert_channels(
        "provider",
        [
            Channel(
                "another-episode",
                "Pilot refreshed",
                "https://new.invalid/series/new-user/new-pass/101.mkv",
                kind="movie",
                series_id="42",
                provider_key="episode:42:101",
            )
        ],
    )[0]
    assert stored_episode.id == "provider:legacy-episode"
    assert store.favorites() == {"provider:legacy-episode"}
    assert store.progress("provider:legacy-episode") == (33, 60)


def test_connection_edit_rolls_back_source_catalog_and_profile_on_database_error(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "library.sqlite3")
    old = xtream_source("https://old.invalid")
    store.save_source(old)
    store.replace_channels(
        "provider", [Channel("old", "Old", "https://old.invalid/1", provider_key="live:1")]
    )
    old_profile = AccountProfile("active", None, None, 0, 1, 1_700_000_000)
    store.save_account_profile("provider", old_profile)
    before_source = store.sources()
    before_channels = store.channels()
    before_profile = store.account_profile("provider")

    def fail(_rows):
        raise RuntimeError("fixture write failure")

    monkeypatch.setattr(store, "_upsert_channel_rows", fail)
    candidate = xtream_source("https://new.invalid", user="new", password="secret")
    with pytest.raises(RuntimeError, match="fixture write failure"):
        store.apply_source_connection(
            old,
            candidate,
            Playlist(
                [Channel("new", "New", "https://new.invalid/1", provider_key="live:1")],
                [],
                [],
                AccountProfile("active", None, None, 1, 1, 1_700_000_002),
            ),
        )

    assert store.sources() == before_source
    assert store.channels() == before_channels
    assert store.account_profile("provider") == before_profile == old_profile


def test_stale_or_deleted_source_cannot_apply_candidate(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    expected = xtream_source("https://old.invalid")
    store.save_source(expected)
    store.replace_channels("provider", [Channel("old", "Old", "https://old.invalid/1")])
    candidate = xtream_source("https://new.invalid")
    playlist = Playlist([Channel("new", "New", "https://new.invalid/1")], [], [])

    store.rename_source("provider", "Concurrent rename")
    assert not store.apply_source_connection(expected, candidate, playlist)
    assert store.sources()[0]["location"] == "https://old.invalid"

    current = store.sources()[0]
    store.remove_source("provider")
    assert not store.apply_source_connection(current, candidate, playlist)
    assert store.sources() == [] and store.channels() == []


def test_unparseable_cached_episode_drops_old_secret_url_but_keeps_metadata():
    candidate = xtream_source("https://new.invalid", user="new", password="new-pass")
    cached = Channel(
        "episode",
        "Odd episode",
        "https://old.invalid/not-an-xtream-path?password=old-pass",
        group="Season 1",
        kind="movie",
        series_id="42",
    )
    [safe] = retarget_cached_episodes(candidate, [cached])
    assert safe.id == cached.id and safe.name == cached.name and safe.group == cached.group
    assert safe.url == ""
    assert "old-pass" not in repr(safe)


def test_reopen_backfills_legacy_xtream_identity_without_losing_user_state(tmp_path):
    path = tmp_path / "migration.sqlite3"
    store = Store(path)
    store.save_source(xtream_source("https://old.invalid"))
    store.replace_channels(
        "provider",
        [
            Channel(
                "legacy",
                "One",
                "https://old.invalid/live/old-user/old-pass/11.ts",
            )
        ],
    )
    store.set_favorite("provider:legacy", True)
    store.save_progress("provider:legacy", 8, 20)
    store.close()
    with sqlite3.connect(path) as database:
        database.execute("DROP INDEX channels_provider_key_idx")
        database.execute("UPDATE channels SET provider_key='' WHERE id='provider:legacy'")

    reopened = Store(path)
    try:
        [channel] = reopened.channels("provider")
        assert channel.id == "provider:legacy"
        assert channel.provider_key == "live:11"
        assert reopened.favorites() == {"provider:legacy"}
        assert reopened.progress("provider:legacy") == (8, 20)
    finally:
        reopened.close()


def test_validation_does_not_mutate_store_and_health_uses_bounded_kind_specific_probe(
    tmp_path, provider_server
):
    base, requests = provider_server
    store = Store(tmp_path / "library.sqlite3")
    source = xtream_source(base, user="fixture", password="secret")
    store.save_source(source)

    playlist = validate_candidate(source)
    assert len(playlist.channels) == 2
    assert store.channels() == []

    before_xtream_health = len(requests)
    xtream_health = check_connection(source, checked_at=123)
    assert xtream_health.status == "available" and xtream_health.checked_at == 123
    assert len(requests) == before_xtream_health + 1
    assert parse_qs(urlsplit(requests[-1][1]).query).get("action") is None
    before = len(requests)
    direct_health = check_connection(
        dict(source, type="direct", location=base + "/video.ts"), checked_at=124
    )
    assert direct_health.status == "responding"
    assert requests[before][0] == "HEAD"
    assert len(requests) == before + 1

    m3u_health = check_connection(
        dict(source, type="m3u", location=base + "/list.m3u"), checked_at=125
    )
    assert m3u_health.status == "available"
    assert requests[-1][0] == "GET"
    # The server writes much more, while the health probe accepts a small prefix only.
    assert requests[-1][2] > 4096


def test_direct_head_405_is_truthfully_unverified_without_get_fallback(provider_server):
    base, requests = provider_server
    result = check_connection(
        dict(xtream_source(base), type="direct", location=base + "/head-unsupported"),
        checked_at=200,
    )
    assert result.status == "unverified"
    assert [method for method, path, _size in requests if path == "/head-unsupported"] == ["HEAD"]


def test_local_health_rejects_fifo_without_opening_it(tmp_path):
    fifo = tmp_path / "fixture.fifo"
    fifo.unlink(missing_ok=True)
    fifo.parent.mkdir(exist_ok=True)
    import os

    os.mkfifo(fifo)
    result = check_connection(
        dict(xtream_source("https://unused.invalid"), type="m3u", location=str(fifo)),
        checked_at=300,
    )
    assert result.status == "unavailable"


@pytest.mark.parametrize("source_type,tab", [("m3u", 0), ("xtream", 1), ("direct", 2)])
def test_edit_dialog_prefills_connection_and_locks_source_type(qt_app, source_type, tab):
    source = {
        "id": "stable",
        "name": "Existing",
        "type": source_type,
        "location": "https://provider.invalid/input",
        "username": "fixture-user",
        "password": "fixture-pass",
        "epg_url": "https://provider.invalid/guide.xml",
    }
    dialog = SourceDialog(source=source)
    try:
        assert dialog.windowTitle() == "Luna IPTV · Kaynağı düzenle"
        assert dialog.tabs.currentIndex() == tab
        assert [dialog.tabs.isTabEnabled(i) for i in range(3)] == [i == tab for i in range(3)]
        assert dialog.source()["id"] == "stable"
        assert dialog.source()["type"] == source_type
        assert dialog.source()["location"] == source["location"]
        assert dialog.source()["username"] == ("fixture-user" if source_type == "xtream" else "")
        dialog.reject()
        assert dialog.result() == QDialog.Rejected
    finally:
        dialog.close()


@pytest.fixture
def window(qt_app, tmp_path):
    widget = MainWindow(Store(tmp_path / "ui.sqlite3"))
    yield widget
    if isValid(widget):
        widget.close()
    qt_app.processEvents()


def test_source_menu_keeps_existing_actions_and_shows_health_snapshot(window):
    source = xtream_source("https://old.invalid")
    window.store.save_source(source)
    window.store.save_source_health("provider", "unavailable", 1_788_582_400)
    window.refresh_library(select_source="provider")

    menu = window.build_source_menu()
    labels = [action.text() for action in menu.actions()]
    assert labels[:6] == [
        "Seçili kaynağı yeniden adlandır",
        "Bağlantıyı düzenle",
        "Seçili kaynağı yenile",
        "Bağlantıyı kontrol et",
        f"Son kontrol: Ulaşılamıyor · {datetime.fromtimestamp(1_788_582_400):%d.%m.%Y %H:%M}",
        "Hesap durumu",
    ]
    assert not menu.actions()[4].isEnabled()


def test_edit_callback_keeps_playback_session_and_refreshes_current_metadata(window, monkeypatch):
    old = xtream_source("https://old.invalid")
    window.store.save_source(old)
    [stored] = window.store.replace_channels(
        "provider",
        [Channel("old", "One", "https://old.invalid/live", provider_key="live:11")],
    )
    window.refresh_library(select_source="provider")
    window.current = stored
    loads, stops = [], []
    monkeypatch.setattr(window.player, "load", lambda *args: loads.append(args))
    monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))
    callbacks = {}

    class AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def source(self):
            return xtream_source("https://new.invalid", user="new", password="new-pass")

    def capture(function, success, message, retry=None, busy=True, failure=None):
        callbacks.update(function=function, success=success, failure=failure)

    monkeypatch.setattr("luna_iptv.window.SourceDialog", AcceptedDialog)
    monkeypatch.setattr(window, "run_task", capture)
    window.edit_source(old)
    callbacks["success"](
        Playlist(
            [
                Channel(
                    "new",
                    "One updated",
                    "https://new.invalid/live/new/new-pass/11.ts",
                    provider_key="live:11",
                )
            ],
            [],
            [],
        )
    )

    assert not loads and not stops
    assert window.current.id == stored.id
    assert window.current.url == "https://new.invalid/live/new/new-pass/11.ts"
    assert window.store.sources()[0]["location"] == "https://new.invalid"


def test_cancelled_edit_starts_no_task_and_deleted_source_ignores_late_reply(window, monkeypatch):
    source = xtream_source("https://old.invalid")
    window.store.save_source(source)
    calls = []

    class CancelledDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("luna_iptv.window.SourceDialog", CancelledDialog)
    monkeypatch.setattr(window, "run_task", lambda *args, **kwargs: calls.append(args))
    window.edit_source(source)
    assert calls == []

    class AcceptedDialog(CancelledDialog):
        def exec(self):
            return QDialog.Accepted

        def source(self):
            return xtream_source("https://new.invalid")

    callbacks = {}
    monkeypatch.setattr("luna_iptv.window.SourceDialog", AcceptedDialog)
    monkeypatch.setattr(
        window,
        "run_task",
        lambda function, success, *args, **kwargs: callbacks.update(success=success),
    )
    window.edit_source(source)
    window.store.remove_source("provider")
    callbacks["success"](Playlist([Channel("new", "New", "https://new.invalid")], [], []))
    assert window.store.sources() == []


def test_health_callback_persists_failure_snapshot_without_catalog_or_playback(window, monkeypatch):
    source = xtream_source("https://old.invalid")
    window.store.save_source(source)
    window.store.replace_channels("provider", [Channel("one", "One", "https://stream.invalid")])
    before = window.store.channels()
    calls = {}
    monkeypatch.setattr(
        window,
        "run_task",
        lambda function, success, *args, **kwargs: calls.update(function=function, success=success),
    )
    loads = []
    monkeypatch.setattr(window.player, "load", lambda *args: loads.append(args))

    window.check_source(source)
    result = HealthResult("unavailable", 1_788_582_400)
    calls["success"](result)

    assert window.store.source_health("provider") == ("unavailable", 1_788_582_400)
    assert window.store.channels() == before
    assert loads == []


def test_failed_candidate_validation_preserves_every_stored_value(tmp_path):
    store = Store(tmp_path / "invalid.sqlite3")
    old = dict(xtream_source("https://old.invalid"), type="m3u", username="", password="")
    playlist_file = tmp_path / "broken.m3u"
    playlist_file.write_text("<!doctype html><title>provider error</title>", encoding="utf-8")
    store.save_source(old)
    store.replace_channels("provider", [Channel("one", "One", "https://stream.invalid")])
    store.set_favorite("provider:one", True)
    store.save_progress("provider:one", 12, 30)
    before = (store.sources(), store.channels(), store.favorites(), store.progress("provider:one"))

    candidate = dict(old, location=str(playlist_file))
    loaded = validate_candidate(candidate)
    assert loaded.channels == []
    assert (
        store.sources(),
        store.channels(),
        store.favorites(),
        store.progress("provider:one"),
    ) == before


def test_old_episode_reply_after_connection_edit_is_ignored(window, monkeypatch):
    old = xtream_source("https://old.invalid")
    window.store.save_source(old)
    [series] = window.store.replace_channels(
        "provider",
        [Channel("series", "Show", "", kind="series", series_id="42", provider_key="series:42")],
    )
    callbacks = {}
    monkeypatch.setattr(
        window,
        "run_task",
        lambda function, success, *args, **kwargs: callbacks.update(success=success),
    )
    window.open_series(series)
    window.store.save_source(
        xtream_source("https://new.invalid", user="new-user", password="new-pass")
    )
    callbacks["success"](
        [
            Channel(
                "episode",
                "Pilot",
                "https://old.invalid/series/old-user/old-pass/101.mkv",
                kind="movie",
                series_id="42",
                provider_key="episode:42:101",
            )
        ]
    )
    assert all("old-pass" not in channel.url for channel in window.store.channels())


def test_blank_cached_episode_surfaces_refetch_message_instead_of_loading(window, monkeypatch):
    channel = Channel(
        "provider:episode", "Pilot", "", kind="movie", series_id="42", provider_key=""
    )
    loads = []
    monkeypatch.setattr(window.player, "load", lambda *args: loads.append(args))
    window.play(channel)
    assert loads == []
    assert "yeniden" in window.message.text().lower()


def test_xtream_refresh_uses_reconciled_legacy_id_and_keeps_playback(window, monkeypatch):
    source = xtream_source("https://provider.invalid")
    window.store.save_source(source)
    [legacy] = window.store.replace_channels(
        "provider",
        [
            Channel(
                "legacy-channel-id",
                "Old title",
                "https://provider.invalid/live/old-user/old-pass/11.ts",
                provider_key="live:11",
            )
        ],
    )
    window.current = legacy
    window._loading = True
    stops = []
    monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))
    monkeypatch.setattr(window, "save_progress", lambda: None)

    window.accept_import(
        source,
        Playlist(
            [
                Channel(
                    "new-host-derived-id",
                    "New title",
                    "https://provider.invalid/live/old-user/old-pass/11.ts",
                    provider_key="live:11",
                )
            ],
            [],
            [],
        ),
    )

    assert stops == []
    assert window.current.id == "provider:legacy-channel-id"
    assert window.current.name == "New title"
    assert window._loading is True


def test_direct_connection_edit_preserves_single_channel_identity_and_user_state(tmp_path):
    store = Store(tmp_path / "direct.sqlite3")
    old = {
        "id": "direct",
        "name": "Local film",
        "type": "direct",
        "location": "/tmp/first.mkv",
        "username": "",
        "password": "",
        "epg_url": "",
    }
    store.save_source(old)
    [existing] = store.replace_channels(
        "direct", [Channel("old-hash", "Local film", "file:///tmp/first.mkv", kind="movie")]
    )
    store.set_favorite(existing.id, True)
    store.save_progress(existing.id, 19, 90)
    candidate = dict(old, location="/tmp/second.mkv")

    assert store.apply_source_connection(
        old,
        candidate,
        Playlist(
            [Channel("new-hash", "Local film", "file:///tmp/second.mkv", kind="movie")],
            [],
            [],
        ),
    )

    [updated] = store.channels("direct")
    assert updated.id == existing.id
    assert updated.url == "file:///tmp/second.mkv"
    assert store.favorites() == {existing.id}
    assert store.progress(existing.id) == (19, 90)

    [refreshed] = store.replace_channels(
        "direct",
        [Channel("later-url-hash", "Local film", "file:///tmp/second.mkv", kind="movie")],
    )
    assert refreshed.id == existing.id
    assert store.favorites() == {existing.id}
    assert store.progress(existing.id) == (19, 90)


def test_edit_that_removes_playing_channel_detaches_progress_without_stopping(window, monkeypatch):
    old = dict(xtream_source("/tmp/old.m3u"), type="m3u", username="", password="", epg_url="")
    window.store.save_source(old)
    [playing] = window.store.replace_channels(
        "provider", [Channel("old", "Old", "https://stream.invalid/old")]
    )
    window.current = playing
    window._position, window._duration = 10, 60
    stops = []
    monkeypatch.setattr(window.player, "stop", lambda: stops.append(True))
    callbacks = {}

    class AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def source(self):
            return dict(old, location="/tmp/new.m3u")

    monkeypatch.setattr("luna_iptv.window.SourceDialog", AcceptedDialog)
    monkeypatch.setattr(
        window,
        "run_task",
        lambda function, success, *args, **kwargs: callbacks.update(success=success),
    )
    window.edit_source(old)
    callbacks["success"](Playlist([Channel("new", "New", "https://stream.invalid/new")], [], []))

    assert stops == []
    assert window.current is playing
    assert window.current.id not in {channel.id for channel in window.store.channels()}
    window.save_progress()
    window.loaded()
    assert not window.favorite_button.isEnabled()
