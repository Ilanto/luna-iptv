"""Live appearance choices, a quiet guide pulse, and persistent profile pictures."""

import ast
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QWidget

from luna_iptv import motion, theme
from luna_iptv.epg import GuideIndex
from luna_iptv.guide_view import HEADER, LOGO_COLUMN, ROW, GuideGrid
from luna_iptv.logos import LogoCache
from luna_iptv.models import Channel
from luna_iptv.profiles_ui import ProfileAvatar, ProfileEditor
from luna_iptv.settings_dialog import SettingsDialog
from luna_iptv.storage import Store

AVATARS = (
    "moon",
    "star",
    "rocket",
    "cat",
    "fox",
    "owl",
    "bear",
    "planet",
    "comet",
    "sun",
    "cloud",
    "tv",
)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library.sqlite3")
    yield value
    value.close()


@pytest.fixture(autouse=True)
def restore_appearance(qt_app):
    level = motion.motion_level()
    style, palette, font = qt_app.styleSheet(), qt_app.palette(), qt_app.font()
    yield
    if hasattr(theme, "set_palette"):
        theme.set_palette("moon", "night")
    motion.set_motion_level(level)
    qt_app.setStyleSheet(style)
    qt_app.setPalette(palette)
    qt_app.setFont(font)


def contrast(first, second):
    def luminance(value):
        rgb = QColor(value).getRgbF()[:3]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_palette_changes_tokens_and_stylesheet_and_restores():
    original = (theme.ACCENT, theme.ACCENT_STRONG, theme.ACCENT_TINT, theme.ACCENT_INK, theme.STYLE)
    theme.set_palette("rose", "oled")
    assert theme.ACCENT != original[0]
    assert theme.ACCENT_STRONG != original[1]
    assert theme.ACCENT_TINT != original[2]
    assert theme.STYLE != original[4]
    assert theme.ACCENT in theme.STYLE and theme.NIGHT in theme.STYLE
    theme.set_palette("moon", "night")
    assert (
        theme.ACCENT,
        theme.ACCENT_STRONG,
        theme.ACCENT_TINT,
        theme.ACCENT_INK,
        theme.STYLE,
    ) == original


@pytest.mark.parametrize("accent", ["moon", "gold", "rose", "mint"])
@pytest.mark.parametrize("base", ["night", "oled"])
def test_palette_contrast(accent, base):
    theme.set_palette(accent, base)
    assert contrast(theme.TEXT, theme.NIGHT) >= 7
    assert contrast(theme.ACCENT_INK, theme.ACCENT) >= 4.5
    assert contrast(theme.ACCENT_INK, theme.ACCENT_STRONG) >= 4.5
    if base == "oled":
        assert theme.NIGHT == "#000000"
        assert len({theme.NIGHT, theme.DUSK, theme.SURFACE, theme.RAISED, theme.LINE}) == 5
        assert contrast(theme.LINE, theme.NIGHT) > 1.3


def test_settings_apply_immediately_and_repaint_existing_widgets(qt_app, store):
    class PaintProbe(QWidget):
        painted = None

        def paintEvent(self, event):
            self.painted = theme.ACCENT

    probe = PaintProbe()
    probe.show()
    theme.apply_theme(qt_app, store)
    dialog = SettingsDialog(store)
    assert dialog.accent_combo.currentText() == "Ay mavisi"
    assert dialog.base_theme_combo.currentText() == "Gece"
    original_style = qt_app.styleSheet()
    dialog.accent_combo.setCurrentIndex(dialog.accent_combo.findData("mint"))
    dialog.base_theme_combo.setCurrentIndex(dialog.base_theme_combo.findData("oled"))
    qt_app.processEvents()
    assert store.setting("accent") == "mint"
    assert store.setting("base_theme") == "oled"
    assert qt_app.styleSheet() != original_style
    assert theme.ACCENT in qt_app.styleSheet()
    assert probe.painted == theme.ACCENT
    assert qt_app.palette().window().color().name() == "#000000"
    dialog.close()
    reopened = SettingsDialog(store)
    assert reopened.accent_combo.currentData() == "mint"
    assert reopened.base_theme_combo.currentData() == "oled"
    reopened.close()
    probe.close()


