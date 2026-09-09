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
    options = preferences.begin(source)
    assert options["alang"] == "tr"
    assert options["aid"] == "auto"
    preferences.update_tracks(
        [
            {"id": 3, "type": "audio", "lang": "tur", "title": "Commentary", "selected": True},
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
    player.values.clear()
    assert preferences.begin(source)["sid"] == "no"
    other = store.save_source({"name": "Other", "type": "m3u"})
    options = preferences.begin(other)
    assert options["aid"] == options["sid"] == "auto"
    assert options["alang"] == options["slang"] == ""
    assert not player.values
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
    preferences.update_tracks(
        [
            {"id": 5, "type": "audio", "lang": "tur"},
            {"id": 8, "type": "audio", "lang": "eng", "selected": True},
        ]
    )
    player.values.clear()
    preferences.loaded()
    assert player.values == []
    preferences.set_remember(True)
    assert player.values == [("aid", 5)]
    preferences.reset()
    assert player.values[-2:] == [("aid", "auto"), ("sid", "auto")]
    assert ("alang", "") in player.values
    assert ("slang", "") in player.values
    assert ("subs-fallback", "default") in player.values
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


def test_explicit_unlabelled_choice_is_not_overridden_by_saved_language(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    tur = {"id": 2, "type": "audio", "lang": "tur"}
    unknown = {"id": 8, "type": "audio"}
    preferences.begin(source)
    preferences.select("audio", tur)
    preferences.begin(source)
    preferences.update_tracks([tur, unknown])
    preferences.loaded()
    player.values.clear()
    preferences.select("audio", unknown)
    preferences.update_tracks([tur, unknown | {"selected": True}])
    assert player.values == [("aid", 8)]
    assert store.playback_preferences(source)["audio"]["lang"] == "tr"
    preferences.begin(source)
    preferences.update_tracks([tur, unknown])
    preferences.loaded()
    assert player.values[-1] == ("aid", 2)


def test_preview_is_detached_and_does_not_touch_current_playback(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    other = store.save_source({"name": "Other", "type": "m3u"})
    store.save_playback_preferences(other, {"audio": {"mode": "track", "lang": "eng"}})
    player = Commands()
    preferences = TrackPreferences(store, player)
    preferences.begin(source)
    generation = preferences.generation
    saved = store.playback_preferences(other)
    preview = preferences.preview(other)
    preview["audio"]["lang"] = "tr"
    preview["remember"] = False
    assert preferences.preview(other)["audio"]["lang"] == "en"
    assert preferences.source_id == source
    assert preferences.generation == generation
    assert store.playback_preferences(other) == saved
    assert not player.values


def test_one_shot_choices_survive_loaded_manual_selection_and_finish(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    store.save_playback_preferences(source, {"audio": {"mode": "track", "lang": "tur"}})
    player = Commands()
    preferences = TrackPreferences(store, player)
    options = preferences.begin(
        source,
        {
            "audio": {"mode": "track", "lang": "eng"},
            "sub": {"mode": "track", "lang": "tur"},
            "remember": False,
        },
    )
    assert options["alang"] == "en"
    assert options["slang"] == "tr"
    assert options["subs-fallback"] == options["subs-fallback-forced"] == "no"
    assert options["subs-with-matching-audio"] == "yes"
    assert not player.values
    tracks = [
        {"type": "audio", "id": 1, "lang": "eng", "selected": True},
        {"type": "audio", "id": 2, "lang": "tur"},
        {"type": "sub", "id": 3, "lang": "tur", "selected": True},
    ]
    preferences.update_tracks(tracks)
    preferences.loaded()
    assert not player.values
    assert preferences.current_choices()["audio"]["lang"] == "en"
    preferences.select("sub", None)
    preferences.finish()
    choices = preferences.current_choices()
    assert choices["remember"] is False
    assert choices["sub"] == {"mode": "off"}
    assert choices["audio"]["lang"] == "en"
    assert preferences.begin(source, choices)["sid"] == "no"
    assert store.playback_preferences(source)["audio"]["lang"] == "tr"
    assert store.playback_preferences(source)["remember"] is False
    assert preferences.preview(source) == {
        "audio": {"mode": "auto"},
        "sub": {"mode": "auto"},
        "remember": False,
    }
    assert preferences.begin(source)["alang"] == ""


def test_explicit_automatic_clears_saved_language_only_when_remembering(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    store.save_playback_preferences(
        source,
        {"audio": {"mode": "track", "lang": "tur"}, "sub": {"mode": "off"}},
    )
    preferences = TrackPreferences(store, Commands())
    choices = {"audio": {"mode": "auto"}, "sub": {"mode": "auto"}, "remember": False}
    preferences.begin(source, choices)
    assert store.playback_preferences(source)["audio"]["lang"] == "tr"
    assert store.playback_preferences(source)["sub"] == {"mode": "off"}
    preferences.begin(source, choices | {"remember": True})
    assert store.playback_preferences(source) == {"remember": True}


def test_missing_languages_notice_waits_for_authoritative_tracks_and_is_one_shot(library):
    from luna_iptv.preferences import TrackPreferences

    store, source = library
    player = Commands()
    preferences = TrackPreferences(store, player)
    requested = {
        "audio": {"mode": "track", "lang": "tur"},
        "sub": {"mode": "track", "lang": "deu"},
        "remember": False,
    }
    preferences.begin(source, requested)
    preferences.loaded()
    assert preferences.take_notice() == ""
    tracks = [
        {"type": "audio", "id": 1, "lang": "eng", "selected": True},
        {"type": "sub", "id": 2, "lang": "eng", "selected": False},
    ]
    preferences.update_tracks(tracks)
    notice = preferences.take_notice()
    assert "tr" in notice and "de" in notice
    assert not player.values
    preferences.update_tracks(tracks)
    assert preferences.take_notice() == ""
    assert preferences.current_choices()["audio"]["lang"] == "tr"
    assert preferences.begin(source, requested)["slang"] == "de"
    preferences.update_tracks(tracks)
    preferences.loaded()
    assert preferences.take_notice() == notice
