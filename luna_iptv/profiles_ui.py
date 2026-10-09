"""Profiles: a round avatar in the rail, the 'Kim izliyor?' picker and the profile editor."""

from PySide6.QtCore import QRectF, QSize, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
)

from . import icons, theme
from .dialogs import text_label
from .kids_limits import LIMIT_CHOICES, profile_limits

PROFILE_COLORS = ("#E8B04B", "#7C8CF8", "#4FC3A1", "#F07A8C", "#B48CF2", "#5BB8F0")
PROFILE_AVATARS = (
    ("Ay", "moon"),
    ("Yıldız", "star"),
    ("Roket", "rocket"),
    ("Kedi", "cat"),
    ("Tilki", "fox"),
    ("Baykuş", "owl"),
    ("Ayı", "bear"),
    ("Gezegen", "planet"),
    ("Kuyruklu yıldız", "comet"),
    ("Güneş", "sun"),
    ("Bulut", "cloud"),
    ("TV", "tv"),
)


def profile_flags(profile):
    flags = []
    if profile["kids"]:
        flags.append("Çocuk profili")
    if profile["protected"]:
        flags.append("PIN ile girilir")
    return " · ".join(flags)


class ProfileAvatar(QAbstractButton):
    """A coloured disc with an initial or picture, plus the kids/PIN badge."""

    def __init__(self, size=40, parent=None):
        super().__init__(parent)
        self.profile = None
        self.diameter = size
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_profile(self, profile):
        self.profile = profile
        name = profile["name"] if profile else ""
        self.setToolTip(f"Profil: {name}" if name else "Profil")
        self.setAccessibleName(f"Profil: {name}. Profil değiştir" if name else "Profil")
        self.update()

    def sizeHint(self):
        return QSize(self.diameter, self.diameter)

    def paintEvent(self, event):
        if self.profile is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        base = QColor(self.profile["color"])
        glow = QRadialGradient(rect.center().x() - rect.width() * 0.2, rect.top(), rect.width())
        glow.setColorAt(0.0, base.lighter(135))
        glow.setColorAt(1.0, base.darker(150))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(rect)
        if self.hasFocus() or self.underMouse() or self.isDown() or self.isChecked():
            painter.setPen(QColor(theme.ACCENT_STRONG))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect.adjusted(-1, -1, 1, 1))
        avatar = self.profile.get("avatar")
        if avatar in {value for _, value in PROFILE_AVATARS}:
            inner = rect.adjusted(
                rect.width() * 0.2, rect.height() * 0.2, -rect.width() * 0.2, -rect.height() * 0.2
            )
            pixmap = icons.pixmap(
                avatar, theme.TEXT, round(inner.width()), self.devicePixelRatioF()
            )
            painter.save()
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(inner, pixmap, QRectF(pixmap.rect()))
            painter.restore()
        else:
            font = QFont(self.font())
            font.setPixelSize(max(10, int(rect.height() * 0.44)))
            font.setWeight(QFont.Bold)
            painter.setFont(font)
            painter.setPen(QColor(theme.ACCENT_INK))
            initial = self.profile["name"][:1].upper()
            painter.drawText(rect, Qt.AlignCenter, initial)
        badge = max(12.0, rect.width() * 0.34)
        corner = QRectF(rect.right() - badge + 2, rect.bottom() - badge + 2, badge, badge)
        mark = (
            "star-filled" if self.profile["kids"] else "lock" if self.profile["protected"] else ""
        )
        if mark:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.NIGHT))
            painter.drawEllipse(corner)
            ratio = self.devicePixelRatioF()
            pixmap = icons.pixmap(mark, theme.GOLD, int(badge * 0.62), ratio)
            inner = corner.adjusted(badge * 0.19, badge * 0.19, -badge * 0.19, -badge * 0.19)
            painter.drawPixmap(inner, pixmap, QRectF(pixmap.rect()))


class ProfilePicker(QDialog):
    """'Kim izliyor?': one large avatar per profile."""

    def __init__(self, profiles, current_id=None, parent=None):
        super().__init__(parent)
        self.setObjectName("profilePicker")
        self.setWindowTitle("Kim izliyor?")
        self.chosen = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 26)
        layout.setSpacing(20)
        heading = text_label("Kim izliyor?", "display")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)
        row = QHBoxLayout()
        row.setSpacing(26)
        row.addStretch()
        self.avatars = {}
        for profile in profiles:
            column = QVBoxLayout()
            column.setSpacing(8)
            avatar = ProfileAvatar(96)
            avatar.set_profile(profile)
            avatar.clicked.connect(lambda checked=False, pid=profile["id"]: self.pick(pid))
            column.addWidget(avatar, 0, Qt.AlignHCenter)
            name = text_label(profile["name"], "profileName")
            name.setAlignment(Qt.AlignCenter)
            column.addWidget(name)
            flags = text_label(profile_flags(profile), "faint")
            flags.setAlignment(Qt.AlignCenter)
            column.addWidget(flags)
            row.addLayout(column)
            self.avatars[profile["id"]] = avatar
        row.addStretch()
        layout.addLayout(row)
        if current_id in self.avatars:
            self.avatars[current_id].setFocus()

    def pick(self, profile_id):
        self.chosen = profile_id
        self.accept()