def test_saved_theme_applies_and_invalid_values_fall_back(qt_app, store):
    store.set_setting("accent", "gold")
    store.set_setting("base_theme", "oled")
    theme.apply_theme(qt_app, store)
    assert theme.NIGHT == "#000000" and theme.ACCENT == "#ffd58a"
    store.set_setting("accent", ["bad"])
    store.set_setting("base_theme", "bad")
    theme.apply_theme(qt_app, store)
    assert theme.ACCENT == "#a9b8ff" and theme.NIGHT == "#0b1020"


def test_no_import_time_copies_of_theme_colors():
    for path in Path(theme.__file__).parent.glob("*.py"):
        if path.name == "theme.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("theme"):
                assert not any(n.name.isupper() or n.name == "*" for n in node.names), path
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = [*node.args.defaults, *node.args.kw_defaults]
                for default in filter(None, defaults):
                    assert not any(
                        isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id == "theme"
                        and n.attr.isupper()
                        for n in ast.walk(default)
                    ), path
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                assert not any(
                    isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "theme"
                    and n.attr.isupper()
                    for n in ast.walk(node)
                ), path


def test_existing_and_new_icon_buttons_follow_palette(qt_app):
    old = motion.IconButton("Ayarlar", "gear")
    old.setCheckable(True)
    old.setChecked(True)
    moon = old.icon_color()
    theme.set_palette("mint", "oled")
    assert old.icon_color() == theme.ACCENT_STRONG != moon
    new = motion.IconButton("Ayarlar", "gear")
    new.setCheckable(True)
    new.setChecked(True)
    assert new.icon_color() == theme.ACCENT_STRONG
    old.close()
    new.close()


def test_window_refreshes_cached_artwork_links_and_toast(qt_app, store):
    from luna_iptv import icons
    from luna_iptv.window import MainWindow

    window = MainWindow(store)
    theme.apply_theme(qt_app, store)
    store.set_setting("accent", "rose")
    store.set_setting("base_theme", "oled")
    theme.apply_theme(qt_app, store)
    qt_app.processEvents()
    assert theme.ACCENT in window.hidden_categories_label.text()
    assert (
        window.guide_mark.pixmap().toImage()
        == icons.pixmap("guide", theme.ACCENT, 16, window.devicePixelRatioF()).toImage()
    )
    assert "#1b2444" not in window.toast.pill.styleSheet()
    assert f"QFrame#toastPill {{ background: {theme.RAISED};" in qt_app.styleSheet()
    window.close()


def test_guide_pulse_obeys_visibility_and_live_motion_changes(qt_app):
    motion.set_motion_level("full")
    grid = GuideGrid()
    assert not grid._pulse_timer.isActive()
    grid.show()
    assert grid._pulse_timer.isActive()
    for level in ("reduced", "off"):
        motion.set_motion_level(level)
        assert not grid._pulse_timer.isActive()
        motion.set_motion_level("full")
        assert grid._pulse_timer.isActive()
    grid.hide()
    assert not grid._pulse_timer.isActive()
    motion.set_motion_level("off")
    motion.set_motion_level("full")
    assert not grid._pulse_timer.isActive()
    grid.show()
    assert grid._pulse_timer.isActive()
    grid.close()
    assert not grid._pulse_timer.isActive()


def test_pulse_updates_only_a_narrow_line_strip(qt_app, monkeypatch):
    motion.set_motion_level("full")
    grid = GuideGrid()
    grid.resize(900, 400)
    grid.show()
    grid.scroll_to(datetime.now(timezone.utc))
    qt_app.processEvents()
    updates = []
    with monkeypatch.context() as patch:
        patch.setattr(grid.viewport(), "update", lambda *args: updates.append(args))
        grid._pulse_tick()
        assert len(updates) == 1
        (rect,) = updates[0]
        assert isinstance(rect, QRect)
        assert 0 < rect.width() <= 20
        assert rect.top() >= HEADER - 8
        assert rect.contains(round(grid.x_for(grid._now)), HEADER + 10)
        updates.clear()
        grid.set_day(datetime.now().date() - timedelta(days=1))
        updates.clear()
        grid._pulse_tick()
        assert not updates
    grid.close()


