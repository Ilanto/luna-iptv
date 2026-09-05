import unicodedata

from PySide6.QtCore import QAbstractListModel, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate


def search_key(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
        if not unicodedata.combining(c)
    )


class ChannelModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = []
        self.favorites = set()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.channels)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.channels):
            return None
        channel = self.channels[index.row()]
        if role == Qt.DisplayRole:
            return channel.name
        if role == Qt.UserRole:
            return channel
        if role == Qt.UserRole + 1:
            return channel.id in self.favorites
        if role == Qt.AccessibleTextRole:
            return channel.name + ", " + channel.group

    def reset(self, channels, favorites):
        self.beginResetModel()
        self.channels = channels
        self.favorites = favorites
        self.endResetModel()


class ChannelFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.section = "live"
        self.query = ""
        self.group = ""
        self.source = ""
        self.episode_ids = None
        self.recent = set()

    def filterAcceptsRow(self, row, parent):
        channel = self.sourceModel().channels[row]
        if self.source and not channel.id.startswith(self.source + ":"):
            return False
        if self.episode_ids is not None:
            if channel.id not in self.episode_ids:
                return False
        elif self.section == "favorites":
            if channel.id not in self.sourceModel().favorites:
                return False
        elif self.section == "recent":
            if channel.id not in self.recent:
                return False
        elif channel.kind != self.section or (channel.series_id and channel.kind == "movie"):
            return False
        if self.group and channel.group != self.group:
            return False
        return not self.query or search_key(self.query) in search_key(
            channel.name + " " + channel.group
        )

    def refresh(self):
        if hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateFilter()


class ChannelDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(240, 69)

    def paint(self, painter, option, index):
        channel = index.data(Qt.UserRole)
        if channel is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(4, 3, -4, -3)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3b2d34" if selected else "#292c2d" if hovered else "#1d2021"))
        painter.drawRoundedRect(rect, 6, 6)
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor("#e889a8"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)
        icon = rect.adjusted(10, 11, 0, -11)
        icon.setWidth(38)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#49353f" if selected else "#303334"))
        painter.drawRoundedRect(icon, 6, 6)
        font = QFont(option.font)
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e889a8" if selected else "#c2afb8"))
        painter.drawText(icon, Qt.AlignCenter, channel.name[:2].upper())
        title = rect.adjusted(60, 8, -25, -29)
        font.setPointSize(10)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(QColor("#f8e7ec"))
        painter.drawText(
            title,
            Qt.AlignVCenter,
            painter.fontMetrics().elidedText(channel.name, Qt.ElideRight, title.width()),
        )
        subtitle = rect.adjusted(60, 32, -20, -7)
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#b2aaad"))
        painter.drawText(
            subtitle,
            Qt.AlignVCenter,
            painter.fontMetrics().elidedText(
                channel.group
                or {"live": "Canlı yayın", "movie": "Film / video", "series": "Dizi"}.get(
                    channel.kind, ""
                ),
                Qt.ElideRight,
                subtitle.width(),
            ),
        )
        if index.data(Qt.UserRole + 1):
            painter.setPen(QColor("#e889a8"))
            painter.drawText(rect.adjusted(0, 0, -9, 0), Qt.AlignRight | Qt.AlignVCenter, "★")
        painter.restore()
