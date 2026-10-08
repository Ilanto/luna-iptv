"""The parental PIN: asked every time for locked content and for anything that changes Luna."""

import time

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import icons, theme
from .dialogs import text_label
from .library import search_key
from .parental import hash_pin, valid_pin, verify_pin

ATTEMPTS = 5
LOCKOUT_SECONDS = 30
# Shared by every PIN prompt, so closing and reopening the prompt does not reset the count.
_failures = {"count": 0, "until": 0.0}


def reset_failures():
    _failures.update(count=0, until=0.0)


def pin_field(placeholder="PIN"):
    field = QLineEdit()
    field.setObjectName("pinField")
    field.setEchoMode(QLineEdit.Password)
    field.setAlignment(Qt.AlignCenter)
    field.setMaxLength(8)
    field.setPlaceholderText(placeholder)
    field.setAccessibleName(placeholder)
    field.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,8}"), field))
    return field


def lock_badge(size=44):
    badge = QLabel()
    badge.setObjectName("pinBadge")
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignCenter)
    badge.setPixmap(icons.pixmap("lock", theme.GOLD, size // 2, badge.devicePixelRatioF()))
    return badge


class PinDialog(QDialog):
    """Asks for the PIN; five wrong tries close the door for thirty seconds."""

    def __init__(self, store, reason, parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self.setObjectName("pinDialog")
        self.setWindowTitle("PIN gerekli")
        self._store = store
        self._clock = clock
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)
        layout.addWidget(lock_badge(), 0, Qt.AlignHCenter)
        title = text_label("PIN gerekli", "heading")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        self.reason = text_label(reason, "muted")
        self.reason.setAlignment(Qt.AlignCenter)
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.field = pin_field()
        self.field.returnPressed.connect(self.try_pin)
        layout.addWidget(self.field)
        self.error = text_label("", "error")
        self.error.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.cancel_button = buttons.addButton("Vazgeç", QDialogButtonBox.RejectRole)
        self.open_button = buttons.addButton("Aç", QDialogButtonBox.AcceptRole)
        self.open_button.setObjectName("primary")
        self.open_button.setDefault(True)
        buttons.rejected.connect(self.reject)
        self.open_button.clicked.disconnect()
        self.open_button.clicked.connect(self.try_pin)
        layout.addWidget(buttons)
        self.setFixedWidth(360)
        self._refresh_lockout()

    def _refresh_lockout(self):
        wait = _failures["until"] - self._clock()
        locked = wait > 0
        self.field.setEnabled(not locked)
        self.open_button.setEnabled(not locked)
        if locked:
            self.error.setText(f"Çok fazla yanlış deneme. {int(wait) + 1} saniye sonra dene.")
        return locked

    def try_pin(self):
        if self._refresh_lockout():
            return False
        if verify_pin(self.field.text(), self._store.pin_hash()):
            reset_failures()
            self.accept()
            return True
        _failures["count"] += 1
        self.field.clear()
        if _failures["count"] >= ATTEMPTS:
            _failures.update(count=0, until=self._clock() + LOCKOUT_SECONDS)
            self._refresh_lockout()
        else:
            left = ATTEMPTS - _failures["count"]
            self.error.setText(f"PIN yanlış. {left} deneme hakkın kaldı.")
        return False


def ask_pin(store, reason, parent=None):
    """True when no PIN is set or the right one was typed. Never remembered between asks."""
    if not store.pin_hash():
        return True
    return PinDialog(store, reason, parent).exec() == QDialog.Accepted


class NewPinDialog(QDialog):
    """Choose a PIN, typed twice."""

    def __init__(self, parent=None, *, title="PIN belirle"):
        super().__init__(parent)
        self.setObjectName("pinDialog")
        self.setWindowTitle(title)
        self.pin = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)
        layout.addWidget(lock_badge(), 0, Qt.AlignHCenter)
        heading = text_label(title, "heading")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)
        hint = text_label(
            "4 ile 8 rakam arası. Çocukların tahmin edemeyeceği bir PIN seç.", "muted"
        )
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        self.first = pin_field("Yeni PIN")
        self.second = pin_field("Yeni PIN (tekrar)")
        self.second.returnPressed.connect(self.save)
        layout.addWidget(self.first)
        layout.addWidget(self.second)
        self.error = text_label("", "error")
        self.error.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        buttons.addButton("Vazgeç", QDialogButtonBox.RejectRole)
        self.save_button = buttons.addButton("Kaydet", QDialogButtonBox.AcceptRole)
        self.save_button.setObjectName("primary")
        self.save_button.clicked.disconnect()
        self.save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setFixedWidth(360)

    def save(self):
        if not valid_pin(self.first.text()):
            self.error.setText("PIN 4 ile 8 rakam arası olmalı.")
            return False
        if self.first.text() != self.second.text():
            self.error.setText("İki PIN aynı değil.")
            self.second.clear()
            return False
        self.pin = self.first.text()
        self.accept()
        return True


