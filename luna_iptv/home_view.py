"""A personal landing page over the shared channel catalogue."""

from datetime import datetime
from heapq import nsmallest

from PySide6.QtCore import QEvent, QSize, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListView,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .dialogs import text_label
from .library import (
    CARD_GAP,
    CARD_HEIGHT,
    CARD_WIDTH,
    POSTER_HEIGHT,
    POSTER_WIDTH,
    ChannelDelegate,
    resumable,
    search_key,
)
from .logos import LogoViewportController
from .motion import IconButton


def greeting(hour):
    """Local-time greeting, with exclusive upper hour boundaries."""
    if 5 <= hour < 12:
        return "Günaydın"
    if 12 <= hour < 18:
        return "İyi günler"
    if 18 <= hour < 23:
        return "İyi akşamlar"
    return "İyi geceler"


class IdListModel(QSortFilterProxyModel):
    """An ordered selection that retains all roles from ChannelModel."""

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.order = {}
        self.hide_locked = False
        self.setSourceModel(source)
        self.sort(0)

    def set_ids(self, ids, *, hide_locked=False):
        order = {cid: rank for rank, cid in enumerate(ids)}
        if order != self.order or hide_locked != self.hide_locked:
            self.order, self.hide_locked = order, hide_locked
            self.invalidate()

    def filterAcceptsRow(self, row, parent):
        source = self.sourceModel()
        cid = source.channels[row].id
        return cid in self.order and not (self.hide_locked and cid in source.locked)

    def lessThan(self, left, right):
        channels = self.sourceModel().channels
        return self.order[channels[left.row()].id] < self.order[channels[right.row()].id]


class CardStrip(QListView):
    """One unwrapped row; the wheel moves horizontally and Tab leaves the row."""

    def __init__(self, poster_mode, parent=None):
        super().__init__(parent)
        self.poster_mode = poster_mode
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListView.SingleSelection)
        self.setSelectionRectVisible(False)
        self.setEditTriggers(QListView.NoEditTriggers)
        self.setTabKeyNavigation(False)
        self.setMouseTracking(True)
        self.setHorizontalScrollMode(QListView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        width, height = (POSTER_WIDTH, POSTER_HEIGHT) if poster_mode else (CARD_WIDTH, CARD_HEIGHT)
        self.setGridSize(QSize(width + CARD_GAP, height + CARD_GAP))
        self.setFixedHeight(height + CARD_GAP)
        self.horizontalScrollBar().setSingleStep(48)

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and event.key() in (Qt.Key_Left, Qt.Key_Right):
            event.accept()  # Navigate cards instead of invoking the player's seek shortcuts.
            return True
        return super().event(event)

    def wheelEvent(self, event):
        pixels, angle = event.pixelDelta(), event.angleDelta()
        delta = (pixels.x() or pixels.y()) if not pixels.isNull() else (angle.x() or angle.y())
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - delta)
        event.accept()


class HomeRow(QWidget):
    """A titled strip with shared artwork, activation and channel menus."""

    def __init__(self, window, title, *, posters=False, resume=False, parent=None):
        super().__init__(parent)
        self.window_ref, self.title, self.resume = window, title, resume
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.setContentsMargins(7, 0, 7, 0)
        label = text_label(title, "title")
        label.setWordWrap(True)
        heading.addWidget(label, 1)
        self.previous_button = QPushButton("‹")
        self.next_button = QPushButton("›")
        for button, name, step in (
            (self.previous_button, "Önceki kartlar", -1),
            (self.next_button, "Sonraki kartlar", 1),
        ):
            button.setObjectName("homeScroll")
            button.setFixedSize(30, 30)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)  # Tab goes straight to the next strip.
            button.setAccessibleName(f"{title}, {name}")
            button.setToolTip(name)
            button.clicked.connect(lambda checked=False, direction=step: self.scroll(direction))
            heading.addWidget(button)
        layout.addLayout(heading)
        self.view = CardStrip(posters)
        self.model = IdListModel(window.model, self)
        self.view.setModel(self.model)
        self.view.setItemDelegate(
            ChannelDelegate(
                self.view, logos=window.logos, posters=window.posters, now_for=window.programme_now
            )
        )
        self.artwork = LogoViewportController(self.view, window.logos, window.posters)
        self.view.clicked.connect(self.activate)
        self.view.activated.connect(self.activate)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)
        bar = self.view.horizontalScrollBar()
        bar.valueChanged.connect(self.update_buttons)
        bar.rangeChanged.connect(self.update_buttons)
        layout.addWidget(self.view)
        self.update_buttons()

    def set_ids(self, ids, *, hide_locked):
        self.model.set_ids(ids, hide_locked=hide_locked)
        count = self.model.rowCount()
        self.view.setAccessibleName(f"{self.title}, {count} öğe")
        self.setVisible(count > 0)

    def scroll(self, direction):
        bar = self.view.horizontalScrollBar()
        bar.setValue(bar.value() + direction * max(self.view.gridSize().width(), bar.pageStep()))

    def update_buttons(self, *_):
        bar = self.view.horizontalScrollBar()
        self.previous_button.setEnabled(bar.value() > bar.minimum())
        self.next_button.setEnabled(bar.value() < bar.maximum())

    def activate(self, index):
        channel = index.data(Qt.UserRole)
        if channel is not None:
            if self.resume:
                self.window_ref.request_play(channel)
            else:
                self.window_ref.open_channel(channel)

    def context_menu(self, position):
        channel = self.view.indexAt(position).data(Qt.UserRole)
        if channel is None:
            return
        menu = self.window_ref.build_channel_menu(channel)
        menu.exec(self.view.viewport().mapToGlobal(position))
        menu.deleteLater()


