"""Collect Python garbage on the Qt main thread only.

Python's cyclic collector runs in whichever thread happens to allocate when a threshold is
crossed. If that is a worker (logo downloads, a network task) and the garbage holds Qt
objects, they are destroyed off the main thread and the process can crash. Automatic
collection is therefore switched off and the same thresholds are checked from a main-thread
timer instead (the approach pyqtgraph uses).
"""

import gc

from PySide6.QtCore import QObject, QTimer

INTERVAL_MS = 1000


class MainThreadCollector(QObject):
    def __init__(self, parent=None, interval_ms=INTERVAL_MS):
        super().__init__(parent)
        self.thresholds = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def check(self):
        """Collect the youngest generation whose count crossed its threshold."""
        counts = gc.get_count()
        for generation in (2, 1, 0):
            if counts[generation] > self.thresholds[generation]:
                gc.collect(generation)
                return generation
        return None

    def stop(self):
        self.timer.stop()
        gc.enable()
