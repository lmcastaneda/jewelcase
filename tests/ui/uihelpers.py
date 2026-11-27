import time

from PySide6.QtWidgets import QApplication

from jewelcase.core.session import open_session
from jewelcase.ui.controller import Controller
from jewelcase.ui.window import MainWindow


def make_window(tmp_path):
    session = open_session(demo_dir=tmp_path / "demo")
    controller = Controller(session, poll=False)
    controller.refresh_blocking()
    window = MainWindow(controller)
    window.resize(1180, 760)
    window.show()
    QApplication.processEvents()
    return window


def wait_until(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def wheel(widget, steps=-1):
    """Turn the mouse wheel over a widget, as when scrolling a list with the pointer resting on it."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    at = widget.rect().center()
    event = QWheelEvent(QPointF(at), QPointF(widget.mapToGlobal(at)), QPoint(0, 0), QPoint(0, 120 * steps),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()
