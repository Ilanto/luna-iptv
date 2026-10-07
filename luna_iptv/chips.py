"""Pill-shaped quick filters: the busiest choices in reach, the rest one search away."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .library import search_key


class ChoicePopup(QFrame):
    """A searchable list of every choice; picking one closes it."""

    chosen = Signal(str)

    def __init__(self, items, current, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("chipPopup")
        self.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Kategori ara…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Kategori ara")
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("chipList")
        self.list.setAccessibleName("Kategoriler")
        layout.addWidget(self.list, 1)
        for value, label, count in items:
            item = QListWidgetItem(f"{label}   {count:,}".replace(",", "."))
            item.setData(Qt.UserRole, value)
            item.setData(Qt.UserRole + 1, search_key(label))
            self.list.addItem(item)
            if value == current:
                self.list.setCurrentItem(item)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._pick_first_visible)
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        self.resize(320, 380)

    def _filter(self, text):
        key = search_key(text)
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(key) and key not in item.data(Qt.UserRole + 1))

    def _pick_first_visible(self):
        item = next(
            (
                self.list.item(r)
                for r in range(self.list.count())
                if not self.list.item(r).isHidden()
            ),
            None,
        )
        if item is not None:
            self._pick(item)

    def _pick(self, item):
        self.chosen.emit(item.data(Qt.UserRole))
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        self.search.setFocus()


class ChipBar(QWidget):
    """'All', the busiest choices as pills, and a button listing everything.

    ``set_items`` takes (value, label, count) triples; value "" means all.
    ``chosen`` reports the value the user picked; the owner applies it and
    calls ``set_items`` again with the new current value.
    """

    chosen = Signal(str)

    def __init__(self, all_label="Tümü", more_label="", limit=7, show_counts=False, parent=None):
        super().__init__(parent)
        self.all_label = all_label
        self.more_label = more_label
        self.limit = limit
        self.show_counts = show_counts
        self._items = []
        self._current = ""
        self._buttons = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.more_button = None

    def buttons(self):
        """Visible pills by value (for tests and keyboard focus)."""
        return dict(self._buttons)

    def _chip(self, value, text, tip=""):
        button = QPushButton(text)
        button.setObjectName("chip")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(30)
        if tip:
            button.setToolTip(tip)
        button.setAccessibleName(tip or text)
        button.clicked.connect(lambda checked=False, v=value: self.chosen.emit(v))
        self._group.addButton(button)
        self._buttons[value] = button
        self._layout.addWidget(button)
        return button

    def _label(self, label, count):
        return f"{label}  {count:,}".replace(",", ".") if self.show_counts else label

    def set_items(self, items, current=""):
        self._items = list(items)
        self._current = current
        for button in self._group.buttons():
            self._group.removeButton(button)
            button.deleteLater()
        self._buttons = {}
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None and item.widget() is not self.more_button:
                item.widget().deleteLater()
        total = sum(count for _, _, count in self._items)
        self._chip("", self._label(self.all_label, total))
        busiest = sorted(self._items, key=lambda item: (-item[2], item[1].casefold()))
        shown = busiest[: self.limit]
        if current and all(value != current for value, _, _ in shown):
            shown += [item for item in self._items if item[0] == current]
        for value, label, count in shown:
            self._chip(value, self._label(label, count), f"{label} · {count:,}".replace(",", "."))
        if self.more_label and len(self._items) > len(shown):
            if self.more_button is None:
                self.more_button = QPushButton(self.more_label + "  ▾")
                self.more_button.setObjectName("chipMore")
                self.more_button.setCursor(Qt.PointingHandCursor)
                self.more_button.setFixedHeight(30)
                self.more_button.setAccessibleName(self.more_label)
                self.more_button.clicked.connect(self.open_popup)
            self._layout.addWidget(self.more_button)
            self.more_button.show()
        elif self.more_button is not None:
            self.more_button.hide()
        self._layout.addStretch()
        button = self._buttons.get(current) or self._buttons[""]
        button.setChecked(True)

    def set_current(self, value):
        """Mark ``value`` chosen, bringing it into the row if it was in the list only."""
        self.set_items(self._items, value)

    def open_popup(self):
        popup = ChoicePopup(sorted(self._items, key=lambda i: i[1].casefold()), self._current, self)
        popup.chosen.connect(self.chosen)
        anchor = self.more_button or self
        popup.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        popup.show()
        return popup

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 30)
