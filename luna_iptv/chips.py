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

from .i18n import _, format_number
from .library import search_key


class ChoicePopup(QFrame):
    """A searchable list of every choice; picking one closes it."""

    chosen = Signal(str)
    edit_requested = Signal()

    def __init__(self, items, current, parent=None, *, editable=False):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("chipPopup")
        self.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Kategori ara…"))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(_("Kategori ara"))
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("chipList")
        self.list.setAccessibleName(_("Kategoriler"))
        layout.addWidget(self.list, 1)
        for value, label, count in items:
            item = QListWidgetItem(f"{label}   {format_number(count, decimals=0, grouping=True)}")
            item.setData(Qt.UserRole, value)
            item.setData(Qt.UserRole + 1, search_key(label))
            self.list.addItem(item)
            if value == current:
                self.list.setCurrentItem(item)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._pick_first_visible)
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        if editable:
            self.edit_button = QPushButton(_("Kategorileri düzenle…"))
            self.edit_button.setObjectName("ghost")
            self.edit_button.clicked.connect(self._edit)
            layout.addWidget(self.edit_button)
        self.resize(320, 380)

    def _edit(self):
        self.close()
        self.edit_requested.emit()

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
    edit_requested = Signal()

    def __init__(
        self,
        all_label=None,
        more_label="",
        limit=7,
        show_counts=False,
        parent=None,
        *,
        sort_by_count=True,
        editable=False,
    ):
        super().__init__(parent)
        self.all_label = _("Tümü") if all_label is None else all_label
        self.more_label = more_label
        self.limit = limit
        self.show_counts = show_counts
        self.sort_by_count = sort_by_count
        self._total = None
        self._items = []
        self._current = ""
        self._buttons = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.more_button = None
        self.edit_button = None
        if editable:
            self.edit_button = QPushButton(_("Düzenle"), self)
            self.edit_button.setObjectName("chipMore")
            self.edit_button.setAccessibleName(_("Kategorileri düzenle"))
            self.edit_button.setCursor(Qt.PointingHandCursor)
            self.edit_button.setFixedHeight(30)
            self.edit_button.setMinimumWidth(self.edit_button.sizeHint().width())
            self.edit_button.clicked.connect(self.edit_requested)
        self._overflow = False
        # Chips never shrink below their text; what does not fit goes behind "more".
        self.setMinimumWidth(1)

    def buttons(self):
        """Visible pills by value (for tests and keyboard focus)."""
        return dict(self._buttons)

    def _chip(self, value, text, tip=""):
        button = QPushButton(text)
        button.setObjectName("chip")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(30)
        button.setMinimumWidth(button.sizeHint().width())
        if tip:
            button.setToolTip(tip)
        button.setAccessibleName(tip or text)
        button.clicked.connect(lambda checked=False, v=value: self.chosen.emit(v))
        self._group.addButton(button)
        self._buttons[value] = button
        self._layout.addWidget(button)
        return button

    def _label(self, label, count):
        return (
            f"{label}  {format_number(count, decimals=0, grouping=True)}"
            if self.show_counts
            else label
        )

    def set_items(self, items, current="", *, total=None):
        self._items = list(items)
        self._current = current
        self._total = total
        for button in self._group.buttons():
            self._group.removeButton(button)
            button.deleteLater()
        self._buttons = {}
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None and item.widget() not in (
                self.more_button,
                self.edit_button,
            ):
                item.widget().deleteLater()
        if total is None:
            total = sum(count for _, _, count in self._items)
        self._chip("", self._label(self.all_label, total))
        busiest = (
            sorted(self._items, key=lambda item: (-item[2], item[1].casefold()))
            if self.sort_by_count
            else self._items
        )
        shown = busiest[: self.limit]
        if current and all(value != current for value, _, _ in shown):
            shown += [item for item in self._items if item[0] == current]
        for value, label, count in shown:
            self._chip(
                value,
                self._label(label, count),
                f"{label} · {format_number(count, decimals=0, grouping=True)}",
            )
        self._overflow = len(self._items) > len(shown)
        if self.more_label:
            if self.more_button is None:
                self.more_button = QPushButton(self.more_label + "  ▾")
                self.more_button.setObjectName("chipMore")
                self.more_button.setCursor(Qt.PointingHandCursor)
                self.more_button.setFixedHeight(30)
                self.more_button.setAccessibleName(self.more_label)
                self.more_button.clicked.connect(self.open_popup)
                self.more_button.setMinimumWidth(self.more_button.sizeHint().width())
            self._layout.addWidget(self.more_button)
        self._layout.addStretch()
        if self.edit_button is not None:
            self._layout.addWidget(self.edit_button)
        button = self._buttons.get(current) or self._buttons[""]
        button.setChecked(True)
        self._fit()

    def _fit(self):
        """Show the chips that fit in order, the chosen one always; the rest wait in the list."""
        if not self.more_label or not self._buttons:
            return
        spacing = self._layout.spacing()
        chips = [button for value, button in self._buttons.items() if value]
        chosen = self._buttons.get(self._current) if self._current else None
        room = self.width() - self._buttons[""].minimumWidth()
        room -= self.more_button.minimumWidth() + spacing * 2
        if self.edit_button is not None and not self.edit_button.isHidden():
            room -= self.edit_button.minimumWidth() + spacing
        if chosen is not None:
            room -= chosen.minimumWidth() + spacing
        hidden = False
        for button in chips:
            if button is chosen:
                continue
            need = button.minimumWidth() + spacing
            fits = not hidden and need <= room
            button.setVisible(fits)
            room -= need if fits else 0
            hidden = hidden or not fits
        self.more_button.setVisible(self._overflow or hidden)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def set_current(self, value):
        """Mark ``value`` chosen, bringing it into the row if it was in the list only."""
        self.set_items(self._items, value, total=self._total)

    def open_popup(self):
        items = (
            sorted(self._items, key=lambda i: i[1].casefold())
            if self.sort_by_count
            else self._items
        )
        editable = self.edit_button is not None and not self.edit_button.isHidden()
        popup = ChoicePopup(items, self._current, self, editable=editable)
        popup.chosen.connect(self.chosen)
        popup.edit_requested.connect(self.edit_requested)
        anchor = self.more_button or self
        popup.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        popup.show()
        return popup

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 30)
