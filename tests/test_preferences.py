"""Semantic track choices survive changing mpv IDs without changing defaults."""

import json

import pytest

from luna_iptv.models import Channel
from luna_iptv.storage import Store


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    source = store.save_source({"name": "Local", "type": "m3u"})
    store.replace_channels(source, [Channel("one", "Video", "file:///fixture.mkv", kind="movie")])
    yield store, source
    store.close()


class Commands:
    def __init__(self):
        self.values = []

    def set_property(self, name, value):
        self.values.append((name, value))


def test_saved_language_matches_new_ids_and_does_not_save_ids(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    preferences.select("audio", {"id": 2, "type": "audio", "lang": "tur", "title": "Türkçe"})
    saved = store.playback_preferences(source)
    assert "id" not in saved["audio"]
    assert "2" not in json.dumps(saved["audio"])
    preferences.begin(source)
    preferences.update_tracks(
        [
            {"id": 1, "type": "audio", "lang": "eng", "selected": True},
            {"id": 7, "type": "audio", "lang": "tr", "title": "Türkçe"},
        ]
    )
    player.values.clear()
    preferences.loaded()
    assert player.values == [("aid", 7)]
    preferences.update_tracks(
        [
            {"id": 1, "type": "audio", "lang": "eng"},
            {"id": 7, "type": "audio", "lang": "tr", "title": "Türkçe", "selected": True},
        ]
    )
    assert player.values == [("aid", 7)]


def test_missing_language_keeps_default_and_preference_for_later_video(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    preferences.select("audio", {"id": 2, "type": "audio", "lang": "tur"})
    saved = store.playback_preferences(source)
    preferences.begin(source)
    preferences.update_tracks([{"id": 5, "type": "audio", "lang": "eng", "selected": True}])
    player.values.clear()
    preferences.loaded()
    assert player.values == []
    assert store.playback_preferences(source) == saved


def test_off_persists_reopen_and_source_preferences_are_isolated(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    preferences.select("sub", None)
    reopened = Store(store.path)
    try:
        assert reopened.playback_preferences(source)["sub"] == {"mode": "off"}
    finally:
        reopened.close()
    preferences.begin(source)
    assert player.values[-1] == ("sid", "no")
    other = store.save_source({"name": "Other", "type": "m3u"})
    preferences.begin(other)
    assert player.values[-2:] == [("aid", "auto"), ("sid", "auto")]
    assert store.playback_preferences(other) == {}
    store.remove_source(source)
    assert store.playback_preferences(source) == {}


def test_disabled_remembering_and_reset_do_not_keep_old_numeric_selection(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    preferences.select("audio", {"id": 2, "type": "audio", "lang": "tur"})
    preferences.set_remember(False)
    preferences.select("audio", {"id": 8, "type": "audio", "lang": "eng"})
    assert store.playback_preferences(source)["audio"]["lang"] == "tr"
    preferences.begin(source)
    preferences.update_tracks([{"id": 5, "type": "audio", "lang": "tur"}])
    player.values.clear()
    preferences.loaded()
    assert player.values == []
    preferences.set_remember(True)
    assert player.values == [("aid", 5)]
    preferences.reset()
    assert player.values[-2:] == [("aid", "auto"), ("sid", "auto")]
    assert "audio" not in store.playback_preferences(source)


def test_old_menu_action_cannot_change_new_source(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    old = preferences.generation
    other = store.save_source({"name": "Other", "type": "m3u"})
    preferences.begin(other)
    player.values.clear()
    assert not preferences.select("sub", None, generation=old)
    assert not player.values
    assert store.playback_preferences(other) == {}


def test_unlabelled_track_plays_without_inventing_persistent_identity(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    preferences.select("audio", {"id": 2, "type": "audio"})
    assert player.values[-1] == ("aid", 2)
    assert "audio" not in store.playback_preferences(source)


def test_subtitle_title_and_forced_flag_prefer_matching_variant():
    from luna_iptv.preferences import match_track, track_preference

    choice = track_preference({"lang": "eng", "title": "Full", "forced": False})
    tracks = [
        {"id": 3, "type": "sub", "lang": "eng", "title": "Signs", "forced": True},
        {"id": 9, "type": "sub", "lang": "en", "title": "Full", "forced": False},
    ]
    assert match_track(tracks, "sub", choice) == 9
    assert match_track(tracks, "audio", choice) is None


def test_preference_storage_rejects_unknown_metadata_and_bounds_titles(library):
    store, source = library
    store.save_playback_preferences(
        source,
        {
            "password": "do-not-save",
            "audio": {"mode": "track", "lang": "tur", "title": "a" * 1000, "id": 99},
        },
    )
    saved = store.playback_preferences(source)
    assert "password" not in saved
    assert "id" not in saved["audio"]
    assert len(saved["audio"]["title"]) <= 256


def test_old_settings_actions_and_deleted_source_cannot_write(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    preferences = TrackPreferences(store, Commands())
    preferences.begin(source)
    old = preferences.generation
    other = store.save_source({"name": "Other", "type": "m3u"})
    preferences.begin(other)
    assert not preferences.set_remember(False, generation=old)
    assert not preferences.reset(generation=old)
    assert store.playback_preferences(other) == {}
    preferences.finish()
    store.remove_source(other)
    preferences.reset()
    preferences.set_remember(False)
    assert preferences.source_id is None
    assert store.playback_preferences(other) == {}
