"""Virtual couch rows, painted in 1080p coordinates with shared artwork."""

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import icons, theme
from .i18n import N_, _
from .library import CARD_HEIGHT, CARD_WIDTH, tile_color
from .motion import animation_ms, motion_level, on_motion_changed

TABS = (
    N_("Canlı"),
    N_("Filmler"),
    N_("Diziler"),
    N_("Rehber"),
    N_("Favoriler"),
    N_("TV modundan çık"),
)
CARD_W, CARD_H = CARD_WIDTH * 2, CARD_HEIGHT * 2
ROW_H = CARD_H + 80


class TvBrowser(QWidget):
    """Paint only visible cards; keep the remote's focus independent of mouse focus."""

    def __init__(self, tv):
        super().__init__(tv)
        self.tv = tv
        self.lift = 1.0
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._lift)
        on_motion_changed(self._motion_changed)
        for cache in (tv.host.logos, tv.host.posters):
            cache.ready.connect(self.update)
        self.setFocusPolicy(Qt.NoFocus)

    def _lift(self, value):
        self.lift = value
        self.update()

    def _motion_changed(self):
        self.animation.stop()
        self.lift = 1.0
        self.update()

    def focus_changed(self):
        self.animation.stop()
        if motion_level() == "full" and self.tv.row >= 0 and not self.tv.playing:
            self.animation.setDuration(animation_ms(150))
            self.animation.setStartValue(0.0)
            self.animation.setEndValue(1.0)
            self.animation.start()
        else:
            self.lift = 1.0
        channel = self.tv.focused_channel()
        self.tv.setAccessibleName(channel.name if channel else _(TABS[self.tv.tab]))
        self.update()

    @staticmethod
    def text(painter, rect, text, size=28, color=None, *, wrap=False, bold=False, center=False):
        font = QFont(painter.font())
        font.setPixelSize(size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color or theme.TEXT))
        flags = (Qt.AlignHCenter if center else Qt.AlignLeft) | Qt.AlignVCenter
        if wrap:
            flags |= Qt.TextWordWrap
        else:
            text = painter.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width()))
        painter.drawText(rect, flags, text)

    def paintEvent(self, event):
        tv = self.tv
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(theme.NIGHT))
        painter.scale(tv.scale, tv.scale)
        width, height = self.width() / tv.scale, self.height() / tv.scale
        self._top(painter, width)
        area = QRectF(48, 150, width - 96, height - 410)
        painter.save()
        painter.setClipRect(area)
        guide = tv.tab == 3 and tv.series is None
        row_height = 150 if guide else ROW_H
        scroll = max(0, tv.row * row_height - (area.height() - row_height) / 2)
        artwork = {"live": [], "vod": []}
        first = max(0, int(scroll // row_height))
        last = min(len(tv.rows), int((scroll + area.height()) // row_height) + 1)
        for ri in range(first, last):
            title, channels = tv.rows[ri]
            y = area.y() + ri * row_height - scroll
            if guide:
                self._guide(painter, QRectF(58, y + 8, width - 116, 130), channels[0], ri)
                continue
            self.text(painter, QRectF(60, y, width - 120, 48), title, 30, bold=True)
            selected = tv.columns[ri]
            step = CARD_W + 32
            offset = max(0, selected * step + CARD_W / 2 - area.width() / 2)
            start = max(0, int(offset // step))
            end = min(len(channels), int((offset + area.width()) // step) + 1)
            for ci in range(start, end):
                channel = channels[ci]
                rect = QRectF(60 + ci * step - offset, y + 60, CARD_W, CARD_H)
                self._card(painter, rect, channel, ri == tv.row and ci == selected)
                if channel.id not in tv.host.model.locked and channel.logo:
                    artwork["live" if channel.kind == "live" else "vod"].append(channel.logo)
        painter.restore()
        if not tv.rows:
            self.text(painter, area, _("Burada henüz içerik yok."), 36)
        tv.host.logos.request_visible(artwork["live"], owner=self)
        tv.host.posters.request_visible(artwork["vod"], owner=self)
        painter.fillRect(QRectF(0, height - 245, width, 245), QColor(theme.DUSK))
        channel = tv.focused_channel()
        if channel:
            self.text(
                painter, QRectF(60, height - 230, width - 120, 56), channel.name, 36, bold=True
            )
            self.text(
                painter,
                QRectF(60, height - 168, width - 120, 102),
                tv.description(channel),
                26,
                theme.TEXT_SOFT,
                wrap=True,
            )
        self.text(
            painter,
            QRectF(60, height - 53, width - 120, 40),
            _("↑↓ Satır  ·  ←→ Seç  ·  Enter İzle  ·  Geri Dön  ·  Geri basılı: Çık"),
            24,
            theme.TEXT_SOFT,
        )
        painter.end()

    def _top(self, painter, width):
        tv = self.tv
        profile = tv.host.store.profile(tv.host.store.profile_id)
        avatar = QRectF(60, 28, 58, 58)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(profile["color"]))
        painter.drawEllipse(avatar)
        pixmap = icons.pixmap(profile.get("avatar") or "moon", theme.TEXT, 36)
        painter.drawPixmap(avatar.adjusted(11, 11, -11, -11), pixmap, QRectF(pixmap.rect()))
        self.text(painter, QRectF(138, 28, width - 370, 58), profile["name"], 28)
        self.text(painter, QRectF(width - 180, 28, 130, 58), datetime.now().strftime("%H:%M"), 32)
        cell = (width - 120) / len(TABS)
        for i, title in enumerate(TABS):
            rect = QRectF(60 + i * cell, 96, cell - 12, 48)
            if i == tv.tab:
                painter.setBrush(QColor(theme.ACCENT_TINT))
                painter.setPen(QPen(QColor(theme.ACCENT), 4 if tv.row == -1 else 1))
                painter.drawRoundedRect(rect, 12, 12)
            self.text(painter, rect.adjusted(12, 0, -12, 0), _(title), 26)

    def _outline(self, painter, rect, selected):
        painter.setBrush(QColor(theme.RAISED if selected else theme.SURFACE))
        painter.setPen(
            QPen(QColor(theme.ACCENT_STRONG if selected else theme.LINE), 6 if selected else 1)
        )
        painter.drawRoundedRect(rect, 22, 22)

    def _card(self, painter, rect, channel, selected):
        painter.save()
        if selected:
            growth = 1 + 0.025 * self.lift
            painter.translate(rect.center())
            painter.scale(growth, growth)
            painter.translate(-rect.center())
        self._outline(painter, rect, selected)
        locked = channel.id in self.tv.host.model.locked
        cache = self.tv.host.logos if channel.kind == "live" else self.tv.host.posters
        image = None if locked else cache.prepared_logo(channel.logo)
        art = rect.adjusted(24, 20, -24, -100)
        if image is not None:
            size = image.size().scaled(art.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(0, 0, size.width(), size.height())
            target.moveCenter(art.center())
            painter.drawPixmap(target, image, QRectF(image.rect()))
        elif locked:
            pixmap = icons.pixmap("lock", theme.GOLD, 80)
            target = QRectF(art.center().x() - 40, art.center().y() - 40, 80, 80)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        else:
            # Like the main grid: the channel's own night hue with its name large on it.
            stage = rect.adjusted(6, 6, -6, -96)
            base = QColor(tile_color(channel.name))
            glow = QLinearGradient(stage.topLeft(), stage.bottomRight())
            glow.setColorAt(0.0, base.lighter(150))
            glow.setColorAt(1.0, base)
            shape = QPainterPath()
            shape.addRoundedRect(stage, 18, 18)
            painter.fillPath(shape, glow)
            self.text(
                painter, stage.adjusted(20, 0, -20, 0), channel.name, 40, bold=True, center=True
            )
        self.text(
            painter,
            QRectF(rect.x() + 24, rect.bottom() - 90, rect.width() - 48, 40),
            channel.name,
            30,
            bold=True,
        )
        programme = None if locked else self.tv.host.programme_now(channel)
        subtitle = (
            _("Kilitli içerik") if locked else programme.title if programme else channel.group
        )
        self.text(
            painter,
            QRectF(rect.x() + 24, rect.bottom() - 48, rect.width() - 48, 34),
            subtitle,
            24,
            theme.TEXT_SOFT,
        )
        painter.restore()

    def _guide(self, painter, rect, channel, row):
        self._outline(painter, rect, row == self.tv.row)
        self.text(
            painter, rect.adjusted(24, 12, -rect.width() * 0.7, -12), channel.name, 28, bold=True
        )
        self.text(
            painter,
            rect.adjusted(rect.width() * 0.32, 12, -24, -12),
            self.tv.description(channel),
            26,
            wrap=True,
        )
