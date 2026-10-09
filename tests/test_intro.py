"""The opening eclipse."""

from PySide6.QtWidgets import QWidget

from luna_iptv import motion
from luna_iptv.intro import IntroOverlay


def test_intro_covers_then_disappears_and_respects_motion(qt_app):
    host = QWidget()
    host.resize(800, 500)
    host.show()
    overlay = IntroOverlay.play(host)
    assert overlay is not None and overlay.geometry() == host.rect()
    for value in (0.0, 0.4, 0.8):
        overlay._step(value)
        image = overlay.grab().toImage()
        assert not image.isNull()
    host.resize(900, 600)
    qt_app.processEvents()
    assert overlay.size() == host.size()
    overlay.animation.setCurrentTime(overlay.animation.duration())
    qt_app.processEvents()
    assert not overlay.isVisible()
    motion.set_motion_level("reduced")
    try:
        assert IntroOverlay.play(host) is None
    finally:
        motion.set_motion_level("full")
    host.close()
