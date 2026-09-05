"""Run bounded network work off the GUI thread; callbacks arrive on the GUI thread."""

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @Slot()
    def run(self):
        try:
            result = self.function()
        except Exception as exc:
            from .network import NetworkError

            self.signals.failed.emit(
                str(exc)
                if isinstance(exc, NetworkError)
                else "Kaynak işlenemedi. Dosya biçimini ve erişim bilgilerini kontrol edin."
            )
        else:
            self.signals.done.emit(result)
