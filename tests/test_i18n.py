"""Language selection, catalog integrity and real offscreen UI text."""

import importlib.util
import json
import subprocess
from datetime import date
from pathlib import Path
from string import Formatter

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_translation_module_exists():
    assert importlib.util.find_spec("luna_iptv.i18n") is not None


@pytest.fixture
def language():
    from luna_iptv.i18n import set_language

    yield set_language
    set_language("tr")


def test_language_identity_fallback_and_interpolation(language):
    from luna_iptv.i18n import _

    language("tr")
    assert _("Ayarlar") == "Ayarlar"
    language("en")
    assert _("Ayarlar") == "Settings"
    assert _("{name} profili açık.").format(name="Ada") == "Ada's profile is active."
    assert _("Uncatalogued user data") == "Uncatalogued user data"
    language("unsupported")
    assert _("Ayarlar") == "Ayarlar"


def test_dates_numbers_and_plurals(language):
    from luna_iptv.i18n import _n, day_label, format_number, month_name

    today = date(2026, 10, 9)
    language("tr")
    assert day_label(today, today=today) == "Bugün"
    assert day_label(date(2026, 10, 12), today=today) == "Pzt 12"
    assert format_number(1.5) == "1,5"
    assert _n("{count} kanal", "{count} kanallar", 3).format(count=3) == "3 kanal"
    language("en")
    assert day_label(today, today=today) == "Today"
    assert day_label(date(2026, 10, 10), today=today) == "Tomorrow"
    assert day_label(date(2026, 10, 12), today=today) == "Mon 12"
    assert month_name(today) == "October"
    assert format_number(1.5) == "1.5"
    assert _n("{count} kanal", "{count} kanallar", 1).format(count=1) == "1 channel"
    assert _n("{count} kanal", "{count} kanallar", 3).format(count=3) == "3 channels"


def test_catalog_checker_and_placeholders():
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/i18n_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    catalog = json.loads((ROOT / "luna_iptv/locale/en.json").read_text())
    formatter = Formatter()

    def fields(text):
        return sorted(
            (field, spec, conversion)
            for _, field, spec, conversion in formatter.parse(text)
            if field is not None
        )

    for source, translated in catalog.items():
        assert fields(source) == fields(translated), source


def test_settings_language_is_saved_for_restart(qt_app, tmp_path, language):
    from luna_iptv.i18n import _
    from luna_iptv.settings_dialog import SettingsDialog
    from luna_iptv.storage import Store

    language("tr")
    store = Store(tmp_path / "library.sqlite3")
    dialog = SettingsDialog(store, tray_available=False)
    try:
        dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("en"))
        assert store.setting("language") == "en"
        assert _("Ayarlar") == "Ayarlar"
        assert dialog.language_toast.text == ("Dil değişikliği yeniden başlatınca uygulanır.")
        language("en")
        other = SettingsDialog(store, tray_available=False)
        try:
            assert other.motion_combo.itemText(0) == "Full"
            assert other.motion_combo.itemData(0) == "full"
        finally:
            other.close()
    finally:
        dialog.close()
        store.close()


def test_english_windows_have_translated_visible_text(qt_app, tmp_path, language):
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel
    from shiboken6 import isValid

    from luna_iptv.logos import LogoCache
    from luna_iptv.media_dialog import MediaDetailDialog
    from luna_iptv.models import Channel
    from luna_iptv.parental_ui import ParentalDialog
    from luna_iptv.profiles_ui import ProfilesDialog
    from luna_iptv.settings_dialog import SettingsDialog
    from luna_iptv.storage import Store
    from luna_iptv.window import MainWindow

    language("en")
    store = Store(tmp_path / "library.sqlite3")
    store.update_profile(store.profile_id, name="Ada", color="#E8B04B")
    cache = LogoCache(tmp_path / "posters.sqlite3")
    windows = []
    try:
        windows = [
            (MainWindow(store), "Home"),
            (SettingsDialog(store, tray_available=False), "Settings"),
            (ProfilesDialog(store), "Profiles"),
            (ParentalDialog(store), "Parental controls"),
            (
                MediaDetailDialog(Channel("film", "Sample", "file:///sample", kind="movie"), cache),
                "Play",
            ),
        ]
        for window, expected in windows:
            window.show()
            qt_app.processEvents()
            assert not window.grab().isNull()
            texts = [window.windowTitle()]
            for widget in window.findChildren(QLabel) + window.findChildren(QAbstractButton):
                if widget.isVisibleTo(window):
                    texts.extend((widget.text(), widget.toolTip(), widget.accessibleName()))
            for widget in window.findChildren(QComboBox):
                if widget.isVisibleTo(window):
                    texts.extend(widget.itemText(i) for i in range(widget.count()))
            joined = "\n".join(texts)
            assert expected in joined, joined
            assert not any(letter in joined for letter in "ğşıİ"), joined
            window.hide()
    finally:
        for window, _expected in reversed(windows):
            if isValid(window):
                window.close()
        qt_app.processEvents()
        cache.close()
        store.close()


def test_media_info_and_validation_messages_follow_language(tmp_path, language):
    from luna_iptv.media_info import MediaInfo
    from luna_iptv.storage import Store

    language("en")
    info = MediaInfo()
    assert info.video_codec == "No information"
    store = Store(tmp_path / "library.sqlite3")
    try:
        with pytest.raises(ValueError, match="Folder names"):
            store.create_folder("")
    finally:
        store.close()


def test_checker_reports_missing_unused_and_deferred_labels(tmp_path):
    import sys

    source = tmp_path / "source"
    source.mkdir()
    (source / "view.py").write_text('_("Eksik")\nN_("Sonra")\n_n("Tek", "Çok", 2)\n_(user_data)\n')
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"Sonra": "Later", "Tek": "One", "Eski": "Old"}))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/i18n_check.py"),
            "--root",
            str(source),
            "--catalog",
            str(catalog),
            "--list",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Missing: 'Eksik'" in result.stdout
    assert "Missing: 'Çok'" in result.stdout
    assert "Unused: 'Eski'" in result.stdout
    assert "Missing: 'Sonra'" not in result.stdout


def test_media_numbers_follow_language(language):
    from luna_iptv.media_info import _decimal

    language("tr")
    assert _decimal(23.976) == "23,98"
    language("en")
    assert _decimal(23.976) == "23.98"
    assert _decimal(24.0) == "24"
