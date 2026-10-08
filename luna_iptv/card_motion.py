"""One idle-stopping clock per card view; no catalogue traversal on animation ticks."""

from time import monotonic

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QTimer
from PySide6.QtWidgets import QListView

from .motion import animation_ms, motion_level, on_motion_changed


class CardMotionController(QObject):
    """Animate at most the entering/leaving cards and the delegate's visible fades."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.clock = monotonic
        self._hover = QPersistentModelIndex()
        self._states = {}
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.clear)
        view.horizontalScrollBar().valueChanged.connect(self.clear)
        on_motion_changed(self.clear)

    def repaint(self, index):
        if index.isValid():
            rect = self.view.visualRect(index)
            if rect.intersects(self.view.viewport().rect()):
                self.view.viewport().update(rect)

    def progress(self, index):
        if not self._states:
            return 0.0
        state = self._states.get(QPersistentModelIndex(index))
        if state is None or motion_level() != "full":
            return 0.0
        start, target, at = state
        elapsed = min(1.0, max(0.0, (self.clock() - at) * 1000 / animation_ms(140, 0)))
        return start + (target - start) * (1 - (1 - elapsed) ** 3)

    def set_hover(self, index):
        if motion_level() != "full":
            return
        index = QPersistentModelIndex(index)
        if index == self._hover:
            return
        previous = self._hover
        now = self.clock()
        values = {key: self.progress(key) for key in (previous, index) if key.isValid()}
        # Rapid pointer moves discard the older leaving card, repainting its resting state.
        old = self._states
        self._states = {}
        self._hover = index
        if previous.isValid():
            self._states[previous] = (values[previous], 0.0, now)
        if index.isValid():
            self._states[index] = (values[index], 1.0, now)
        for key in old:
            self.repaint(key)
        self.start()

    def start(self):
        if motion_level() == "full" and self.view.isVisible():
            self.timer.start()

    def tick(self):
        if motion_level() != "full" or not self.view.isVisible():
            self.clear()
            return
        active = False
        now = self.clock()
        for index, (_, target, at) in tuple(self._states.items()):
            self.repaint(index)
            if not index.isValid() or (target == 0 and now - at >= animation_ms(140, 0) / 1000):
                del self._states[index]
            elif now - at < animation_ms(140, 0) / 1000:
                active = True
        delegate = self.view.itemDelegate()
        if hasattr(delegate, "advance_fades"):
            active = delegate.advance_fades() or active
        if not active:
            self.timer.stop()

    def clear(self, *_):
        self.timer.stop()
        old, self._states = self._states, {}
        self._hover = QPersistentModelIndex()
        for index in old:
            self.repaint(index)
        self.repaint(self.view.currentIndex())
        delegate = self.view.itemDelegate()
        if hasattr(delegate, "clear_fades"):
            delegate.clear_fades()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseMove:
            self.set_hover(self.view.indexAt(event.position().toPoint()))
        elif event.type() == QEvent.Leave:
            self.set_hover(QModelIndex())
        elif event.type() in (QEvent.Hide, QEvent.Resize):
            self.clear()
        return False


class CardView(QListView):
    """Shared motion lifecycle for the wrapping grid and home strips."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.card_motion = CardMotionController(self)

    def setModel(self, model):
        previous = self.model()
        if previous is not None:
            for signal in (previous.modelReset, previous.layoutChanged, previous.rowsRemoved):
                signal.disconnect(self.card_motion.clear)
        self.card_motion.clear()
        super().setModel(model)
        if model is not None:
            for signal in (model.modelReset, model.layoutChanged, model.rowsRemoved):
                signal.connect(self.card_motion.clear)
