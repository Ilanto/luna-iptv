"""Populated dialogs translate labels while retaining provider data and stored choices."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from luna_iptv.i18n import set_language
from luna_iptv.media_details import MediaDetails
from luna_iptv.media_dialog import EPISODE_DURATION_ROLE, MediaDetailDialog
from luna_iptv.models import Channel
from luna_iptv.parental import hash_pin
from luna_iptv.parental_ui import NewPinDialog, ParentalDialog, PinDialog, reset_failures
from luna_iptv.profiles_ui import ProfileEditor, ProfilesDialog
from luna_iptv.storage import Store


class PosterCache(QObject):
    ready = Signal(str)

    def request_logo(self, _url):
        raise AssertionError("Synthetic dialog should not request artwork")


@pytest.fixture
def english(qt_app):
    set_language("en")
    reset_failures()
    yield
    reset_failures()
    set_language("tr")


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    value.update_profile(value.profile_id, name="Çağrı", color="#E8B04B", avatar="moon")
    yield value
    value.close()


def test_populated_series_labels_keep_metadata_and_playback_identity(english):
    series = Channel("series", "Provider title", "", kind="series", series_id="show")
    episode = Channel(
        "episode",
        "Çağrı's episode",
        "file:///synthetic.mkv",
        kind="movie",
        group="Sezon 2",
        series_id="show",
        provider_key="episode:2",
    )
    details = MediaDetails(
        info={"description": "Provider plot", "duration": "128 dk"},
        episodes=[episode],
        episode_info={"episode:2": {"duration": "42 dk", "description": "Episode plot"}},
        series_title="Provider title",
    )
    original = deepcopy(details)
    cache = PosterCache()
    card = MediaDetailDialog(series, cache)
    try:
        card.set_details(details)
        assert card.season_combo.currentText() == "Season 2"
        assert card.season_combo.currentData() == "Sezon 2"
        assert card.season_tabs.tabText(0) == "Season 2"
        assert card.episode_combo.currentText() == episode.name
        assert card.episode_combo.itemData(0, EPISODE_DURATION_ROLE) == "42 min"
        assert "Episode 1" in card.episode_combo.itemData(0, Qt.AccessibleTextRole)
        assert card.facts_label.text() == "42 min"
        assert card.selected_channel() is episode
        assert details == original
        index = card.audio_combo.findText("English")
        assert index >= 0
        card.audio_combo.setCurrentIndex(index)
        assert card.playback_preferences()["audio"]["lang"] == "en"
        assert card.favorite_button.text() == "Add episode to favourites"
    finally:
        card.close()


def test_populated_profiles_translate_avatars_without_changing_saved_values(english, store):
    profile = store.profile(store.profile_id)
    editor = ProfileEditor(store, profile)
    listing = ProfilesDialog(store)
    try:
        assert listing.windowTitle() == "Profiles"
        assert editor.name.text() == "Çağrı"
        assert editor.avatar_buttons["moon"].toolTip() == "Moon"
        assert editor.avatar_buttons["star"].accessibleName() == "Star"
        assert editor.avatar_buttons["moon"].property("avatar") == "moon"
        editor.avatar_buttons["star"].setChecked(True)
        editor.daily_limit.setCurrentIndex(editor.daily_limit.findData(30))
        assert editor.daily_limit.currentText() == "30 min"
        assert editor.daily_limit.currentData() == 30
        assert editor.values()["avatar"] == "star"
        assert store.profile(store.profile_id) == profile
    finally:
        editor.close()
        listing.close()


def test_parental_prompts_translate_at_display_and_pluralize(english, store):
    store.set_pin_hash(hash_pin("2468"))
    dialog = ParentalDialog(store)
    new_pin = NewPinDialog()
    pin = PinDialog(store, "Synthetic reason", clock=lambda: 100)
    try:
        assert dialog.windowTitle() == "Parental controls"
        assert dialog.change_button.text() == "Change PIN"
        assert "locked" in dialog.summary.text()
        assert new_pin.windowTitle() == "Set a PIN"
        assert new_pin.first.placeholderText() == "New PIN"
        pin.field.setText("0000")
        assert not pin.try_pin()
        assert pin.error.text() == "Incorrect PIN. You have 4 attempts left."
        for _attempt in range(3):
            pin.field.setText("0000")
            pin.try_pin()
        assert pin.error.text() == "Incorrect PIN. You have 1 attempt left."
    finally:
        dialog.close()
        new_pin.close()
        pin.close()


def test_turkish_generated_metadata_display_is_unchanged(qt_app):
    set_language("tr")
    card = MediaDetailDialog(
        Channel("film", "Film", "file:///synthetic.mkv", kind="movie"), PosterCache()
    )
    try:
        card.set_details(MediaDetails(info={"duration": "128 dk", "genre": "Bilim kurgu"}))
        assert card.facts_label.text() == "128 dk"
        assert card.kind_label.text() == "FİLM  ·  BİLİM KURGU"
    finally:
        card.close()
