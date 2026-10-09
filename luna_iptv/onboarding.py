"""A dismissible first-library guide, driven by real saved setup state."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout

from . import theme
from .dialogs import text_label


class OnboardingCard(QFrame):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.current_step = 0
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(text_label("Luna'ya yerleşelim", "title"))
        self.steps = [
            text_label(text)
            for text in (
                "1 Kaynağını ekle",
                "2 Rehberini bağla (isteğe bağlı)",
                "3 Favorilerini seç",
            )
        ]
        for step in self.steps:
            layout.addWidget(step)
        row = QHBoxLayout()
        self.primary_button = QPushButton()
        self.primary_button.setObjectName("primary")
        self.primary_button.clicked.connect(self.activate_step)
        row.addWidget(self.primary_button)
        self.guide_skip = QPushButton("Rehberi atla")
        self.guide_skip.setObjectName("ghost")
        self.guide_skip.clicked.connect(self.skip_guide)
        row.addWidget(self.guide_skip)
        row.addStretch()
        self.skip_button = QPushButton("Geç")
        self.skip_button.setObjectName("ghost")
        self.skip_button.setCursor(Qt.PointingHandCursor)
        self.skip_button.clicked.connect(self.skip)
        row.addWidget(self.skip_button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        store = self.window.store
        sources = store.sources()
        if (
            not sources
            and not store.setting("onboarding_done", False)
            and not store.setting("onboarding_started", False)
        ):
            store.set_setting("onboarding_started", True)
        completed = [
            bool(sources),
            any(s.get("epg_url") for s in sources)
            or bool(store.setting("onboarding_guide_skipped", False)),
            bool(store.favorites()),
        ]
        self.current_step = next((i for i, done in enumerate(completed) if not done), 3)
        if all(completed) and not store.setting("onboarding_done", False):
            store.set_setting("onboarding_done", True)
        visible = store.setting("onboarding_started", False) and not store.setting(
            "onboarding_done", False
        )
        self.setVisible(bool(visible))
        for index, label in enumerate(self.steps):
            color = theme.ACCENT if index == self.current_step else theme.TEXT_SOFT
            label.setStyleSheet(
                f"color: {color}; font-weight: {'600' if index == self.current_step else '400'};"
            )
            label.setAccessibleDescription(
                "Tamamlandı"
                if completed[index]
                else "Şimdiki adım"
                if index == self.current_step
                else "Sıradaki adım"
            )
        self.primary_button.setText(
            ("Kaynak ekle", "Rehber bağla", "Favorilerini seç", "Tamamlandı")[self.current_step]
        )
        self.guide_skip.setVisible(self.current_step == 1)

    def activate_step(self):
        if self.current_step == 0:
            self.window.add_source()
        elif self.current_step == 1:
            if not self.window.source_combo.currentData():
                source = self.window.store.sources()[0]
                self.window.source_combo.setCurrentIndex(
                    self.window.source_combo.findData(source["id"])
                )
            self.window.configure_guide()
        elif self.current_step == 2:
            self.window.set_section("live")
            self.window.status("Sevdiğin yayını seçip yıldız düğmesiyle favorilerine ekle.")
        if not self.window._closed:
            self.refresh()

    def skip_guide(self):
        self.window.store.set_setting("onboarding_guide_skipped", True)
        self.refresh()

    def skip(self):
        self.window.store.set_setting("onboarding_done", True)
        self.hide()