def test_pulse_breathes_every_2400ms():
    assert GuideGrid.pulse_alpha(0) == pytest.approx(GuideGrid.pulse_alpha(2400))
    assert GuideGrid.pulse_alpha(1200) > GuideGrid.pulse_alpha(0)
    assert 0 < GuideGrid.pulse_alpha(0) < GuideGrid.pulse_alpha(1200) < 100


def test_guide_paints_cached_logo_and_requests_only_visible_rows(qt_app, tmp_path, monkeypatch):
    cache = LogoCache(tmp_path / "library.sqlite3")
    pixmap = QPixmap(80, 30)
    pixmap.fill(QColor("#ed13d4"))
    url = "https://example.invalid/channel.png"
    cache._memory[url] = (pixmap, float("inf"))
    requests = []
    monkeypatch.setattr(cache, "request_visible", lambda urls, **kw: requests.append(list(urls)))
    grid = GuideGrid(cache)
    grid.resize(900, 220)
    grid.set_rows(
        [
            (
                Channel(
                    str(i), "Nova", "", logo=url if i == 0 else f"https://example.invalid/{i}.png"
                ),
                GuideIndex([]),
            )
            for i in range(30)
        ]
    )
    grid.show()
    qt_app.processEvents()
    grid._request_logos()
    assert requests[-1] == [grid.rows[i][0].logo for i in grid._visible_rows()]
    assert len(requests[-1]) < 30
    painted = grid.viewport().grab().toImage()
    ratio = painted.devicePixelRatio()
    assert (
        painted.pixelColor(round(LOGO_COLUMN / 2 * ratio), round((HEADER + ROW / 2) * ratio)).name()
        == "#ed13d4"
    )
    grid.close()
    cache.close()


def test_avatar_column_migrates_old_database_and_survives_reopening(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                color TEXT NOT NULL, kids INTEGER NOT NULL DEFAULT 0,
                protected INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL);
            INSERT INTO profiles VALUES (9, 'Eski', '#E8B04B', 1, 1, 4);
        """)
    db.close()
    store = Store(path)
    assert store.profile(9) == dict(
        id=9, name="Eski", color="#E8B04B", kids=True, protected=True, position=4, avatar=None
    )
    store.update_profile(9, avatar="fox")
    store.update_profile(9, name="Yeni")
    assert store.profile(9)["avatar"] == "fox"
    store.close()
    reopened = Store(path)
    assert reopened.profile(9)["avatar"] == "fox"
    reopened.update_profile(9, avatar=None)
    assert reopened.profile(9)["avatar"] is None
    reopened.close()


def test_editor_saves_and_restores_avatar_then_initial(qt_app, store):
    editor = ProfileEditor(store)
    assert len(editor.avatar_buttons) == 13
    editor.name.setText("Luna")
    editor.avatar_buttons["cat"].click()
    assert editor.preview.profile["avatar"] == "cat"
    assert editor.save()
    profile = store.profile(editor.profile_id)
    assert profile["avatar"] == "cat"
    reopened = ProfileEditor(store, profile)
    assert reopened.avatar_buttons["cat"].isChecked()
    reopened.avatar_buttons[None].click()
    assert reopened.save()
    assert store.profile(editor.profile_id)["avatar"] is None
    editor.close()
    reopened.close()


@pytest.mark.parametrize("avatar", AVATARS)
def test_avatar_painting_differs_from_initial_and_keeps_badges(qt_app, avatar):
    widget = ProfileAvatar(96)
    profile = dict(name="Luna", color="#7C8CF8", kids=True, protected=True)
    widget.set_profile(profile)
    initial = widget.grab().toImage()
    widget.set_profile(dict(profile, avatar=avatar))
    painted = widget.grab().toImage()
    assert initial != painted
    ratio = painted.devicePixelRatio()
    badge = QRect(round(69 * ratio), round(69 * ratio), round(25 * ratio), round(25 * ratio))
    assert initial.copy(badge) == painted.copy(badge)
    widget.close()