class ParentalDialog(QDialog):
    """Set the PIN, choose locked categories and channels, or turn the control off."""

    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ebeveyn denetimi")
        self.resize(560, 620)
        self._store = store
        self._filling = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(lock_badge(40))
        titles = QVBoxLayout()
        titles.addWidget(text_label("Ebeveyn denetimi", "heading"))
        self.summary = text_label("", "muted")
        self.summary.setWordWrap(True)
        titles.addWidget(self.summary)
        head.addLayout(titles, 1)
        layout.addLayout(head)
        self.setup_button = QPushButton("PIN belirle ve başla")
        self.setup_button.setObjectName("primary")
        self.setup_button.clicked.connect(self.set_up)
        layout.addWidget(self.setup_button, 0, Qt.AlignLeft)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Kategori ara…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Kategori ara")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setObjectName("lockTree")
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("Kilitli kategoriler")
        self.tree.itemChanged.connect(self._item_changed)
        layout.addWidget(self.tree, 1)
        tools = QHBoxLayout()
        self.adult_button = QPushButton("Yetişkin kategorilerini bul")
        self.adult_button.clicked.connect(self.find_adult)
        self.change_button = QPushButton("PIN'i değiştir")
        self.change_button.clicked.connect(self.change_pin)
        self.off_button = QPushButton("Denetimi kapat")
        self.off_button.setObjectName("danger")
        self.off_button.clicked.connect(self.turn_off)
        for button in (self.adult_button, self.change_button):
            tools.addWidget(button)
        tools.addStretch()
        tools.addWidget(self.off_button)
        layout.addLayout(tools)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Kapat")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    @property
    def active(self):
        return bool(self._store.pin_hash())

    def refresh(self):
        active = self.active
        for widget in (self.search, self.tree, self.adult_button, self.change_button):
            widget.setVisible(active)
        self.off_button.setVisible(active)
        self.setup_button.setVisible(not active)
        if not active:
            self.summary.setText(
                "Bir PIN belirle. Seçtiğin kategoriler ve yayınlar her açılışta PIN ister; "
                "ayarlar, kaynaklar, yedekler ve profiller de PIN'le korunur. "
                "Çocuk profillerinde kilitli içerik hiç görünmez."
            )
            return
        groups = self._store.locked_groups()
        channels = self._store.locked_channels()
        self.summary.setText(
            f"{len(groups)} kategori ve {len(channels)} yayın kilitli. "
            "Kilitli içerik her açılışta PIN ister."
        )
        self._fill(groups, channels)

    def _fill(self, groups, channels):
        self._filling = True
        self.tree.clear()
        names = {source["id"]: source["name"] for source in self._store.sources()}
        parents = {}
        for source_id, group, count in self._store.channel_groups():
            parent = parents.get(source_id)
            if parent is None:
                parent = QTreeWidgetItem([names.get(source_id, source_id)])
                parent.setFlags(Qt.ItemIsEnabled)
                self.tree.addTopLevelItem(parent)
                parents[source_id] = parent
            item = QTreeWidgetItem([f"{group}   {count:,}".replace(",", ".")])
            item.setData(0, Qt.UserRole, ("group", source_id, group))
            item.setData(0, Qt.UserRole + 1, search_key(group))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if (source_id, group) in groups else Qt.Unchecked)
            parent.addChild(item)
        if channels:
            by_id = {channel.id: channel for channel in self._store.channels()}
            parent = QTreeWidgetItem(["Tek tek kilitlenen yayınlar"])
            parent.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(parent)
            for channel_id in sorted(channels, key=lambda c: by_id[c].name if c in by_id else c):
                channel = by_id.get(channel_id)
                item = QTreeWidgetItem([channel.name if channel else channel_id])
                item.setData(0, Qt.UserRole, ("channel", channel_id, ""))
                item.setData(0, Qt.UserRole + 1, search_key(item.text(0)))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked)
                parent.addChild(item)
        self.tree.expandAll()
        self._filling = False
        self._filter(self.search.text())

    def _filter(self, text):
        key = search_key(text)
        for row in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(row)
            shown = 0
            for child_row in range(parent.childCount()):
                child = parent.child(child_row)
                hidden = bool(key) and key not in child.data(0, Qt.UserRole + 1)
                child.setHidden(hidden)
                shown += not hidden
            parent.setHidden(shown == 0)

    def _item_changed(self, item, column):
        if self._filling or item.data(0, Qt.UserRole) is None:
            return
        kind, identity, group = item.data(0, Qt.UserRole)
        locked = item.checkState(0) == Qt.Checked
        if kind == "group":
            self._store.set_group_locked(identity, group, locked)
        else:
            self._store.set_channel_locked(identity, locked)
        self._update_summary()
        self.changed.emit()

    def _update_summary(self):
        self.summary.setText(
            f"{len(self._store.locked_groups())} kategori ve "
            f"{len(self._store.locked_channels())} yayın kilitli. "
            "Kilitli içerik her açılışta PIN ister."
        )

    def set_up(self):
        dialog = NewPinDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return False
        self._store.set_pin_hash(hash_pin(dialog.pin))
        found = self._store.lock_adult_groups()
        self.refresh()
        self.changed.emit()
        if found:
            self.summary.setText(
                f"PIN kaydedildi. {found} yetişkin kategorisi kendiliğinden kilitlendi; "
                "listeden değiştirebilirsin."
            )
        return True

    def find_adult(self):
        found = self._store.lock_adult_groups()
        self.refresh()
        if found:
            self.changed.emit()
        self.summary.setText(
            f"{found} yeni yetişkin kategorisi kilitlendi."
            if found
            else "Yeni yetişkin kategorisi bulunmadı. Kendin işaretleyebilirsin."
        )
        return found

    def change_pin(self):
        dialog = NewPinDialog(self, title="PIN'i değiştir")
        if dialog.exec() == QDialog.Accepted:
            self._store.set_pin_hash(hash_pin(dialog.pin))
            self.summary.setText("Yeni PIN kaydedildi.")

    def turn_off(self):
        answer = QMessageBox.question(
            self,
            "Denetimi kapat",
            "PIN silinsin mi? Kilit seçimlerin saklanır ama PIN belirleyene kadar etkisiz kalır.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
        self._store.set_pin_hash(None)
        reset_failures()
        self.refresh()
        self.changed.emit()
        return True
