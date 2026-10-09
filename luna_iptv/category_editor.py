"""Per-source category visibility and ordering, staged until Save."""

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .dialogs import text_label
from .i18n import N_, _, format_number
from .library import search_key

SECTION_LABELS = {"live": N_("Canlı TV"), "movie": N_("Filmler"), "series": N_("Diziler")}


class CategoryEditor(QDialog):
    """Checkboxes control visibility; list order becomes the category order."""

    def __init__(self, store, source_id, kind, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Kategori düzeni"))
        self.resize(540, 600)
        self._store, self.kind = store, kind
        self._source = None
        self._drafts = {}
        self._dirty = False
        self._reset = False
        self._filling = False
        self._prefs = store.category_prefs()
        profile = store.profile(store.profile_id)
        locked_groups = store.locked_groups() if profile["kids"] else set()
        locked_channels = store.locked_channels() if profile["kids"] and store.pin_hash() else set()
        self._counts = {}
        for channel in store.channels():
            source = channel.id.split(":", 1)[0]
            if (
                channel.kind != kind
                or (kind == "movie" and channel.series_id)
                or (source, channel.group) in locked_groups
                or channel.id in locked_channels
            ):
                continue
            self._counts.setdefault(source, Counter())[channel.group] += 1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(text_label(_("Kategori düzeni"), "heading"))
        self.subtitle = text_label("", "muted")
        layout.addWidget(self.subtitle)
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName(_("Düzenlenecek kaynak"))
        for source in store.sources():
            self.source_combo.addItem(source["name"], source["id"])
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(source_id)))
        self.source_combo.setVisible(not source_id)
        layout.addWidget(self.source_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Kategori ara…"))
        self.search.setAccessibleName(_("Kategori ara"))
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        note = text_label(
            _("Görmek istediklerini işaretle. Sırayı sürükleyerek değiştirebilirsin."), "muted"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        self.list.setObjectName("categoryList")
        self.list.setAccessibleName(_("Kategori sırası ve görünürlüğü"))
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.list, 1)
        actions = QHBoxLayout()
        for name, label, callback in (
            ("up_button", _("Yukarı"), lambda: self.move(-1)),
            ("down_button", _("Aşağı"), lambda: self.move(1)),
            ("show_all_button", _("Hepsini göster"), self.show_all),
            ("reset_button", _("Sıfırla"), self.reset),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            setattr(self, name, button)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(_("Kaydet"), QDialogButtonBox.AcceptRole)
        self.save_button.setObjectName("primary")
        self.save_button.clicked.disconnect()
        self.save_button.clicked.connect(self.save)
        buttons.addButton(_("Vazgeç"), QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source_combo.currentIndexChanged.connect(self._load_source)
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(self._changed)
        self.list.model().rowsMoved.connect(self._changed)
        self.list.currentRowChanged.connect(self._update_buttons)
        self._load_source()

    def rows(self):
        return [
            (self.list.item(row).data(Qt.UserRole), self.list.item(row).checkState() != Qt.Checked)
            for row in range(self.list.count())
        ]

    def _stash(self):
        if self._dirty and self._source is not None:
            self._drafts[self._source] = None if self._reset else self.rows()

    def _load_source(self, *_args):
        self._stash()
        self._source = self.source_combo.currentData()
        self.subtitle.setText(f"{self.source_combo.currentText()} · {_(SECTION_LABELS[self.kind])}")
        self._dirty = False
        self._reset = self._source in self._drafts and self._drafts[self._source] is None
        groups = self._counts.get(self._source, {})
        if self._source in self._drafts:
            rows = self._drafts[self._source]
            rows = [(group, False) for group in groups] if rows is None else rows
        else:
            prefs = {g: self._prefs.get((self._source, self.kind, g), {}) for g in groups}
            ordered = sorted(
                groups,
                key=lambda g: (
                    prefs[g].get("position") is None,
                    prefs[g].get("position") or 0,
                ),
            )
            rows = [(g, prefs[g].get("hidden", False)) for g in ordered]
        self._fill(rows)
        self.save_button.setEnabled(self._source is not None)

    def _fill(self, rows):
        self._filling = True
        self.list.clear()
        for group, hidden in rows:
            count = self._counts[self._source][group]
            item = QListWidgetItem(
                f"{group or _('Kategorisiz')}   {format_number(count, decimals=0, grouping=True)}"
            )
            item.setData(Qt.UserRole, group)
            item.setFlags(
                (item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                & ~Qt.ItemIsDropEnabled
            )
            item.setCheckState(Qt.Unchecked if hidden else Qt.Checked)
            self.list.addItem(item)
        self._filling = False
        self._filter(self.search.text())

    def _changed(self, *_):
        if not self._filling:
            self._dirty, self._reset = True, False
            self._update_buttons()

    def _filter(self, text):
        key = search_key(text)
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(key not in search_key(item.data(Qt.UserRole) or _("Kategorisiz")))
        self._update_buttons()

    def _neighbor(self, step):
        row = self.list.currentRow()
        if row < 0 or self.list.item(row).isHidden():
            return None
        target = row + step
        while 0 <= target < self.list.count():
            if not self.list.item(target).isHidden():
                return target
            target += step
        return None

    def _update_buttons(self, *_):
        self.up_button.setEnabled(self._neighbor(-1) is not None)
        self.down_button.setEnabled(self._neighbor(1) is not None)

    def move(self, step):
        target = self._neighbor(step)
        if target is None:
            return
        item = self.list.takeItem(self.list.currentRow())
        self.list.insertItem(target, item)
        self.list.setCurrentItem(item)
        self._changed()

    def show_all(self):
        for row in range(self.list.count()):
            self.list.item(row).setCheckState(Qt.Checked)

    def reset(self):
        self._fill([(group, False) for group in self._counts.get(self._source, {})])
        self._dirty, self._reset = True, True

    def save(self):
        self._stash()
        if self._source is not None and self._source not in self._drafts:
            self._drafts[self._source] = self.rows()
        for source, rows in self._drafts.items():
            if rows is None:
                self._store.reset_category_prefs(source, self.kind)
            else:
                # Keep preferences for absent or parental-hidden categories out of the editor.
                excluded = [
                    (group, pref["hidden"])
                    for (sid, kind, group), pref in self._prefs.items()
                    if sid == source
                    and kind == self.kind
                    and group not in self._counts.get(source, {})
                ]
                self._store.save_category_prefs(source, self.kind, rows + excluded)
        self.accept()
