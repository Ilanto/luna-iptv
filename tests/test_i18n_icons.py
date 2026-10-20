"""Translated transport labels must keep choosing their state icons."""

import pytest

from luna_iptv.i18n import _, get_language, set_language
from luna_iptv.motion import IconButton


@pytest.fixture(autouse=True)
def restore_language():
    previous = get_language()
    yield
    set_language(previous)


@pytest.mark.parametrize("language", ["tr", "en"])
def test_mute_icon_follows_translated_state(qt_app, language):
    set_language(language)
    button = IconButton(_("Ses"))
    try:
        assert button.icon_name() == "volume"
        button.setText(_("Sessiz"))
        assert button.icon_name() == "mute"
        button.setText(_("Ses"))
        assert button.icon_name() == "volume"
    finally:
        button.close()


def test_mini_return_icon_resolves_after_language_selection(qt_app):
    button = IconButton("Mini")
    try:
        for language in ("tr", "en", "tr"):
            set_language(language)
            button.setText(_("Geri dön"))
            assert button.icon_name() == "pip-exit"
            button.setText(_("Mini"))
            assert button.icon_name() == "pip"
    finally:
        button.close()


def test_symbol_states_and_explicit_icons_stay_independent_of_language(qt_app):
    set_language("en")
    button = IconButton("Custom action", "plus")
    try:
        assert button.icon_name() == "plus"
        button.setText("▶")
        assert button.icon_name() == "play"
        button.setText("Ⅱ")
        assert button.icon_name() == "pause"
        button.setText("Custom action")
        assert button.icon_name() == "plus"
    finally:
        button.close()
