"""Run a function off the UI thread and hear back on it."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    error = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as e:      # reported to the UI thread, never lost
            traceback.print_exc()
            self.signals.error.emit(e)
        else:
            self.signals.done.emit(result)


_live: set[_Task] = set()


def run_async(fn, on_done=None, on_error=None) -> None:
    task = _Task(fn)
    task.setAutoDelete(False)
    _live.add(task)

    def finish(callback, value):
        _live.discard(task)
        if callback:
            callback(value)

    task.signals.done.connect(lambda v: finish(on_done, v))
    task.signals.error.connect(lambda e: finish(on_error, e))
    QThreadPool.globalInstance().start(task)
