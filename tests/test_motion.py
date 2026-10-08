"""Luna's icons and motion stay cheap: event-driven, and silent during playback."""

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QEnterEvent
from shiboken6 import isValid

from luna_iptv import icons
from luna_iptv.models import Channel
from luna_iptv.motion import IconButton
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


def test_every_icon_renders_visible_pixels(qt_app):
    for name in icons.ICONS:
        image = icons.pixmap(name, "#ffffff", 24, 2.0).toImage()
        assert image.width() == 48
        assert any(
            image.pixelColor(x, y).alpha() > 0 for x in range(0, 48, 2) for y in range(0, 48, 2)
        ), name


def test_icon_follows_text_state(qt_app):
    button = IconButton("▶", label=False)
    assert button.icon_name() == "play"
    button.setText("Ⅱ")
    assert button.icon_name() == "pause"
    named = IconButton("Bilgi", "info")
    assert named.icon_name() == "info"
    assert named.text() == "Bilgi"


def test_hover_animates_and_settles(qt_app):
    button = IconButton("Ses")
    button.show()
    button.enterEvent(
        QEnterEvent(button.rect().center(), button.rect().center(), button.rect().center())
    )
    assert button._animation.endValue() == 1.0
    button._animation.setCurrentTime(button._animation.duration())
    assert button._hover == 1.0
    button.leaveEvent(QEvent(QEvent.Leave))
    button._animation.setCurrentTime(button._animation.duration())
    assert button._hover == 0.0
    assert button.icon_color() == button.colors[0]
    button.close()


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    store = Store(tmp_path / "library.sqlite3")
    store.save_source({"id": "home", "type": "m3u", "name": "Home"})
    store.replace_channels("home", [Channel("live", "Live", "file:///live.ts")])
    value = MainWindow(store)
    monkeypatch.setattr(value.player, "load", lambda *a, **kw: None)
    monkeypatch.setattr(value.player, "set_property", lambda *a: None)
    # This fixture exercises the welcome artwork explicitly, even with the idle panel hidden.
    value.watch.show()
    value.show()
    qt_app.processEvents()
    yield value
    if isValid(value):
        value.close()
    qt_app.processEvents()


def test_welcome_sky_animates_only_while_visible(window, qt_app):
    assert window.welcome_sky.animating
    window.play(next(iter(window.store.channels())))
    qt_app.processEvents()
    assert window.video_stack.currentIndex() == 1
    assert not window.welcome_sky.animating
    window.stop_playback()
    window.video_stack.setCurrentIndex(0)
    qt_app.processEvents()
    assert window.welcome_sky.animating


def test_nav_indicator_glides_to_selected_section(window, qt_app):
    indicator = window.sidebar.indicator
    window.set_section("favorites")
    indicator._animation.setCurrentTime(indicator._animation.duration())
    qt_app.processEvents()
    assert indicator.geometry() == window.nav_buttons["favorites"].geometry()


def test_play_button_shows_pause_once_playback_loads(window, qt_app):
    window.play(next(iter(window.store.channels())))
    window.loaded()
    assert window.play_button.text() == "Ⅱ"
    assert window.play_button.icon_name() == "pause"
    window.playback_property(window._playback_token, "pause", True)
    window.player_property("pause", True)
    assert window.play_button.icon_name() == "play"
