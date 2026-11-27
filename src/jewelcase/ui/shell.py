"""How the app sits on the desktop: notifications, launcher progress, one instance, remembered state.

Everything here is best-effort. Without a session bus, or on a desktop that offers none of this,
each call quietly does nothing.
"""
from __future__ import annotations

import configparser
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core import integrate

DESKTOP_ID = f"{integrate.desktop_name()}.desktop"


def _run(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify(title: str, body: str = "") -> bool:
    """A desktop notification. Titles are sentence case with no full stop, as the HIG asks."""
    send = shutil.which("notify-send")
    if send and _run([send, "--app-name=Jewelcase", f"--icon={integrate.desktop_name()}", title, body]):
        return True
    gdbus = shutil.which("gdbus")
    if gdbus:
        return _run([gdbus, "call", "--session", "--dest", "org.freedesktop.Notifications",
                     "--object-path", "/org/freedesktop/Notifications",
                     "--method", "org.freedesktop.Notifications.Notify",
                     "Jewelcase", "0", integrate.desktop_name(), title, body, "[]", "{}", "-1"])
    return False


def launcher_progress(fraction: float | None) -> None:
    """Progress on the dock icon, where the dock understands the launcher-entry signal.

    Sent from the app's own bus connection: docks drop an entry when its sender leaves the bus.
    """
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        props = {"progress-visible": fraction is not None}
        if fraction is not None:
            props["progress"] = float(max(0.0, min(1.0, fraction)))
        message = QDBusMessage.createSignal("/com/canonical/unity/launcherentry/jewelcase",
                                            "com.canonical.Unity.LauncherEntry", "Update")
        message.setArguments([f"application://{DESKTOP_ID}", props])
        bus.send(message)
    except Exception:       # best effort: a desktop without this must never break a sync
        return


class SingleInstance:
    """Starting Jewelcase again while it is running brings its window back instead of opening a second one."""

    def __init__(self, name: str):
        self.name = name
        self._server: QLocalServer | None = None

    def already_running(self) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if not sock.waitForConnected(300):
            return False
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True

    def listen(self, on_activate) -> bool:
        QLocalServer.removeServer(self.name)        # a socket left by a crash must not block us
        server = QLocalServer()
        if not server.listen(self.name):
            return False

        def incoming() -> None:
            conn = server.nextPendingConnection()
            if conn is not None:
                conn.readyRead.connect(lambda: conn.readAll())
                conn.disconnected.connect(conn.deleteLater)
            on_activate()
        server.newConnection.connect(incoming)
        self._server = server
        return True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None


class WindowState:
    """Size, view, sort order, column widths and collapsed sections, restored at the next launch."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, window) -> None:
        from . import models
        head = window.table.horizontalHeader()
        data = {
            "size": f"{window.width()}x{window.height()}",
            "splitter": ",".join(str(n) for n in window.split.sizes()),
            "scope": window._scope,
            "view": window.games_view,
            "sort_column": str(head.sortIndicatorSection()),
            "sort_order": "1" if head.sortIndicatorOrder() == Qt.DescendingOrder else "0",
            "collapsed": ", ".join(sorted(window._collapsed)),
            "columns": ",".join(str(window.table.columnWidth(c)) for c in range(len(models.HEADERS))),
        }
        ini = configparser.ConfigParser(interpolation=None)
        ini["window"] = data
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                ini.write(f)
        except OSError:
            pass

    def restore(self, window) -> None:
        """Apply what still makes sense; anything that no longer fits is ignored."""
        from . import models
        ini = configparser.ConfigParser(interpolation=None)
        try:
            if not ini.read(self.path, encoding="utf-8") or "window" not in ini:
                return
        except (configparser.Error, UnicodeError, OSError):
            return
        w = ini["window"]

        def ints(text: str, sep: str) -> list[int]:
            try:
                return [int(x) for x in text.split(sep) if x.strip()]
            except ValueError:
                return []

        size = ints(w.get("size", ""), "x")
        if len(size) == 2 and 800 <= size[0] <= 10000 and 500 <= size[1] <= 10000:
            window.resize(size[0], size[1])
        parts = ints(w.get("splitter", ""), ",")
        if len(parts) == 2 and min(parts) > 0:
            window.split.setSizes(parts)
        loaded = window.controller.loaded
        names = [n.strip() for n in w.get("collapsed", "").split(",") if n.strip()]
        if loaded:
            sections = {window.sidebar.topLevelItem(i).text(0) for i in range(window.sidebar.topLevelItemCount())}
            window._collapsed = {n for n in names if n in sections}
            for i in range(window.sidebar.topLevelItemCount()):
                top = window.sidebar.topLevelItem(i)
                top.setExpanded(top.text(0) not in window._collapsed)
        else:
            window._restore_collapsed = names      # at launch the lists are not there yet: applied after loading
        widths = ints(w.get("columns", ""), ",")
        if len(widths) == len(models.HEADERS):
            for col, width in enumerate(widths):
                if col not in (models.COL_CHECK, models.COL_TITLE) and 30 <= width <= 900:
                    window.table.setColumnWidth(col, width)
        window.set_games_view(w.get("view", "list"))
        known = {item.data(0, Qt.UserRole) for item in window._items()}
        scope = w.get("scope", "all")
        if loaded:
            window.select_scope(scope if scope in known else "all")
        else:
            window._restore_scope = scope
        column = ints(w.get("sort_column", ""), ",")
        if column and 0 <= column[0] < len(models.HEADERS):
            order = Qt.DescendingOrder if w.get("sort_order") == "1" else Qt.AscendingOrder
            window.table.sortByColumn(column[0], order)
        else:
            window.table.sortByColumn(models.COL_TITLE, Qt.AscendingOrder)
