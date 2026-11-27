"""The BIOS page: per system, whether the BIOS files its emulators need are on the handheld."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core import bios
from ..core.fmt import human_size
from . import hig
from .views import _page_header, _table
from .workers import run_async

STATE_TEXT = {"verified": "Verified", "incomplete": "Incomplete", "unrecognised": "Found, Not Recognised", "missing": "Missing",
              "installed": "Installed"}
NEED_TEXT = {"needed": "Needed", "optional": "Optional"}


class BiosView(QWidget):
    """Looks at the handheld off the UI thread when opened and when the device changes. It changes nothing."""

    def __init__(self, controller, window=None):
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.controller = controller
        self.subtitle = QLabel(bios.NOTE)
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.check_button = QPushButton("Check Again")
        self.check_button.clicked.connect(lambda: self.load(force=True))
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addLayout(_page_header("BIOS", self.subtitle), 1)
        head.addWidget(self.status_label, 0, Qt.AlignBottom)
        head.addWidget(self.check_button, 0, Qt.AlignBottom)
        self.infobar = hig.InfoBar()
        self.tree = _table(["System", "Needs", "State", "Details"])
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        for col, width in ((0, 200), (1, 90), (2, 190)):
            self.tree.setColumnWidth(col, width)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        lay.addLayout(head)
        lay.addWidget(self.infobar)
        lay.addWidget(self.tree, 1)
        self.busy = False
        self.results: list[bios.SystemBios] = []
        self._key: tuple | None = None          # which device the results are for
        controller.rows_changed.connect(self._device_maybe_changed)
        controller.job_finished.connect(self._job_ended)
        self._update()

    # ------------------------------------------------------------ loading
    def _device_key(self) -> tuple:
        c = self.controller
        return (c.connected, c.session.serial)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.load()

    def _device_maybe_changed(self) -> None:
        if self._device_key() != self._key and self.isVisible():
            self.load()

    def _job_ended(self, _result) -> None:
        if self.isVisible() and not (self.controller.bios_cache and self.controller.bios_cache[0] == self._device_key()):
            self.load()                 # the page was opened during the sync and has nothing to show yet
        else:
            self._update()

    def load(self, force: bool = False) -> None:
        """Look at the handheld in the background, once per connection: the last result is shown again
        unless `force` (Check Again) or the device changed. Never while a sync is running. A look already
        running is left to finish."""
        c = self.controller
        self._key = self._device_key()
        if self.busy:
            return
        if not c.connected:
            self.results = []
            self._update()
            return
        cached = c.bios_cache if c.bios_cache and c.bios_cache[0] == self._key else None
        if c.job_running or (cached and not force):
            self.results = cached[1] if cached else []
            self._update()
            return
        self.busy = True
        key = self._key
        rows = list(c.rows)
        self._update()

        def done(results):
            self.busy = False
            self.results = results
            c.bios_cache = (key, results)
            self._update()
            if key != self._device_key():       # the device changed while it was looking
                self.load()

        def failed(_error):
            self.busy = False
            self.results = []
            self._update(failed=True)

        run_async(lambda: bios.check(c.session, rows), done, failed)

    # ------------------------------------------------------------ contents
    def _update(self, failed: bool = False) -> None:
        c = self.controller
        self.check_button.setEnabled(c.connected and not self.busy and not c.job_running)
        if not c.connected:
            self.status_label.setText("")
            self.infobar.set_message("info", "Connect the handheld to check its BIOS files.")
        elif failed:
            self.infobar.set_message("warning", "The handheld could not be read. Check that it is connected.")
        elif c.job_running:
            self.infobar.set_message("info", "A sync is running, so the handheld is not looked at again until it is done."
                                     + (" This is what was found before." if self.results else ""))
        else:
            self.infobar.set_message(None)
        self.status_label.setText("Looking…" if self.busy else "")
        self._fill()

    def _fill(self) -> None:
        bold = QFont(self.font())
        bold.setBold(True)
        self.tree.clear()
        for r in self.results:
            top = QTreeWidgetItem([r.name, NEED_TEXT[r.need], STATE_TEXT[r.state], " ".join(t for t in (r.missing, r.note) if t)])
            top.setData(0, Qt.UserRole, r.folder)
            top.setFont(0, bold)
            if r.has_games:
                top.setToolTip(0, "Games for this system are on the handheld or ticked to go there")
            for f in r.files:
                detail = f.path if not f.known_as else f"{f.path}   ({f.known_as})"
                child = QTreeWidgetItem([f.path.rsplit("/", 1)[-1], "", STATE_TEXT[f.state],
                                         detail if f.state != "installed" else f.path])
                child.setToolTip(3, f.path + (f" · {human_size(f.size)}" if f.size else ""))
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(bool(r.files))

    def rows(self) -> list[tuple[str, ...]]:
        """Every row as text (System, Needs, State, Details), systems and their files in order."""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            out.append(tuple(top.text(c) for c in range(4)))
            out += [tuple(top.child(j).text(c) for c in range(4)) for j in range(top.childCount())]
        return out
