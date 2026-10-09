"""Application settings that apply as soon as a choice changes."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QVBoxLayout

from . import theme
from .dialogs import text_label
from .media_dialog import _LANGUAGE_PREFERENCES
from .motion import IconButton, set_motion_level
from .settings import (
    ACCENT_CHOICES,
    AUTOPLAY_CHOICES,
    BASE_THEME_CHOICES,
    MOTION_CHOICES,
    STARTUP_CHOICES,
    selected_setting,
)


class SettingsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Ayarlar")
        self.resize(500, 470)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(text_label("Ayarlar", "heading"))

        appearance = self._section(layout, "GÖRÜNÜM")
        self.accent_combo = self._choice(appearance, "Vurgu rengi", "accent", ACCENT_CHOICES)
        self.base_theme_combo = self._choice(appearance, "Tema", "base_theme", BASE_THEME_CHOICES)
        self.motion_combo = self._choice(appearance, "Hareket", "motion_level", MOTION_CHOICES)
        note = text_label("Az: gökyüzü ve logo sabit. Kapalı: tüm hareketler kapalı.", "faint")
        note.setWordWrap(True)
        appearance.addWidget(note)

        playback = self._section(layout, "OYNATMA TERCİHLERİ")
        languages = (("Otomatik", "auto"), *_LANGUAGE_PREFERENCES)
        subtitles = (("Otomatik", "auto"), ("Kapalı", "off"), *_LANGUAGE_PREFERENCES)
        self.audio_combo = self._choice(
            playback, "Varsayılan ses dili", "audio_language", languages
        )
        self.subtitle_combo = self._choice(
            playback, "Varsayılan altyazı dili", "subtitle_language", subtitles
        )
        self.autoplay_combo = self._choice(
            playback, "Sonraki bölümü otomatik oynat", "autoplay_next", AUTOPLAY_CHOICES
        )
        note = text_label(
            "Kaynağa özel kayıtlı tercihler önceliklidir. "
            "Dil değişiklikleri sonraki oynatmada kullanılır.",
            "faint",
        )
        note.setWordWrap(True)
        playback.addWidget(note)

        startup = self._section(layout, "BAŞLANGIÇ")
        self.startup_combo = self._choice(startup, "Açılışta", "startup_action", STARTUP_CHOICES)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.addWidget(text_label("Değişiklikler anında kaydedilir.", "faint"))
        footer.addStretch()
        self.close_button = IconButton("Kapat", "close", label=True, size=15)
        self.close_button.setObjectName("ghost")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

    @staticmethod
    def _section(layout, title):
        panel = QFrame()
        panel.setObjectName("panel")
        content = QVBoxLayout(panel)
        content.setContentsMargins(18, 14, 18, 16)
        content.setSpacing(10)
        content.addWidget(text_label(title, "eyebrow"))
        layout.addWidget(panel)
        return content

    def _choice(self, layout, title, key, choices):
        row = QHBoxLayout()
        label = text_label(title, "muted")
        combo = QComboBox()
        combo.setAccessibleName(title)
        label.setBuddy(combo)
        for text, value in choices:
            combo.addItem(text, value)
        combo.setCurrentIndex(combo.findData(selected_setting(self.store, key, choices)))
        combo.currentIndexChanged.connect(lambda _: self._save(key, combo.currentData()))
        row.addWidget(label)
        row.addStretch()
        row.addWidget(combo)
        layout.addLayout(row)
        return combo

    def _save(self, key, value):
        self.store.set_setting(key, value)
        if key == "motion_level":
            set_motion_level(value)
        elif key in ("accent", "base_theme"):
            theme.apply_theme(QApplication.instance(), self.store)
