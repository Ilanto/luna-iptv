"""Bounded card animations and catalogue placeholders, without a real display."""

import pytest
from PySide6.QtCore import QModelIndex, QObject, QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from luna_iptv import motion
from luna_iptv.home_view import CardStrip
from luna_iptv.library import CardGrid, ChannelDelegate, ChannelModel
from luna_iptv.models import Channel, Playlist
from luna_iptv.storage import Store
from luna_iptv.window import MainWindow


class Artwork(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.images = {}

    def prepared_logo(self, url):
        return self.images.get(url)


@pytest.fixture
def cards(qt_app):
    motion.set_motion_level("full")
    views = []

    def create(count=4, strip=False):
        view = CardStrip(False) if strip else CardGrid()
        model = ChannelModel(view)
        model.reset(
            [Channel(str(i), f"Kanal {i}", "", logo=f"art:{i}") for i in range(count)], set()
        )
        view.setModel(model)
        cache = Artwork()
        delegate = ChannelDelegate(view, logos=cache)
        view.setItemDelegate(delegate)
        view.resize(780, 380)
        view.show()
        qt_app.processEvents()
        views.append((view, cache))
        return view, model, delegate, cache

    yield create
    for view, _ in views:
        view.close()
        view.deleteLater()
    motion.set_motion_level("full")


@pytest.mark.parametrize("strip", [False, True])
def test_hover_eases_only_entering_and_leaving_rects(cards, monkeypatch, strip):
    view, model, _, _ = cards(strip=strip)
    controller = view.card_motion
    now = [10.0]
    monkeypatch.setattr(controller, "clock", lambda: now[0])
    updates = []
    monkeypatch.setattr(view.viewport(), "update", lambda rect: updates.append(QRect(rect)))
    first, second = model.index(0, 0), model.index(1, 0)
    controller.set_hover(first)
    now[0] += 0.07
    controller.tick()
    assert 0 < controller.progress(first) < 1
    assert updates and all(rect == view.visualRect(first) for rect in updates)
    now[0] += 0.08
    controller.tick()
    assert controller.progress(first) == 1
    assert not controller.timer.isActive()
    controller.set_hover(second)
    now[0] += 0.07
    controller.tick()
    assert 0 < controller.progress(first) < 1
    assert 0 < controller.progress(second) < 1
    controller.set_hover(QModelIndex())
    now[0] += 0.15
    controller.tick()
    assert controller.progress(first) == controller.progress(second) == 0
    assert not controller.timer.isActive()
    assert all(rect in (view.visualRect(first), view.visualRect(second)) for rect in updates)


@pytest.mark.parametrize("level", ["reduced", "off"])
def test_motion_policy_clears_hover_and_fades(cards, level):
    view, model, delegate, cache = cards()
    view.card_motion.set_hover(model.index(0, 0))
    cache.images["art:0"] = QPixmap(30, 30)
    cache.ready.emit("art:0")
    motion.set_motion_level(level)
    assert view.card_motion.progress(model.index(0, 0)) == 0
    assert not view.card_motion.timer.isActive()
    assert not delegate._fading
    assert not delegate._pending_artwork


def paint_card(view, model, delegate, row=0, state=QStyle.State_None):
    canvas = QImage(250, 180, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 250, 180)
    option.state = state
    painter = QPainter(canvas)
    try:
        delegate.paint(painter, option, model.index(row, 0))
    finally:
        painter.end()
    return canvas


def test_only_artwork_arriving_after_placeholder_fades(cards, monkeypatch):
    view, model, delegate, cache = cards()
    now = [20.0]
    monkeypatch.setattr(view.card_motion, "clock", lambda: now[0])
    before = paint_card(view, model, delegate)
    pixmap = QPixmap(30, 30)
    pixmap.fill(Qt.red)
    cache.images["art:0"] = pixmap
    cache.ready.emit("art:0")
    index = model.index(0, 0)
    assert delegate.artwork_opacity(index, "art:0") == 0
    assert paint_card(view, model, delegate) == before
    now[0] += 0.11
    assert 0 < delegate.artwork_opacity(index, "art:0") < 1
    middle = paint_card(view, model, delegate)
    now[0] += 0.12
    view.card_motion.tick()
    assert delegate.artwork_opacity(index, "art:0") == 1
    assert middle != paint_card(view, model, delegate)
    assert not view.card_motion.timer.isActive()
    view.card_motion.clear()
    cache.images["art:1"] = pixmap
    paint_card(view, model, delegate, 1)
    assert delegate.artwork_opacity(model.index(1, 0), "art:1") == 1
    assert not delegate._fading


def test_artwork_tracking_bounded_and_no_pixmaps_allocated_in_paint(cards, monkeypatch):
    view, model, delegate, cache = cards(300)
    pixmap = QPixmap(30, 30)
    pixmap.fill(Qt.red)
    # Reuse a visible cell to simulate successive arrivals without waiting for scrolling.
    monkeypatch.setattr(view, "visualRect", lambda index: QRect(0, 0, 250, 180))
    for row in range(300):
        paint_card(view, model, delegate, row)
        cache.images[f"art:{row}"] = pixmap
        cache.ready.emit(f"art:{row}")
    assert 0 < len(delegate._arrived_at) <= 256
    assert "art:0" not in delegate._arrived_at
    assert "art:299" in delegate._arrived_at
    assert len(delegate._pending_artwork) <= 256
    assert len(delegate._fading) <= 256
    view.card_motion.clear()

    def no_pixmap(*args, **kwargs):
        pytest.fail("paint allocated a pixmap")

    monkeypatch.setattr(QPixmap, "__init__", no_pixmap)
    for row in range(200):
        paint_card(view, model, delegate, row)


@pytest.mark.parametrize("poster", [False, True])
def test_loading_skeleton_lifecycle(cards, qt_app, poster):
    view, model, _, _ = cards(0)
    view.set_poster_mode(poster)
    view.set_loading(True)
    qt_app.processEvents()
    assert view.loading
    assert view._skeleton_timer.isActive()
    image = view.viewport().grab().toImage()
    assert image.pixelColor(20, 20) != image.pixelColor(0, 0)
    view.hide()
    assert not view._skeleton_timer.isActive()
    view.show()
    assert view._skeleton_timer.isActive()
    motion.set_motion_level("reduced")
    assert not view._skeleton_timer.isActive()
    motion.set_motion_level("full")
    assert view._skeleton_timer.isActive()
    model.reset([Channel("one", "Bir", "")], set())
    assert not view._skeleton_timer.isActive()
    model.reset([], set())
    assert view._skeleton_timer.isActive()
    view.set_loading(False)
    assert not view._skeleton_timer.isActive()


@pytest.mark.parametrize("result", ["done", "failed", "empty", "save_error"])
def test_import_loading_clears_on_every_completion(qt_app, tmp_path, monkeypatch, result):
    from PySide6.QtCore import QThreadPool

    tasks = []
    monkeypatch.setattr(QThreadPool, "start", lambda self, task: tasks.append(task))
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    try:
        window.set_section("live")
        window.import_source({"name": "Test", "type": "m3u", "location": "https://example.test"})
        assert window.channel_list.loading
        assert not window.channel_list.isHidden()
        assert window.no_results.isHidden()
        task = tasks[-1]
        if result == "failed":
            task.signals.failed.emit("Kaynak okunamadı")
        else:
            if result == "save_error":

                def fail(*args):
                    raise OSError("test")

                monkeypatch.setattr(window, "accept_import", fail)
            channels = [] if result == "empty" else [Channel("one", "Bir", "")]
            task.signals.done.emit(Playlist(channels, [], []))
        assert not window.channel_list.loading
        assert not window.channel_list._skeleton_timer.isActive()
        assert window.no_results.isHidden() == (result == "done")
    finally:
        window.close()


def test_cache_ready_updates_only_its_visible_cards(cards, monkeypatch):
    from luna_iptv.logos import LogoViewportController

    view, model, _, cache = cards()
    monkeypatch.setattr(cache, "request_visible", lambda *args, **kwargs: None, raising=False)
    controller = LogoViewportController(view, cache)
    try:
        controller._schedule()
        updates = []
        monkeypatch.setattr(view.viewport(), "update", lambda *args: updates.append(args))
        cache.ready.emit("art:0")
        assert updates == [(view.visualRect(model.index(0, 0)),)]
    finally:
        controller.close()


def test_fast_hover_moves_remain_bounded_and_reset_clears_state(cards, monkeypatch):
    view, model, delegate, cache = cards(50000)
    now = [10.0]
    monkeypatch.setattr(view.card_motion, "clock", lambda: now[0])
    for row in range(12):
        view.card_motion.set_hover(model.index(row, 0))
        now[0] += 0.01
        view.card_motion.tick()
        assert len(view.card_motion._states) <= 2
    model.reset([], set())
    assert not view.card_motion._states
    assert not delegate._fading
    assert not delegate._pending_artwork
    assert not view.card_motion.timer.isActive()


@pytest.mark.parametrize("poster", [False, True])
def test_selected_card_lifts_only_with_full_motion(cards, poster):
    view, model, delegate, _ = cards()
    view.set_poster_mode(poster)
    normal = paint_card(view, model, delegate)
    lifted = paint_card(view, model, delegate, state=QStyle.State_Selected)
    assert normal.pixelColor(4, 50).alpha() == 0
    assert lifted.pixelColor(4, 50).alpha() > 0
    motion.set_motion_level("reduced")
    still = paint_card(view, model, delegate, state=QStyle.State_Selected)
    assert still.pixelColor(4, 50).alpha() == 0


def test_poster_fade_repaints_only_its_cell_and_scroll_does_not_replay(cards, monkeypatch):
    view, model, delegate, cache = cards()
    view.set_poster_mode(True)
    before = paint_card(view, model, delegate)
    now = [10.0]
    monkeypatch.setattr(view.card_motion, "clock", lambda: now[0])
    updates = []
    monkeypatch.setattr(view.viewport(), "update", lambda rect: updates.append(QRect(rect)))
    image = QPixmap(30, 45)
    image.fill(Qt.red)
    cache.images["art:0"] = image
    cache.ready.emit("art:0")
    assert paint_card(view, model, delegate) == before
    now[0] += 0.1
    view.card_motion.tick()
    assert updates and all(rect == view.visualRect(model.index(0, 0)) for rect in updates)
    assert paint_card(view, model, delegate) != before
    monkeypatch.undo()
    view.hide()
    view.show()
    assert delegate.artwork_opacity(model.index(0, 0), "art:0") == 1
    assert not view.card_motion.timer.isActive()


def test_mouse_events_drive_hover_and_hide_stops_timer(cards, qt_app):
    from PySide6.QtTest import QTest

    view, model, _, _ = cards()
    first = model.index(0, 0)
    QTest.mouseMove(view.viewport(), view.visualRect(first).center())
    assert view.card_motion.timer.isActive()
    view.hide()
    assert not view.card_motion.timer.isActive()
    assert view.card_motion.progress(first) == 0


def test_close_during_import_clears_loading_before_late_completion(qt_app, tmp_path, monkeypatch):
    from PySide6.QtCore import QThreadPool

    tasks = []
    monkeypatch.setattr(QThreadPool, "start", lambda self, task: tasks.append(task))
    window = MainWindow(Store(tmp_path / "library.sqlite3"))
    window.import_source({"name": "Test", "type": "m3u", "location": "https://example.test"})
    assert window.channel_list.loading
    window.close()
    assert not window.channel_list.loading
    tasks[-1].signals.failed.emit("Kaynak okunamadı")
    assert not window.channel_list._skeleton_timer.isActive()
    assert not window._tasks


def test_other_cache_ready_does_not_consume_pending_poster(cards):
    view, model, _, logos = cards()
    posters = Artwork()
    delegate = ChannelDelegate(view, logos=logos, posters=posters)
    view.setItemDelegate(delegate)
    view.set_poster_mode(True)
    model.reset([Channel("film", "Film", "", kind="movie", logo="shared")], set())
    paint_card(view, model, delegate)
    image = QPixmap(30, 45)
    image.fill(Qt.red)
    logos.images["shared"] = image
    logos.ready.emit("shared")
    posters.images["shared"] = image
    posters.ready.emit("shared")
    assert delegate.artwork_opacity(model.index(0, 0), "shared") < 1
