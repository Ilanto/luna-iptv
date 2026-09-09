"""Card choices commit only with playback, never while browsing or cancelling."""

import pytest
from shiboken6 import isValid

from luna_iptv.media_details import MediaDetails
from luna_iptv.models import Channel
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def choices(remember=True, audio="en", sub="tr"):
    return {
        "audio": {"mode": "track", "lang": audio},
        "sub": {"mode": "track", "lang": sub},
        "remember": remember,
    }


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "name": "Local", "type": "m3u"})
    store.save_source({"id": "other", "name": "Other", "type": "m3u"})
    store.replace_channels(
        "home",
        [
            Channel("live", "Live", "file:///live.ts"),
            Channel(
                "a",
                "A",
                "file:///a.mkv",
                kind="movie",
                series_id="7",
                group="Sezon 1",
                provider_key="episode:7:a",
            ),
            Channel(
                "b",
                "B",
                "file:///b.mkv",
                kind="movie",
                series_id="7",
                group="Sezon 2",
                provider_key="episode:7:b",
            ),
            Channel("show", "Show", "", kind="series", series_id="7"),
        ],
    )
    store.replace_channels("other", [Channel("movie", "Other", "file:///other.mkv", kind="movie")])
    store.save_progress("home:a", 42, 100)
    store.save_progress("home:b", 24, 100)
    store.save_playback_preferences("home", choices(audio="de", sub="en"))
    value = MainWindow(store)
    value.loads, value.property_writes = [], []
    monkeypatch.setattr(
        value.player, "load", lambda *args, **kwargs: value.loads.append((args, kwargs))
    )
    monkeypatch.setattr(
        value.player, "set_property", lambda *args: value.property_writes.append(args)
    )
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def item(window, identity):
    return next(c for c in window.store.channels() if c.id == identity)


def open_episode(window):
    window.details.open(item(window, "home:a"))
    card = window.details.dialog
    card.set_details(
        MediaDetails(
            info={"description": "Series plot"},
            series_title="Show",
            episodes=[item(window, "home:a"), item(window, "home:b")],
        )
    )
    return card


def test_card_edits_close_and_resume_cancel_do_not_touch_live_or_saved_preferences(window):
    window.request_play(item(window, "home:live"))
    saved = window.store.playback_preferences("home")
    writes = list(window.property_writes)
    token = window._playback_token
    card = open_episode(window)
    card.set_playback_preferences(choices(False))
    card.close_button.click()
    assert window.store.playback_preferences("home") == saved
    assert window.property_writes == writes
    assert window._playback_token == token and len(window.loads) == 1
    card = open_episode(window)
    card.set_playback_preferences(choices())
    card.play_button.click()
    assert window._resume_dialog is not None
    assert window.store.playback_preferences("home") == saved
    assert window.property_writes == writes
    window._resume_dialog.cancel_button.click()
    assert window.current.kind == "live" and len(window.loads) == 1
    assert window._playback_token == token
    assert window.store.playback_preferences("home") == saved


@pytest.mark.parametrize("button,start", [("resume_button", 42), ("restart_button", 0)])
@pytest.mark.parametrize("remember", [False, True])
def test_resume_decision_commits_card_languages_and_remember_choice(
    window, button, start, remember
):
    saved = window.store.playback_preferences("home")
    card = open_episode(window)
    card.set_playback_preferences(choices(remember))
    card.play_button.click()
    assert not window.loads
    assert window.store.playback_preferences("home") == saved
    getattr(window._resume_dialog, button).click()
    active = window.track_preferences.current_choices()
    assert active["audio"]["lang"] == "en"
    assert active["sub"]["lang"] == "tr"
    assert window.current.id == "home:a" and window.loads[0][1]["start"] == start
    assert not window.property_writes  # No pre-load global mutation of the old stream.
    window.details.open(item(window, "home:b"))
    preview = window.details.dialog.playback_preferences()
    if remember:
        assert preview["audio"]["lang"] == "en"
        assert preview["sub"]["lang"] == "tr"
    else:
        assert preview["audio"]["mode"] == preview["sub"]["mode"] == "auto"
        assert window.store.playback_preferences("home")["audio"] == saved["audio"]
    assert preview["remember"] is remember
    assert window.store.playback_preferences("other") == {}


def test_changing_episode_preserves_choices_and_uses_selected_resume_position(window):
    card = open_episode(window)
    card.set_playback_preferences(choices(False))
    card.season_combo.setCurrentIndex(1)
    assert card.selected_channel().id == "home:b"
    assert card.playback_preferences()["audio"]["lang"] == "en"
    card.play_button.click()
    window._resume_dialog.resume_button.click()
    assert window.current.id == "home:b" and window.loads[-1][1]["start"] == 24
    assert window.track_preferences.current_choices()["sub"]["lang"] == "tr"
    window.stop_playback()
    window.restart_current()
    assert window.loads[-1][1]["start"] == 0
    assert window.track_preferences.current_choices()["audio"]["lang"] == "en"
    assert window.track_preferences.current_choices()["sub"]["lang"] == "tr"


def test_new_selection_invalidates_pending_preferences(window):
    saved = window.store.playback_preferences("home")
    card = open_episode(window)
    card.set_playback_preferences(choices())
    card.play_button.click()
    old = window._resume_dialog
    window.request_play(item(window, "other:movie"))
    old.resume_button.click()
    assert window.current.id == "other:movie" and len(window.loads) == 1
    assert window.store.playback_preferences("home") == saved
    assert window.track_preferences.current_choices()["audio"]["mode"] == "auto"


def test_missing_languages_report_once_without_accepting_stale_tracks(window):
    window.play(item(window, "home:a"), preferences=choices(audio="tr", sub="tr"))
    old_token = window._playback_token
    window.play(item(window, "home:b"), preferences=choices(audio="tr", sub="tr"))
    window.playback_property(
        old_token,
        "track-list",
        [
            {"id": 2, "type": "audio", "lang": "tur", "selected": True},
            {"id": 2, "type": "sub", "lang": "tur", "selected": True},
        ],
    )
    assert window._tracks == []
    window.playback_property(
        window._playback_token,
        "track-list",
        [
            {"id": 1, "type": "audio", "lang": "eng", "selected": True},
            {"id": 1, "type": "sub", "lang": "eng"},
        ],
    )
    window.loaded()
    assert not window.language_notice.isHidden()
    notice = window.language_notice.text().casefold()
    assert "ses" in notice and "altyazı" in notice
    assert "varsayılan" in notice and "kapalı" in notice
    assert window.track_preferences.take_notice() == ""
    assert not any(name == "aid" and value == 2 for name, value in window.property_writes)
