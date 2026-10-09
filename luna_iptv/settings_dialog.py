"""Application settings that apply as soon as a choice changes."""

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
)

from . import theme
from .dialogs import text_label
from .media_dialog import _LANGUAGE_PREFERENCES
from .motion import IconButton, set_motion_level
from .online_settings import OnlineSettings
from .settings import (
    ACCENT_CHOICES,
    AUTOPLAY_CHOICES,
    BASE_THEME_CHOICES,
    MOTION_CHOICES,
    REFRESH_CHOICES,
    STARTUP_CHOICES,
    refresh_time,
    selected_setting,
)
from .updates import UPDATE_CHOICES


class SettingsDialog(QDialog, OnlineSettings):
    refresh_changed = Signal()

    def __init__(self, store, parent=None, *, tray_available=None):
        super().__init__(parent)
        self.store = store
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Ayarlar")
        self.resize(1000, 740)
        self.setMinimumWidth(920)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(text_label("Ayarlar", "heading"))

        columns = QHBoxLayout()
        columns.setSpacing(16)
        settings = QVBoxLayout()
        online = QVBoxLayout()
        columns.addLayout(settings, 1)
        columns.addLayout(online, 1)
        layout.addLayout(columns, 1)
        self._online_section(online)

        appearance = self._section(settings, "GÖRÜNÜM")
        self.accent_combo = self._choice(appearance, "Vurgu rengi", "accent", ACCENT_CHOICES)
        self.base_theme_combo = self._choice(appearance, "Tema", "base_theme", BASE_THEME_CHOICES)
        self.motion_combo = self._choice(appearance, "Hareket", "motion_level", MOTION_CHOICES)
        note = text_label("Az: gökyüzü ve logo sabit. Kapalı: tüm hareketler kapalı.", "faint")
        note.setWordWrap(True)
        appearance.addWidget(note)

        playback = self._section(settings, "OYNATMA TERCİHLERİ")
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

        startup = self._section(settings, "BAŞLANGIÇ")
        self.startup_combo = self._choice(startup, "Açılışta", "startup_action", STARTUP_CHOICES)
        updates = self._section(online, "GÜNCELLEME")
        self.refresh_combo = self._choice(
            updates, "Kaynakları ve rehberi otomatik yenile", "auto_refresh", REFRESH_CHOICES
        )
        row = QHBoxLayout()
        label = text_label("Saat", "muted")
        self.refresh_time = QTimeEdit(QTime.fromString(refresh_time(store), "HH:mm"))
        self.refresh_time.setDisplayFormat("HH:mm")
        self.refresh_time.setAccessibleName("Saat")
        label.setBuddy(self.refresh_time)
        self.refresh_time.setEnabled(self.refresh_combo.currentData() == "daily")
        self.refresh_time.timeChanged.connect(
            lambda value: self._save("auto_refresh_time", value.toString("HH:mm"))
        )
        self.refresh_combo.currentIndexChanged.connect(
            lambda _: self.refresh_time.setEnabled(self.refresh_combo.currentData() == "daily")
        )
        row.addWidget(label)
        row.addStretch()
        row.addWidget(self.refresh_time)
        updates.addLayout(row)
        self.episode_combo = self._choice(
            updates, "Yeni bölüm bildirimi", "new_episode_notifications", AUTOPLAY_CHOICES
        )
        self.update_combo = self._choice(
            updates, "Yeni sürümleri denetle", "update_check", UPDATE_CHOICES
        )
        self.close_to_tray = QCheckBox("Kapatınca tepsiye küçült")
        self.close_to_tray.setChecked(store.setting("close_to_tray", False) is True)
        available = (
            QSystemTrayIcon.isSystemTrayAvailable() if tray_available is None else tray_available
        )
        self.close_to_tray.setEnabled(available)
        self.close_to_tray.toggled.connect(lambda value: self._save("close_to_tray", value))
        startup.addWidget(self.close_to_tray)
        if not available:
            hint = text_label("Bu masaüstünde sistem tepsisi kullanılamıyor.", "faint")
            hint.setWordWrap(True)
            startup.addWidget(hint)
            self.close_to_tray.setToolTip(hint.text())
        settings.addStretch()
        online.addStretch()
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
        if key in {"auto_refresh", "auto_refresh_time"}:
            self.refresh_changed.emit()
        if key == "motion_level":
            set_motion_level(value)
        elif key in ("accent", "base_theme"):
            theme.apply_theme(QApplication.instance(), self.store)