class HomeView(QWidget):
    """Profile greeting and up to twenty cards in each personal row."""

    def __init__(self, window, parent=None, *, clock=datetime.now):
        super().__init__(parent)
        self.window_ref, self.clock = window, clock
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QWidget#homeContent { background: transparent; }")
        content = QWidget()
        content.setObjectName("homeContent")
        body = QVBoxLayout(content)
        body.setContentsMargins(21, 26, 16, 28)
        body.setSpacing(24)
        header = QVBoxLayout()
        header.setContentsMargins(7, 0, 7, 0)
        header.setSpacing(6)
        self.heading = text_label("", "display")
        self.heading.setWordWrap(True)
        self.summary = text_label("", "muted")
        self.summary.setWordWrap(True)
        header.addWidget(self.heading)
        header.addWidget(self.summary)
        body.addLayout(header)
        self.rows = {}
        for key, title, posters, resume in (
            ("continue", "Kaldığın yerden devam et", True, True),
            ("favorite_live", "Favorilerinde şu an", False, False),
            ("recent_live", "Son izlenen kanallar", False, False),
            ("favorite_vod", "Favori filmler ve diziler", True, False),
        ):
            row = HomeRow(window, title, posters=posters, resume=resume)
            body.addWidget(row)
            self.rows[key] = row
            self.scroll.verticalScrollBar().valueChanged.connect(row.artwork._changed)
        strips = [row.view for row in self.rows.values()]
        for previous, following in zip(strips, strips[1:], strict=False):
            QWidget.setTabOrder(previous, following)
        self.empty = QFrame()
        self.empty.setObjectName("panel")
        empty = QVBoxLayout(self.empty)
        empty.setContentsMargins(28, 28, 28, 28)
        empty.setSpacing(16)
        title = text_label("Burası senin ana sayfan", "title")
        title.setWordWrap(True)
        empty.addWidget(title)
        note = text_label(
            "Yarım kalan filmlerin ve bölümlerin, favorilerin ve son izlediğin kanallar "
            "burada seni bekleyecek. Bir yayın seç, kendine yer aç.",
            "muted",
        )
        note.setWordWrap(True)
        empty.addWidget(note)
        actions = QHBoxLayout()
        self.live_button = IconButton("Canlı TV'ye git", "live", label=True, size=16)
        self.add_button = IconButton("Kaynak ekle", "plus", label=True, size=16)
        self.live_button.clicked.connect(lambda: window.set_section("live"))
        self.add_button.clicked.connect(lambda: window.add_source())
        for button in (self.live_button, self.add_button):
            button.setObjectName("primary" if button is self.add_button else "glass")
            button.setMinimumHeight(40)
            actions.addWidget(button)
        actions.addStretch()
        empty.addLayout(actions)
        body.addWidget(self.empty)
        body.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)

    def refresh(self):
        """One catalogue pass, bulk history, and bounded top-twenty selections."""
        window = self.window_ref
        model = window.model
        profile = window.store.profile(window.store.profile_id)
        self.heading.setText(f"{greeting(self.clock().hour)}, {profile['name']}")
        recent = window.store.recent_ids(max(len(model.channels), len(model.progress)))
        ranks = {cid: rank for rank, cid in enumerate(recent)}
        counts = dict.fromkeys(("live", "movie", "series"), 0)
        candidates = {key: [] for key in self.rows}
        for channel in model.channels:
            cid = channel.id
            if window.proxy.hide_locked and cid in model.locked:
                continue
            episode = channel.kind == "movie" and bool(channel.series_id)
            if channel.kind in counts and not episode and not window.proxy.category_hidden(channel):
                counts[channel.kind] += 1
            favorite = cid in model.favorites
            if (
                channel.kind == "movie"
                and cid in ranks
                and resumable(*model.progress.get(cid, (0, 0)))
            ):
                candidates["continue"].append(cid)
            if channel.kind == "live":
                if favorite:
                    candidates["favorite_live"].append(channel)
                elif cid in ranks:
                    candidates["recent_live"].append(cid)
            elif favorite and channel.kind in ("movie", "series") and not episode:
                candidates["favorite_vod"].append(channel)
        self.summary.setText(
            " · ".join(
                f"{counts[kind]:,} {label}".replace(",", ".")
                for kind, label in (("live", "canlı kanal"), ("movie", "film"), ("series", "dizi"))
            )
        )
        for key, row in self.rows.items():
            if key in ("continue", "recent_live"):
                ids = nsmallest(20, candidates[key], key=ranks.__getitem__)
            else:

                def order(channel, key=key):
                    rank = ranks.get(channel.id, len(ranks)) if key == "favorite_live" else 0
                    return rank, search_key(channel.name), channel.id

                ids = [channel.id for channel in nsmallest(20, candidates[key], key=order)]
            row.set_ids(ids, hide_locked=window.proxy.hide_locked)
        self.empty.setVisible(all(row.isHidden() for row in self.rows.values()))
        self.live_button.setVisible(bool(sum(counts.values())))

    def select_channel(self, channel_id):
        """Give startup's selected live channel a visible keyboard target."""
        for key in ("favorite_live", "recent_live"):
            row = self.rows[key]
            for number in range(row.model.rowCount()):
                index = row.model.index(number, 0)
                if index.data(Qt.UserRole).id == channel_id:
                    row.view.setCurrentIndex(index)
                    row.view.scrollTo(index)
                    row.view.setFocus()
                    return True
        return False

    def refresh_programmes(self):
        for key in ("favorite_live", "recent_live"):
            view = self.rows[key].view
            if view.isVisible() and not view.viewport().visibleRegion().isEmpty():
                view.viewport().update()

    def close_artwork(self):
        for row in self.rows.values():
            row.artwork.close()
