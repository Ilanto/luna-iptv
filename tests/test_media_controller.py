import time

import pytest
from shiboken6 import isValid

from luna_iptv.media_controller import DETAIL_TTL, source_fingerprint
from luna_iptv.media_details import MediaDetails
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source(
        {
            "id": "home",
            "type": "xtream",
            "name": "Fixture",
            "location": "https://fixture.invalid",
            "username": "test",
            "password": "test",
        }
    )
    store.replace_channels(
        "home",
        [
            Channel(
                "film",
                "Film",
                "https://fixture.invalid/movie/test/test/1.mp4",
                kind="movie",
                provider_key="movie:1",
            ),
            Channel(
                "other",
                "Other film",
                "https://fixture.invalid/movie/test/test/2.mp4",
                kind="movie",
                provider_key="movie:2",
            ),
            Channel("series", "Series", "", kind="series", series_id="3", provider_key="series:3"),
            Channel("live", "Live", "https://fixture.invalid/live/test/test/4.ts"),
            Channel("live2", "Live two", "https://fixture.invalid/live/test/test/5.ts"),
        ],
    )
    value = MainWindow(store)
    value.requests = []
    value.loads = []
    monkeypatch.setattr(
        value,
        "run_task",
        lambda function, success, *a, **kw: value.requests.append(
            (function, success, kw["failure"])
        ),
    )
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: value.loads.append((a, kw)))
    monkeypatch.setattr(value.player, "set_property", lambda *a: None)
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def channel(window, name):
    return next(c for c in window.store.channels() if c.id == "home:" + name)


def test_browsing_and_favorite_leave_live_playback_untouched(window):
    live = channel(window, "live")
    window.request_play(live)
    window.set_section("movie")
    window.activate_index(window.proxy.index(0, 0))
    assert not window._busy
    card = window.details.dialog
    card.favorite_button.click()
    assert channel(window, "film").id in window.store.favorites()
    assert live.id not in window.store.favorites()
    window.requests[0][1](MediaDetails(info={"description": "Arrived in background"}))
    assert window.current.id == live.id and len(window.loads) == 1
    card.reject()
    window.set_section("live")
    for row in [1, 0, 1]:
        window.activate_index(window.proxy.index(row, 0))
    assert window.current.id == channel(window, "live2").id
    assert len(window.loads) == 4


def test_latest_card_wins_and_request_queue_is_bounded(window):
    film, other, series = [channel(window, name) for name in ("film", "other", "series")]
    window.details.open(film)
    window.details.open(film)
    window.details.open(other)
    window.details.open(series)
    assert len(window.requests) == 1
    window.requests[0][1](MediaDetails(info={"description": "Wrong card"}))
    assert len(window.requests) == 2
    assert window.details.dialog.selected_channel() is None
    episode = Channel(
        "episode",
        "Pilot",
        "https://fixture.invalid/series/test/test/9.mp4",
        group="Sezon 1",
        kind="movie",
        series_id="3",
        provider_key="episode:3:9",
    )
    window.requests[1][1](MediaDetails(episodes=[episode]))
    assert window.details.dialog.selected_channel().name == "Pilot"
    assert not window.loads


def test_fresh_cache_reopens_without_request_and_stale_error_keeps_episodes(window):
    series = channel(window, "series")
    source = window.store.sources()[0]
    fingerprint = source_fingerprint(source)
    episode = Channel(
        "episode",
        "Pilot",
        "https://fixture.invalid/series/test/test/9.mp4",
        group="Sezon 2",
        kind="movie",
        series_id="3",
        provider_key="episode:3:9",
    )
    details = MediaDetails(info={"description": "Cached plot"}, episodes=[episode])
    window.store.save_media_details(series.id, fingerprint, details, int(time.time()))
    window.details.open(series)
    assert not window.requests
    assert window.details.dialog.selected_channel().name == "Pilot"
    window.details.dismiss()
    window.store.save_media_details(
        series.id, fingerprint, details, int(time.time()) - DETAIL_TTL - 1
    )
    window.details.open(series)
    window.requests[0][2]("provider error")
    selected = window.details.dialog.selected_channel()
    assert selected.name == "Pilot" and window.details.dialog.play_button.isEnabled()
    window.store.save_progress(selected.id, 42, 100)
    window.details.dialog.play_button.click()
    assert not window.loads
    window._resume_dialog.cancel_button.click()
    assert not window.loads
    window.details.open(series)
    assert len(window.requests) == 1  # Failed request cooldown, not a retry loop.


@pytest.mark.parametrize("change", ["remove", "connection", "catalog"])
def test_late_details_cannot_restore_removed_or_changed_source(window, change):
    film = channel(window, "film")
    source = window.store.sources()[0]
    window.details.open(film)
    if change == "remove":
        window.store.remove_source("home")
    elif change == "connection":
        window.store.save_source(dict(source, password="new-password"))
    else:
        window.store.replace_channels("home", [channel(window, "live")])
    window.refresh_library()
    assert window.details.dialog is None
    window.requests[0][1](MediaDetails(info={"description": "Stale data"}))
    assert window.store.media_details(film.id, source_fingerprint(source)) is None
    assert not window.loads


def test_closed_window_ignores_inflight_response(window):
    window.details.open(channel(window, "film"))
    finished = window.requests[0][1]
    window.close()
    finished(MediaDetails(info={"description": "Too late"}))
    assert window.details.dialog is None


def test_live_selection_dismisses_card_and_late_response_does_not_reopen_it(window):
    window.details.open(channel(window, "film"))
    window.set_section("live")
    window.activate_index(window.proxy.index(0, 0))
    window.requests[0][1](MediaDetails(info={"description": "Late movie plot"}))
    assert window.details.dialog is None
    assert window.current.kind == "live" and len(window.loads) == 1


def test_changed_connection_never_plays_cached_episode(window):
    series = channel(window, "series")
    window.details.open(series)
    episode = Channel(
        "episode",
        "Pilot",
        "https://fixture.invalid/series/test/test/9.mp4",
        group="Sezon 1",
        kind="movie",
        series_id="3",
        provider_key="episode:3:9",
    )
    window.requests[0][1](MediaDetails(episodes=[episode]))
    source = window.store.sources()[0]
    window.store.save_source(dict(source, password="new-password"))
    window.details.dialog.play_button.click()
    assert window.details.dialog is None
    assert not window.loads
