"""The Saves page: what is backed up per emulator, and restoring."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import saves
from ..core.fmt import human_ago, human_since, human_size, plural
from ..core.savebackup import STAMP
from . import hig
from .workers import run_async
from .views import _page_header, _table

KIND_ROLE = Qt.UserRole + 1         # "emulator" | "item" | "note"
PACKAGE_ROLE = Qt.UserRole + 2      # the emulator a row belongs to


def backup_line(session) -> str:
    """"Last backed up 12 minutes ago", with what a run had to skip."""
    last = saves.last_backup(session)
    if last is None:
        return "Saves have not been backed up yet"
    text = f"Last backed up {human_since(datetime.strptime(last.stamp, STAMP))}"
    skipped = len(last.detail.get("skipped", []))
    return f"{text} · {plural(skipped, 'file')} could not be copied" if skipped else text


class SavesView(QWidget):
    """Saves, save states and settings per emulator: which are protected, their versions, and putting them back."""

    def __init__(self, controller, window=None):
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.controller = controller
        self.subtitle = QLabel("Saves, save states and emulator settings are copied to the library whenever the "
                               "handheld is connected, and earlier versions are kept.")
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.backup_button = QPushButton("Back Up Now")
        self.backup_button.clicked.connect(lambda: controller.backup_saves())
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addLayout(_page_header("Saves", self.subtitle), 1)
        head.addWidget(self.status_label, 0, Qt.AlignBottom)
        head.addWidget(self.backup_button, 0, Qt.AlignBottom)
        self.infobar = hig.InfoBar()
        self.tree = _table(["Name", "Kind", "Changed", "Versions", "Size"])
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        for col, width in ((1, 130), (2, 190), (3, 90), (4, 100)):
            self.tree.setColumnWidth(col, width)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self._restore_clicked())
        self.restore_button = QPushButton("Restore…")
        self.restore_all_button = QPushButton("Restore All…")
        self.restore_button.clicked.connect(self._restore_clicked)
        self.restore_all_button.clicked.connect(self._restore_all_clicked)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addStretch(1)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.restore_all_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        lay.addLayout(head)
        lay.addWidget(self.infobar)
        lay.addWidget(self.tree, 1)
        lay.addLayout(buttons)
        self._overview: list = []
        self._online = False
        self._others = False
        controller.saves_changed.connect(self.refresh)
        controller.rows_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------ contents
    def refresh(self) -> None:
        c = self.controller
        s = c.session
        keep = self.selected()
        self._overview = c.saves_overview()
        self._online = bool(s.cfg.library) and s.library.online
        self._others = self._online and saves.has_other_sets(s)    # another handheld's backups can be put here
        bold = QFont(self.font())
        bold.setBold(True)
        failed = dict(c.restore_failed)
        self.tree.blockSignals(True)
        self.tree.clear()
        for e in self._overview:
            top = QTreeWidgetItem([e.headline])
            top.setData(0, Qt.UserRole, e.package)
            top.setData(0, KIND_ROLE, "emulator")
            top.setData(0, PACKAGE_ROLE, e.package)
            top.setFont(0, bold)
            top.setToolTip(0, e.advice)
            self.tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)
            for i in e.items:
                changed = "No longer on the handheld" if i.gone else human_ago(i.changed).capitalize()
                if i.item.key in failed:
                    changed = f"Not restored: {failed[i.item.key]}"
                child = QTreeWidgetItem([i.title, i.kind, changed, str(i.versions), human_size(i.size)])
                child.setData(0, Qt.UserRole, i.item.key)
                child.setData(0, KIND_ROLE, "item")
                child.setData(0, PACKAGE_ROLE, e.package)
                child.setToolTip(0, i.item.key)
                child.setToolTip(2, changed)
                top.addChild(child)
            for rel, why in e.skipped:          # what the last backup could not copy, and why
                note = QTreeWidgetItem([rel.rsplit("/", 1)[-1], "Not Backed Up", why[:1].upper() + why[1:], "", ""])
                note.setData(0, KIND_ROLE, "note")
                note.setData(0, PACKAGE_ROLE, e.package)
                note.setToolTip(0, rel)
                top.addChild(note)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        if keep[1]:
            self.select(keep[1])
        if c.saves_busy:
            self.status_label.setText("Restoring saves…" if c.saves_restoring else "Backing up saves…")
        else:
            self.status_label.setText(backup_line(s))
        advice = next((e.advice for e in self._overview if e.advice), "")
        if not self._online:
            self.infobar.set_message("warning", saves.OFFLINE if s.cfg.library
                                     else "Choose a library folder to back up saves.")
        elif failed:
            text = "1 save was not restored. The list says why." if len(failed) == 1 \
                else f"{len(failed)} saves were not restored. The list says why for each."
            self.infobar.set_message("warning", text, "Dismiss", c.dismiss_restore_failures)
        elif advice:
            self.infobar.set_message("info", advice)
        else:
            self.infobar.set_message(None)
        self._update_buttons()
        c.read_game_ids()               # once per game file; a new answer comes back as saves_changed

    def _all_items(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            yield top
            for j in range(top.childCount()):
                yield top.child(j)

    def rows(self) -> list[tuple[str, ...]]:
        """Every row as text: one field for an emulator, five for a save."""
        return [(it.text(0),) if it.data(0, KIND_ROLE) == "emulator" else tuple(it.text(c) for c in range(5))
                for it in self._all_items()]

    def select(self, ident: str) -> None:
        """Select an emulator by package or a save by key."""
        for it in self._all_items():
            if it.data(0, Qt.UserRole) == ident:
                self.tree.setCurrentItem(it)
                return
        self.tree.setCurrentItem(None)
        self.tree.clearSelection()

    def selected(self) -> tuple[str, str]:
        """("emulator" | "item" | "", package or key)."""
        chosen = self.tree.selectedItems()
        if not chosen:
            return "", ""
        return chosen[0].data(0, KIND_ROLE), chosen[0].data(0, Qt.UserRole)

    def restore_all_target(self) -> tuple[str | None, str, int]:
        """(package, name, how many saves) "Restore All…" would act on: the selected emulator, or everything."""
        chosen = self.tree.selectedItems()
        package = chosen[0].data(0, PACKAGE_ROLE) if chosen else None
        for e in self._overview:
            if e.package == package:
                return e.package, e.label, len(e.items)
        return None, "", sum(len(e.items) for e in self._overview)

    def _update_buttons(self) -> None:
        c = self.controller
        idle = c.connected and not c.saves_busy and not c.job_running and self._online
        self.backup_button.setEnabled(idle)
        self.restore_button.setEnabled(idle and self.selected()[0] == "item")
        self.restore_all_button.setEnabled(idle and (self.restore_all_target()[2] > 0 or self._others))

    # ------------------------------------------------------------ restoring
    def _info(self, key: str):
        return next((i for e in self._overview for i in e.items if i.item.key == key), None)

    def build_restore_dialog(self, key: str):
        from . import dialogs
        return dialogs.RestoreDialog(self._info(key), saves.versions(self.controller.session, key), self)

    def restore_item(self, key: str) -> None:
        """Ask which version, then restore it."""
        if self._info(key) is None:
            return
        dlg = self.build_restore_dialog(key)
        dlg.exec()
        if dlg.clicked_role == "suggested" and dlg.chosen():
            self.controller.restore_saves([key], at=dlg.chosen())

    def restore_selected(self, at: str | None = None) -> None:
        kind, key = self.selected()
        if kind == "item":
            self.controller.restore_saves([key], at=at)

    def _restore_clicked(self) -> None:
        kind, key = self.selected()
        if kind == "item" and self.restore_button.isEnabled():
            self.restore_item(key)

    def _restore_all_clicked(self) -> None:
        from . import dialogs
        package, name, count = self.restore_all_target()
        if not (count or self._others):
            return
        noun = "item" if any(i.kind == "Settings" for e in self._overview if package in (None, e.package)
                             for i in e.items) else "save"
        session = self.controller.session
        found = saves.backup_sets(session)
        sets = None
        if self._others:            # another handheld's backups too: this handheld's own is the default
            # another handheld's set is read only when chosen, away from the UI thread (see `read`)
            sets = [(None, s.label, count, saves.backup_runs(session)) if s.own else (s, s.label, None, None)
                    for s in found]
            if not any(s.own for s in found):       # this handheld has no backups yet: it still comes first
                sets.insert(0, (None, "This handheld", count, []))

        def read(bset, ready, failed) -> None:
            def work():
                contents = saves.read_set(session, bset)
                by_id = {s.id: s for s in saves.sources(session)}
                mine = [i for i in contents.items if package is None or by_id[i.source].package == package]
                return len(mine), contents.runs, saves.restore_noun(session, [i.key for i in mine])
            run_async(work, lambda r: ready(*r),
                      lambda e: failed(str(e) if isinstance(e, saves.SavesError)
                                       else "The backups of this handheld could not be read."))
        go, at, from_set = dialogs.confirm_restore_all(self, name, count, saves.backup_runs(session), noun, sets, read)
        if go:
            self.controller.restore_all_saves(package, at, from_set)
