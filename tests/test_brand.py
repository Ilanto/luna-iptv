"""The eclipse logo paints from its packaged layers and only moves when asked."""

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter

from luna_iptv import brand
from luna_iptv.motion import LogoMark


def render(seconds, tile=True, ground=Qt.transparent):
    image = QImage(128, 128, QImage.Format_ARGB32_Premultiplied)
    image.fill(ground)
    painter = QPainter(image)
    brand.paint(painter, QRectF(0, 0, 128, 128), seconds, tile=tile)
    painter.end()
    return image


def test_layers_and_icons_are_packaged():
    for name in ("backdrop.png", "corona.png", "sparkle.png", "geometry.json"):
        assert brand.asset(name).is_file(), name
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        assert brand.asset(f"icon-{size}.png").is_file(), size


def test_logo_paints_tile_disc_and_moves_over_time(qt_app):
    still = render(0.0)
    assert still.pixelColor(64, 64).alpha() == 255  # the dark disc
    assert still.pixelColor(1, 1).alpha() == 0  # rounded tile corner
    assert render(0.0) == still  # deterministic for icon exports
    assert render(brand.PERIOD / 4) != still
    assert render(brand.PERIOD) == still  # one period is a seamless loop
    # Without its tile the logo is drawn into a scene (the welcome sky): the
    # scene shows through, only the light and the disc are added.
    bare = render(0.0, tile=False, ground=Qt.darkBlue)
    assert bare.pixelColor(2, 64) == QColor(Qt.darkBlue)


def test_logo_mark_moves_only_while_hovered(qt_app):
    mark = LogoMark(46)
    mark.show()
    assert not mark.animating
    centre = mark.rect().center()
    mark.enterEvent(QEnterEvent(centre, centre, centre))
    assert mark.animating
    mark.leaveEvent(QEvent(QEvent.Leave))
    assert not mark.animating
    mark.close()


def test_logo_mark_stays_still_while_quiet(qt_app):
    mark = LogoMark(46)
    mark.show()
    centre = mark.rect().center()
    mark.enterEvent(QEnterEvent(centre, centre, centre))
    assert mark.animating
    mark.set_quiet(True)  # video started under the pointer
    assert not mark.animating
    mark.enterEvent(QEnterEvent(centre, centre, centre))
    assert not mark.animating
    mark.close()