class ProfileEditor(QDialog):
    """Name, colour, picture and the two switches of one profile."""

    def __init__(self, store, profile=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Profili düzenle" if profile else "Yeni profil")
        self._store = store
        self._profile = profile
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 18)
        layout.setSpacing(12)
        top = QHBoxLayout()
        self.preview = ProfileAvatar(64)
        top.addWidget(self.preview)
        top.addWidget(text_label(self.windowTitle(), "heading"), 1)
        layout.addLayout(top)
        self.name = QLineEdit(profile["name"] if profile else "")
        self.name.setPlaceholderText("Profil adı")
        self.name.setAccessibleName("Profil adı")
        self.name.setMaxLength(40)
        self.name.textChanged.connect(self._preview)
        layout.addWidget(self.name)
        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        self.colors = QButtonGroup(self)
        current = profile["color"] if profile else PROFILE_COLORS[len(store.profiles()) % 6]
        for color in PROFILE_COLORS:
            button = QPushButton()
            button.setCheckable(True)
            button.setFixedSize(30, 30)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"Renk {color}")
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; border-radius: 15px; border: 2px solid "
                f"transparent; }} QPushButton:checked {{ border-color: {theme.TEXT}; }}"
            )
            button.setProperty("color", color)
            button.setChecked(color.casefold() == current.casefold())
            button.toggled.connect(self._preview)
            self.colors.addButton(button)
            swatches.addWidget(button)
        if self.colors.checkedButton() is None:
            self.colors.buttons()[0].setChecked(True)
        swatches.addStretch()
        layout.addLayout(swatches)
        layout.addWidget(text_label("PROFİL RESMİ", "eyebrow"))
        pictures = QHBoxLayout()
        pictures.setSpacing(3)
        self.avatars = QButtonGroup(self)
        self.avatar_buttons = {}
        current_avatar = profile.get("avatar") if profile else None
        for title, avatar in (("Harf", None), *PROFILE_AVATARS):
            button = ProfileAvatar(34) if avatar else QPushButton("Harf")
            button.setCheckable(True)
            button.setProperty("avatar", avatar)
            button.setAccessibleName(title)
            button.setToolTip(title)
            if avatar is None:
                button.setObjectName("chip")
                button.setFixedSize(56, 34)
            self.avatars.addButton(button)
            self.avatar_buttons[avatar] = button
            pictures.addWidget(button)
        self.avatar_buttons.get(current_avatar, self.avatar_buttons[None]).setChecked(True)
        pictures.addStretch()
        layout.addLayout(pictures)
        self.kids = QCheckBox("Çocuk profili (kilitli içerik hiç görünmez)")
        self.kids.setChecked(bool(profile and profile["kids"]))
        self.kids.toggled.connect(self._preview)
        layout.addWidget(self.kids)
        self.protected = QCheckBox("Bu profile girerken PIN sor")
        self.protected.setChecked(bool(profile and profile["protected"]))
        self.protected.toggled.connect(self._preview)
        layout.addWidget(self.protected)
        self.limits_box = QFrame()
        limits_layout = QVBoxLayout(self.limits_box)
        limits_layout.setContentsMargins(0, 0, 0, 0)
        limits = (
            profile_limits(store, profile["id"])
            if profile
            else {"minutes": 0, "start": None, "end": "07:00"}
        )
        limits_layout.addWidget(text_label("Günlük süre", "muted"))
        self.daily_limit = QComboBox()
        self.daily_limit.setAccessibleName("Günlük süre")
        for label, minutes in LIMIT_CHOICES:
            self.daily_limit.addItem(label, minutes)
        self.daily_limit.setCurrentIndex(self.daily_limit.findData(limits["minutes"]))
        limits_layout.addWidget(self.daily_limit)
        bedtime_row = QHBoxLayout()
        bedtime_row.addWidget(text_label("Yatma saati", "muted"))
        self.bedtime = QComboBox()
        self.bedtime.setAccessibleName("Yatma saati")
        self.bedtime.addItems(["Yok", "Saat aralığı"])
        self.bedtime.setCurrentIndex(1 if limits["start"] else 0)
        bedtime_row.addWidget(self.bedtime)
        self.bedtime_start = QTimeEdit(QTime.fromString(limits["start"] or "21:00", "HH:mm"))
        self.bedtime_end = QTimeEdit(QTime.fromString(limits["end"], "HH:mm"))
        for widget, label in ((self.bedtime_start, "Başlangıç"), (self.bedtime_end, "Bitiş")):
            widget.setDisplayFormat("HH:mm")
            widget.setAccessibleName(label)
            widget.setEnabled(bool(limits["start"]))
            self.bedtime.currentIndexChanged.connect(
                lambda index, w=widget: w.setEnabled(index == 1)
            )
            bedtime_row.addWidget(widget)
        limits_layout.addLayout(bedtime_row)
        layout.addWidget(self.limits_box)
        self.limits_box.setVisible(self.kids.isChecked())
        self.kids.toggled.connect(self.limits_box.setVisible)
        if not store.pin_hash():
            hint = text_label(
                "Kilitler ve PIN koruması için önce Ebeveyn denetimi'nden bir PIN belirle.",
                "faint",
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)
        self.error = text_label("", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        buttons.addButton("Vazgeç", QDialogButtonBox.RejectRole)
        self.save_button = buttons.addButton("Kaydet", QDialogButtonBox.AcceptRole)
        self.save_button.setObjectName("primary")
        self.save_button.clicked.disconnect()
        self.save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.avatars.buttonToggled.connect(self._preview)
        self.setFixedWidth(560)
        self._preview()

    def values(self):
        return dict(
            name=self.name.text().strip(),
            color=self.colors.checkedButton().property("color"),
            kids=self.kids.isChecked(),
            protected=self.protected.isChecked(),
            avatar=self.avatars.checkedButton().property("avatar"),
        )

    def _preview(self, *_):
        values = self.values()
        self.preview.set_profile(dict(values, name=values["name"] or "?"))
        for avatar, button in self.avatar_buttons.items():
            if avatar:
                button.set_profile(dict(values, avatar=avatar, kids=False, protected=False))
                title = next(title for title, value in PROFILE_AVATARS if value == avatar)
                button.setToolTip(title)
                button.setAccessibleName(title)

    def save(self):
        values = self.values()
        start = self.bedtime_start.time().toString("HH:mm") if self.bedtime.currentIndex() else None
        end = self.bedtime_end.time().toString("HH:mm")
        if values["kids"] and start == end:
            self.error.setText("Yatma saatinin başlangıcı ve bitişi farklı olmalı.")
            return False
        try:
            if self._profile is None:
                self.profile_id = self._store.create_profile(
                    values.pop("name"), values.pop("color"), **values
                )
            else:
                self.profile_id = self._profile["id"]
                self._store.update_profile(self.profile_id, **values)
        except ValueError as error:
            self.error.setText(str(error))
            return False
        self._store.set_setting(
            f"kids_limits:{self.profile_id}",
            {
                "minutes": self.daily_limit.currentData(),
                "start": start,
                "end": end,
            },
        )
        self.accept()
        return True


class ProfilesDialog(QDialog):
    """Every profile with its switches; add, edit or delete."""

    changed = Signal()
    deleting = Signal(int)  # before the rows go, so playback can be saved first

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Profiller")
        self.resize(500, 440)
        self._store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.addWidget(text_label("Profiller", "heading"))
        intro = text_label(
            "Her profilin kendi favorileri, klasörleri, geçmişi ve hatırlatıcıları olur. "
            "Kaynaklar ve ayarlar ortaktır.",
            "muted",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        layout.addLayout(self.rows)
        layout.addStretch()
        actions = QHBoxLayout()
        self.add_button = QPushButton("Profil ekle")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(lambda: self.edit(None))
        actions.addWidget(self.add_button)
        actions.addStretch()
        close = QPushButton("Kapat")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        profiles = self._store.profiles()
        for profile in profiles:
            panel = QFrame()
            panel.setObjectName("panel")
            row = QHBoxLayout(panel)
            avatar = ProfileAvatar(40)
            avatar.set_profile(profile)
            avatar.setFocusPolicy(Qt.NoFocus)
            row.addWidget(avatar)
            text = QVBoxLayout()
            active = " (şu an)" if profile["id"] == self._store.profile_id else ""
            text.addWidget(text_label(profile["name"] + active))
            flags = profile_flags(profile)
            if flags:
                text.addWidget(text_label(flags, "faint"))
            row.addLayout(text, 1)
            edit = QPushButton("Düzenle")
            edit.setAccessibleName(f"Profili düzenle: {profile['name']}")
            edit.clicked.connect(lambda checked=False, p=profile: self.edit(p))
            row.addWidget(edit)
            remove = QPushButton("Sil")
            remove.setObjectName("danger")
            remove.setAccessibleName(f"Profili sil: {profile['name']}")
            remove.setEnabled(len(profiles) > 1)
            remove.clicked.connect(lambda checked=False, p=profile: self.remove(p))
            row.addWidget(remove)
            self.rows.addWidget(panel)

    def edit(self, profile):
        dialog = ProfileEditor(self._store, profile, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def remove(self, profile):
        answer = QMessageBox.question(
            self,
            "Profili sil",
            f"“{profile['name']}” silinsin mi? Favorileri, klasörleri, geçmişi ve "
            "hatırlatıcıları da silinir.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
        self.deleting.emit(profile["id"])
        self._store.delete_profile(profile["id"])
        self.refresh()
        self.changed.emit()
        return True
