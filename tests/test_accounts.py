from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from luna_iptv.accounts import AccountProfile, normalize_profile, serialize_profile
from luna_iptv.dialogs import AccountDialog
from luna_iptv.models import Channel, Playlist
from luna_iptv.network import NetworkError, XtreamClient
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture(scope="session")
def app(qt_app) -> QApplication:
    return qt_app


@pytest.fixture
def window(app: QApplication, tmp_path: Path):
    widget = MainWindow(Store(tmp_path / "data" / "library.sqlite3"))
    yield widget
    if isValid(widget):
        widget.close()
    app.processEvents()


def wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("Timed out waiting for account workflow")


def xtream_source(source_id: str | None = None) -> dict[str, str]:
    source = {
        "name": "Fixture Xtream",
        "type": "xtream",
        "location": "http://127.0.0.1:9876",
        "username": "fixture-user",
        "password": "fixture-secret",
        "epg_url": "",
    }
    if source_id is not None:
        source["id"] = source_id
    return source


@pytest.fixture
def profile_server():
    requests: list[tuple[str, dict[str, list[str]]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            requests.append((parsed.path, query))
            action = query.get("action", [""])[0]
            payload = {
                "": {
                    "user_info": {
                        "auth": 1,
                        "status": "Expired",
                        "exp_date": "1893456000",
                        "username": "fixture-user",
                        "password": "fixture-secret",
                    }
                },
                "get_live_categories": [],
                "get_vod_categories": [],
                "get_series_categories": [],
                "get_live_streams": [
                    {"stream_id": 1, "name": "One", "category_id": "1"}
                ],
                "get_vod_streams": [],
                "get_series": [],
            }.get(action, {})
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_normalize_profile_accepts_provider_number_variants_and_whitelists_metadata() -> None:
    profile = normalize_profile(
        {
            "user_info": {
                "auth": "1",
                "status": " Active ",
                "created_at": "1609459200",
                "exp_date": 1893456000.0,
                "active_cons": "2",
                "max_connections": 0,
                "username": "fixture-user",
                "password": "fixture-secret",
                "token": "fixture-token",
            },
            "server_info": {"url": "credential-bearing-provider.test"},
        },
        checked_at=2_000_000_000,
    )

    assert profile == AccountProfile(
        status="active",
        created_at=1_609_459_200,
        expires_at=1_893_456_000,
        active_connections=2,
        max_connections=0,
        checked_at=2_000_000_000,
    )
    serialized = serialize_profile(profile)
    assert set(json.loads(serialized)) == {
        "active_connections",
        "checked_at",
        "created_at",
        "expires_at",
        "max_connections",
        "status",
    }
    assert "fixture-user" not in serialized
    assert "fixture-secret" not in serialized
    assert "fixture-token" not in serialized
    assert "credential-bearing-provider.test" not in serialized


@pytest.mark.parametrize(
    ("user_info", "expected"),
    [
        ({"auth": 1, "status": "Expired"}, "expired"),
        ({"auth": "1", "status": "Disabled"}, "disabled"),
        ({"auth": True, "status": "Banned"}, "banned"),
        ({"auth": 1, "status": "provider-specific"}, "unknown"),
        ({"auth": 1}, "unknown"),
        ({"auth": 0, "status": "Active"}, "unknown"),
        ({"auth": "yes", "status": "Active"}, "unknown"),
    ],
)
def test_normalize_profile_keeps_auth_and_explicit_status_semantics_separate(
    user_info: dict[str, object], expected: str
) -> None:
    assert normalize_profile({"user_info": user_info}, checked_at=100).status == expected


def test_normalize_profile_rejects_bad_missing_and_non_positive_dates_without_inventing_limits() -> None:
    profile = normalize_profile(
        {
            "user_info": {
                "auth": 1,
                "status": "Active",
                "created_at": "",
                "exp_date": "0",
                "active_cons": "-1",
                "max_connections": "unlimited",
            }
        },
        checked_at=101,
    )

    assert profile.status == "active"
    assert profile.created_at is None
    assert profile.expires_at is None
    assert profile.active_connections is None
    assert profile.max_connections is None


def test_playlist_keeps_three_argument_constructor_compatible() -> None:
    assert Playlist([], [], []).account_profile is None


def test_xtream_account_info_only_retrieves_and_normalizes_profile_endpoint(profile_server) -> None:
    base, requests = profile_server

    profile = XtreamClient(base, "fixture-user", "fixture-secret").account_info()

    assert profile.status == "expired"
    assert profile.expires_at == 1_893_456_000
    assert requests == [
        (
            "/player_api.php",
            {"username": ["fixture-user"], "password": ["fixture-secret"]},
        )
    ]
    assert "fixture-secret" not in serialize_profile(profile)


def test_xtream_account_info_rejects_non_profile_response(monkeypatch) -> None:
    client = XtreamClient("http://127.0.0.1:9876", "fixture-user", "fixture-secret")
    monkeypatch.setattr(client, "_api", lambda: {"user_info": []})

    with pytest.raises(NetworkError, match="profil"):
        client.account_info()


def test_xtream_catalog_captures_profile_from_its_existing_account_response(profile_server) -> None:
    base, requests = profile_server

    playlist = XtreamClient(base, "fixture-user", "fixture-secret").catalog()

    assert len(playlist.channels) == 1
    assert playlist.account_profile is not None
    assert playlist.account_profile.status == "expired"
    assert sum(1 for _path, query in requests if "action" not in query) == 1


def test_store_persists_only_sanitized_snapshot_and_reopens_it(tmp_path: Path) -> None:
    database = tmp_path / "library.db"
    store = Store(database)
    source_id = store.save_source(xtream_source())
    profile = normalize_profile(
        {
            "user_info": {
                "auth": 1,
                "status": "Active",
                "created_at": "1609459200",
                "exp_date": "1893456000",
                "active_cons": "2",
                "max_connections": "4",
                "username": "fixture-user",
                "password": "fixture-secret",
            }
        },
        checked_at=2_000_000_000,
    )
    store.save_account_profile(source_id, profile)
    store.close()

    reopened = Store(database)
    assert reopened.account_profile(source_id) == profile
    columns = {
        row[1] for row in reopened._db.execute("PRAGMA table_info(account_snapshots)").fetchall()
    }
    assert "username" not in columns
    assert "password" not in columns
    assert "token" not in columns
    reopened.close()


def test_store_migrates_existing_database_and_snapshot_cascades_with_source(tmp_path: Path) -> None:
    database = tmp_path / "old-library.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE sources (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
            location TEXT NOT NULL, username TEXT NOT NULL,
            password TEXT NOT NULL, epg_url TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO sources VALUES(?,?,?,?,?,?,?)",
        ("old", "Old", "xtream", "http://127.0.0.1:9876", "u", "p", ""),
    )
    connection.commit()
    connection.close()

    store = Store(database)
    profile = AccountProfile("expired", None, 1_900_000_000, 0, 1, 2_000_000_000)
    store.save_account_profile("old", profile)
    assert store.account_profile("old") == profile

    store.remove_source("old")

    assert store.account_profile("old") is None
    assert store._db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone() == (0,)
    store.close()


def test_account_dialog_renders_dates_remaining_status_and_zero_limit(app: QApplication) -> None:
    now = 1_800_000_000
    profile = AccountProfile(
        "active",
        1_609_459_200,
        now + 45 * 86_400,
        2,
        0,
        now - 60,
    )
    dialog = AccountDialog("Fixture Xtream", profile, now=now)

    assert dialog.status_value.text() == "Aktif"
    assert dialog.status_value.property("state") == "active"
    assert "01.01.2021" in dialog.created_value.text()
    assert "45 gün" in dialog.remaining_value.text()
    assert "1,5 ay" in dialog.remaining_value.text()
    assert dialog.connections_value.text() == "2 / 0 · son kontrolde"
    assert "Bilinmiyor" not in dialog.checked_value.text()
    dialog.close()
    app.processEvents()


def test_catalog_import_persists_returned_account_profile(window: MainWindow) -> None:
    profile = AccountProfile("disabled", None, None, 0, 1, 2_000_000_000)

    window.accept_import(
        xtream_source(),
        Playlist([Channel("one", "One", "http://127.0.0.1/live")], [], [], profile),
    )

    source_id = window.store.sources()[0]["id"]
    assert window.store.account_profile(source_id) == profile


def test_source_menu_exposes_account_only_for_xtream_and_opens_it(
    window: MainWindow, monkeypatch
) -> None:
    xtream_id = window.store.save_source(xtream_source())
    m3u_id = window.store.save_source(
        {
            "name": "M3U",
            "type": "m3u",
            "location": "http://127.0.0.1/list.m3u",
            "username": "",
            "password": "",
            "epg_url": "",
        }
    )
    window.refresh_library(select_source=xtream_id)
    opened: list[str] = []
    monkeypatch.setattr(window, "open_account", lambda source: opened.append(source["id"]))

    menu = window.build_source_menu()
    account_action = next(action for action in menu.actions() if action.text() == "Hesap durumu")
    account_action.trigger()
    assert opened == [xtream_id]

    window.source_combo.setCurrentIndex(window.source_combo.findData(m3u_id))
    menu = window.build_source_menu()
    assert "Hesap durumu" not in [action.text() for action in menu.actions()]


def test_account_dialog_shows_cache_then_refreshes_without_catalogue_reload(
    app: QApplication, window: MainWindow, profile_server, monkeypatch
) -> None:
    base, requests = profile_server
    source = xtream_source()
    source["location"] = base
    source_id = window.store.save_source(source)
    source["id"] = source_id
    cached = AccountProfile("active", 1_609_459_200, None, 1, 2, 1_800_000_000)
    window.store.save_account_profile(source_id, cached)
    monkeypatch.setattr(
        window,
        "import_source",
        lambda *_args: pytest.fail("account refresh must not reload the catalogue"),
    )
    initial_window_status = window.message.text()

    dialog = window.open_account(source)

    assert dialog.status_value.text() == "Aktif"
    assert dialog.is_refreshing
    wait_until(app, lambda: not dialog.is_refreshing)
    assert dialog.status_value.text() == "Süresi dolmuş"
    assert window.store.account_profile(source_id).status == "expired"
    assert len(requests) == 1
    assert window.message.text() == initial_window_status

    dialog.refresh_button.click()
    assert dialog.is_refreshing
    wait_until(app, lambda: not dialog.is_refreshing)
    assert len(requests) == 2


def test_account_refresh_failure_keeps_cached_profile_and_timestamp(
    app: QApplication, window: MainWindow, monkeypatch
) -> None:
    source_id = window.store.save_source(xtream_source())
    source = window.store.sources()[0]
    cached = AccountProfile("active", None, None, 1, 2, 1_800_000_000)
    window.store.save_account_profile(source_id, cached)

    class FailingClient:
        def __init__(self, *_args):
            pass

        def account_info(self):
            raise NetworkError("Hesap profiline erişilemedi.")

    monkeypatch.setattr("luna_iptv.window.XtreamClient", FailingClient)

    dialog = window.open_account(source)
    wait_until(app, lambda: not dialog.is_refreshing)

    assert window.store.account_profile(source_id) == cached
    assert dialog.status_value.text() == "Aktif"
    assert "Hesap profiline erişilemedi" in dialog.error_label.text()
    assert dialog.checked_value.text() != "Henüz başarılı kontrol yok."


@pytest.mark.parametrize("finish_by", ["close", "delete"])
def test_late_account_reply_is_ignored_after_dialog_close_or_source_deletion(
    app: QApplication,
    window: MainWindow,
    monkeypatch,
    finish_by: str,
) -> None:
    source_id = window.store.save_source(xtream_source())
    source = window.store.sources()[0]
    cached = AccountProfile("active", None, None, 1, 2, 1_800_000_000)
    window.store.save_account_profile(source_id, cached)
    started = threading.Event()
    release = threading.Event()

    class BlockingClient:
        def __init__(self, *_args):
            pass

        def account_info(self):
            started.set()
            assert release.wait(5)
            return AccountProfile("expired", None, 1_900_000_000, 0, 2, 2_000_000_000)

    monkeypatch.setattr("luna_iptv.window.XtreamClient", BlockingClient)
    dialog = window.open_account(source)
    assert started.wait(2)

    if finish_by == "close":
        dialog.close()
        app.processEvents()
    else:
        monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
        window.remove_source(source)
    release.set()
    wait_until(app, lambda: not window._tasks)

    if finish_by == "close":
        assert window.store.account_profile(source_id) == cached
    else:
        assert window.store.sources() == []
