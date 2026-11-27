"""The sync review sheet and first-run setup."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from ..core import systems
from ..core.fmt import human_duration, human_size, plural
from ..core.plan import SyncPlan, with_copy_first
from . import hig, style

REASONS = {
    "no library copy": "Not in the library — tick to copy it there first",
    "needs conversion": "The library has it in a form the handheld cannot play",
    "library offline": "The library folder is not available right now",
}


def _button_row(*buttons) -> QHBoxLayout:
    """Buttons at the end of a dialog, 6 px apart. `None` marks where the row stretches."""
    row = QHBoxLayout()
    row.setSpacing(6)
    if None not in buttons:
        row.addStretch(1)
    for b in buttons:
        if b is None:
            row.addStretch(1)
        else:
            row.addWidget(b)
    return row


def _question(plan: SyncPlan) -> str:
    """The review's primary line: the situation as a question, in sentence case."""
    adds, removes, pulls = len(plan.adds), len(plan.removes), len(plan.pulls)
    if not (adds or removes or pulls):
        return "Nothing to sync yet"
    if adds and removes:
        return f"Add {adds} and remove {plural(removes, 'game')}?"
    if adds:
        return f"Add {plural(adds, 'game')} to the device?"
    if removes and pulls:
        return f"Copy {plural(pulls, 'game')} to the library and remove {plural(removes, 'game')}?"
    if removes:
        return f"Remove {plural(removes, 'game')} from the device?"
    return f"Copy {plural(pulls, 'game')} to the library?"


class ReviewDialog(QDialog):
    """Everything a sync will do, before it does anything."""

    def __init__(self, plan: SyncPlan, parent=None):
        super().__init__(parent)
        self.setWindowTitle(" ")                # HIG: dialogs carry no window title (blank, not the app name)
        self.setModal(True)
        self.resize(780, 520)
        self.plan = plan
        self._copy: set[tuple[str, str]] = set()
        self._sections: dict[str, QTreeWidgetItem] = {}

        self.headline = QLabel()
        self.headline.setObjectName("h1")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Game", "System", "Size", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 80)
        self.tree.itemChanged.connect(self._item_changed)
        self.space_label = QLabel()
        self.warn_label = QLabel()
        self.warn_label.setObjectName("warn")
        self.warn_label.setWordWrap(True)
        self.cancel_button = QPushButton("Cancel")
        self.sync_button = QPushButton("Sync")
        self.sync_button.setObjectName("suggested")
        self.cancel_button.setAutoDefault(False)
        self.sync_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.sync_button.clicked.connect(self.accept)
        buttons = _button_row(self.cancel_button, self.sync_button)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(self.headline)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.space_label)
        lay.addWidget(self.warn_label)
        lay.addLayout(buttons)
        self._fill()
        self._summarise()

    # ------------------------------------------------------------ contents
    def _section(self, key: str, title: str, colour: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title, "", "", ""])
        f = QFont(self.font())
        f.setBold(True)
        item.setFont(0, f)
        item.setForeground(0, QBrush(QColor(colour)))
        item.setFlags(Qt.ItemIsEnabled)
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)
        self._sections[key] = item
        return item

    def _fill(self) -> None:
        t = style.tokens()
        p = self.plan
        self.tree.blockSignals(True)
        if p.adds:
            sec = self._section("add", f"Add to the device — {plural(len(p.adds), 'game')}, "
                                       f"{human_size(p.add_bytes)}", t["add"])
            for i in p.adds:
                notes = ["Converted from its archive first; the library keeps the converted game" if i.convert else "",
                         "its cover is copied to ES-DE if it has none" if i.row.key in p.covers else ""]
                sec.addChild(self._row(i.row, i.bytes, "; ".join(n for n in notes if n)))
        if p.removes:
            sec = self._section("remove", f"Remove from the device — {plural(len(p.removes), 'game')}, "
                                          f"{human_size(p.remove_bytes)}", t["remove"])
            for i in p.removes:
                kept = len(i.row.dev_unit.files) - len(i.files)
                sec.addChild(self._row(i.row, i.bytes, f"keeps {plural(kept, 'file')} another game uses"
                                       if kept else "stays in the library"))
        if p.skipped:
            sec = self._section("skipped", f"Cannot be synced — {plural(len(p.skipped), 'game')}", t["warn"])
            for row, why in p.skipped:
                item = self._row(row, row.size, REASONS.get(why, why))
                if why == "no library copy":
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Unchecked)
                    item.setData(0, Qt.UserRole, row.key)
                sec.addChild(item)
        self.tree.expandAll()
        self.tree.blockSignals(False)

    @staticmethod
    def _row(row, size: int, note: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([row.title, systems.get(row.system).name, human_size(size), note])
        item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        item.setToolTip(0, row.stem)
        item.setToolTip(3, note)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return item

    def buttons_in_order(self) -> list[QPushButton]:
        return [self.cancel_button, self.sync_button]

    def section_count(self, key: str) -> int:
        return self._sections[key].childCount() if key in self._sections else 0

    def _item_changed(self, item, column) -> None:
        key = item.data(0, Qt.UserRole)
        if key:
            key = tuple(key)
            (self._copy.add if item.checkState(0) == Qt.Checked else self._copy.discard)(key)
            self._summarise()

    def set_copy_first(self, key: tuple[str, str], on: bool) -> None:
        sec = self._sections.get("skipped")
        for i in range(sec.childCount() if sec else 0):
            item = sec.child(i)
            if item.data(0, Qt.UserRole) and tuple(item.data(0, Qt.UserRole)) == tuple(key):
                item.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def accepted_plan(self) -> SyncPlan:
        return with_copy_first(self.plan, self._copy) if self._copy else self.plan

    def _summarise(self) -> None:
        p = self.accepted_plan()
        parts = []
        if p.adds:
            parts.append(f"add {len(p.adds)}")
        if p.removes:
            parts.append(f"remove {len(p.removes)}")
        if p.pulls:
            parts.append(f"copy {len(p.pulls)} to the library")
        self.headline.setText(_question(p))
        self.space_label.setText(
            f"Card space: {human_size(p.free_before)} free now  →  {human_size(max(0, p.free_after))} free after."
            + (f"   Takes {human_duration(p.seconds)}." if not p.empty else ""))
        warnings = list(p.warnings)
        if not p.empty and not p.fits:
            warnings.insert(0, "This does not fit on the card. Untick some games to add, or remove more.")
        self.warn_label.setText("\n".join(warnings))
        self.warn_label.setVisible(bool(warnings))
        self.sync_button.setEnabled(not p.empty and p.fits)


def sync_clicked(window) -> None:
    """The toolbar button: Pause while running, Resume an unfinished sync, else open the review."""
    c = window.controller
    if c.job_running:
        c.stop_job("pause")
        return
    job = c.open_job()
    if job is not None:
        dlg = ResumeDialog(c.remaining_steps(), window)
        dlg.accepted.connect(lambda: c.resume_job(job))
        dlg.discarded.connect(c.discard_job)
        window._resume_dialog = dlg
        dlg.open()
        return
    dlg = ReviewDialog(c.plan, window)
    dlg.accepted.connect(lambda: c.start_sync(dlg.accepted_plan()))
    window._review = dlg
    dlg.open()


class ResumeDialog(QDialog):
    """An unfinished sync is an old decision: show what is left before carrying on."""

    discarded = Signal()

    def __init__(self, left: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(" ")
        self.setModal(True)
        self.resize(560, 380)
        title = QLabel("A sync was interrupted before it finished")
        title.setObjectName("h1")
        hint = QLabel("Resuming checks every library copy again first. A game you have ticked again since "
                      "stays on the device; one you have unticked again is not added.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        for label, verb in (("remove", "Remove"), ("add", "Add"), ("copy", "Copy to the library"),
                            ("cover", "Copy the cover of")):
            for key in left.get(label, []):
                self.list.addItem(f"{verb}: {key.split('/', 1)[-1]}   ({key.split('/', 1)[0]})")
        if not self.list.count():
            self.list.addItem("Only the final refresh of the device is left.")
        self.discard_button = QPushButton("Discard Sync")
        self.cancel_button = QPushButton("Cancel")
        self.resume_button = QPushButton("Resume")
        self.resume_button.setObjectName("suggested")
        for b in (self.discard_button, self.cancel_button):
            b.setAutoDefault(False)
        self.resume_button.setDefault(True)
        self.discard_button.clicked.connect(self.discard)
        self.cancel_button.clicked.connect(self.reject)
        self.resume_button.clicked.connect(self.accept)
        buttons = _button_row(self.discard_button, None, self.cancel_button, self.resume_button)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addWidget(self.list, 1)
        lay.addLayout(buttons)

    def buttons_in_order(self) -> list[QPushButton]:
        return [self.discard_button, self.cancel_button, self.resume_button]

    def summary_text(self) -> str:
        return "\n".join(self.list.item(i).text() for i in range(self.list.count()))

    def discard(self) -> None:
        self.discarded.emit()
        self.reject()


# ---------------------------------------------------------------- first run
def _roots() -> list[Path]:
    user = os.environ.get("USER", "")
    home = Path.home()
    roots = [home, home / "Emulation", home / "Games", Path("/mnt"), Path("/media") / user,
             Path("/run/media") / user]
    return [r for r in roots if r.is_dir()]


def suggest_libraries(roots: list[Path] | None = None) -> list[Path]:
    """Folders that look like a ROM library: several known system folders inside. Looks two levels deep."""
    known = systems.known_folders()
    found: list[tuple[int, Path]] = []
    seen = set()

    def score(folder: Path) -> int:
        try:
            return sum(1 for d in folder.iterdir() if d.name in known and d.is_dir())
        except OSError:
            return 0

    def children(folder: Path) -> list[Path]:
        try:
            return [d for d in sorted(folder.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        except OSError:
            return []

    for root in roots if roots is not None else _roots():
        level1 = children(root)
        for cand in [root, *level1, *[g for c in level1 for g in children(c)]]:
            if cand in seen:
                continue
            seen.add(cand)
            n = score(cand)
            if n >= 3:
                found.append((n, cand))
    return [p for _, p in sorted(found, key=lambda x: (-x[0], str(x[1])))]


# ---------------------------------------------------------------- confirmations that cannot be undone
def build_remove_dialog(parent, name: str, unreadable: str = "", settings: str = ""):
    """`unreadable`: what of its saves Jewelcase could not back up ("10 save folders"), named in the warning.
    `settings`: the same for its settings ("1 settings file"); said once, and it does not stop the removal."""
    from PySide6.QtWidgets import QCheckBox
    text = ("This deletes the app and its private data. For some emulators that includes save files and "
            "settings. Games on the card are not touched.")
    if unreadable:
        one = unreadable.startswith("1 ") and " and " not in unreadable
        text += f" Jewelcase could not back up {unreadable} of {name}. Removing it deletes {'it' if one else 'them'}."
    if settings:
        text += f" Jewelcase could not read {settings} of {name}, so {'it is' if settings.startswith('1 ') and ' and ' not in settings else 'they are'} not backed up."
    dlg = hig.MessageDialog(parent, f"Remove {name} from the device?", text)
    keep = QCheckBox("Keep its data on the device")
    dlg.set_extra(keep)
    dlg.add_button("Cancel", "cancel")
    dlg.add_button("Remove", "destructive")
    return dlg, keep


class RestoreDialog(hig.MessageDialog):
    """Which dated version of one save to put back."""

    def __init__(self, info, versions, parent=None):
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        noun = "save state" if info.item.kind == "state" else "save"
        primary = f"Restore the {noun} for {info.title}?" if info.item.shape != "shared" \
            else f"Restore {info.title}?"
        super().__init__(parent, primary,
                         "The save on the handheld now is backed up first, so this can be undone.")
        self.headline = self.primary
        self.list = QListWidget()
        for v in versions:
            text = f"{v.when.day} {v.when:%b %Y, %H:%M} · {human_size(v.size)}"
            item = QListWidgetItem(text + (" · newest copy" if v.current else ""))
            item.setData(Qt.UserRole, v.run)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.setMinimumHeight(140)
        self.set_extra(self.list, 1)
        self.add_button("Cancel", "cancel")
        self.restore_button = self.add_button("Restore", "suggested")
        self.list.itemDoubleClicked.connect(lambda _item: self.restore_button.click())

    def chosen(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None


def build_restore_all_dialog(parent, name: str, count: int, runs: list, noun: str = "save", sets: list | None = None,
                             on_pick=None):
    """Everything, or one emulator's saves, from the newest copies or as they were at an earlier backup.

    `noun` is "item" when settings files are among what comes back. `sets` lists the backup sets of the
    library as (set or None for this handheld's own, label, count, runs); with more than one, the dialog
    offers a choice of handheld, and the first one (this handheld) is chosen. A set whose count is None has
    not been read yet: the dialog says so, calls `on_pick(set)` once when it is chosen, and waits for
    `dlg.set_ready(set, count, runs)` or `dlg.set_failed(set, text)`.
    """
    from datetime import datetime
    from PySide6.QtWidgets import QComboBox, QWidget

    def title(n: int, word_for: str = noun) -> str:
        if n == 0:
            return "This handheld has no backups to restore yet"
        word = f"1 {word_for}" if n == 1 else f"all {n} {word_for}s"
        return f"Restore {word} for {name}?" if name else f"Restore {word}?"

    dlg = hig.MessageDialog(parent, title(count),
                            "The saves on the handheld now are backed up first." if noun == "save"
                            else "What is on the handheld now is backed up first.")
    box = QComboBox()
    holder = QWidget()
    row = QVBoxLayout(holder)
    row.setContentsMargins(0, 6, 0, 0)
    row.setSpacing(6)

    def line(text: str, widget) -> None:
        r = QHBoxLayout()
        r.setSpacing(6)
        r.addWidget(QLabel(text))
        r.addWidget(widget, 1)
        row.addLayout(r)

    def fill(run_list: list) -> None:
        box.clear()
        box.addItem("The newest copies", None)
        for r in run_list:
            when = datetime.strptime(r.stamp, "%Y%m%d-%H%M%S")
            box.addItem(f"As of {when.day} {when:%b %Y, %H:%M}", r.stamp)

    go = None                                   # the Restore All button, made below
    dlg.set_box = None
    if sets and len(sets) > 1:
        state = [[*entry, noun] for entry in sets]       # [set, label, count or None, runs, noun]
        asked: set[int] = set()
        failed: dict[int, str] = {}
        set_box = QComboBox()
        for chosen, label, _, _ in sets:
            set_box.addItem(label, chosen)

        def show(index: int) -> None:
            chosen, label, n, run_list, word_for = state[index]
            if index in failed:
                dlg.primary.setText(failed[index])
                go.setEnabled(False)
                fill([])
            elif n is None:                             # not read yet: a set is read only when chosen
                dlg.primary.setText(f"Reading the backups of {label}…")
                go.setEnabled(False)
                fill([])
                if index not in asked and on_pick:
                    asked.add(index)
                    on_pick(chosen)
            else:
                dlg.primary.setText(title(n, word_for))
                go.setEnabled(n > 0)
                fill(run_list)

        def picked(index: int) -> None:
            show(index)
        set_box.currentIndexChanged.connect(picked)

        def update(chosen, count, run_list, message: str = "", word_for: str = "") -> None:
            index = next((i for i, entry in enumerate(state) if entry[0] is chosen), None)
            if index is None:
                return
            state[index][2:4] = [count, run_list]
            if word_for:
                state[index][4] = word_for
            if message:
                failed[index] = message
            if set_box.currentIndex() == index:
                show(index)
        dlg.set_ready = lambda chosen, count, run_list, word_for="": update(chosen, count, run_list, "", word_for)
        dlg.set_failed = lambda chosen, text: update(chosen, 0, [], text)
        line("Restore from handheld:", set_box)
        dlg.set_box = set_box
        fill(sets[0][3] or [])
    else:
        fill(runs)
    line("Restore from:", box)
    dlg.set_extra(holder)
    dlg.from_box = box
    dlg.chosen = lambda: box.currentData()
    dlg.chosen_set = lambda: dlg.set_box.currentData() if dlg.set_box is not None else None
    dlg.add_button("Cancel", "cancel")
    go = dlg.add_button("Restore All", "suggested")
    go.setEnabled(((sets[0][2] or 0) if sets and len(sets) > 1 else count) > 0)
    return dlg


def confirm_restore_all(parent, name: str, count: int, runs: list, noun: str = "save",
                        sets: list | None = None, reader=None) -> tuple[bool, str | None, object]:
    """(go, run to restore as of, backup set to restore from: None for this handheld's own).

    `reader(set, ready, failed)` reads a set that was chosen, away from the UI thread, and then calls
    `ready(count, runs, noun)` or `failed(text)`.
    """
    dlg = None

    def pick(chosen) -> None:
        if reader:
            reader(chosen, lambda n, r, word="": dlg.set_ready(chosen, n, r, word),
                   lambda text: dlg.set_failed(chosen, text))
    dlg = build_restore_all_dialog(parent, name, count, runs, noun, sets, pick)
    dlg.exec()
    return dlg.clicked_role == "suggested", dlg.chosen(), dlg.chosen_set()


def confirm_remove_emulator(parent, name: str, unreadable: str = "", settings: str = "") -> tuple[bool, bool]:
    dlg, keep = build_remove_dialog(parent, name, unreadable, settings)
    dlg.exec()
    return dlg.clicked_role == "destructive", keep.isChecked()


def build_factory_dialog(parent, name: str):
    dlg = hig.MessageDialog(
        parent, f"Restore the factory version of {name}?",
        "Its updates are removed and the version that came with the device takes over. Its data is kept.")
    dlg.add_button("Cancel", "cancel")
    dlg.add_button("Restore Factory Version", "destructive")
    return dlg


def confirm_factory_version(parent, name: str) -> bool:
    dlg = build_factory_dialog(parent, name)
    dlg.exec()
    return dlg.clicked_role == "destructive"


# ---------------------------------------------------------------- preferences: applied as they change
class PreferencesDialog(QDialog):
    """Not modal, no OK and no Apply: each change takes effect at once and can be changed back."""

    def __init__(self, session, controller, parent=None):
        super().__init__(parent)
        self.session, self.controller = session, controller
        self.setWindowTitle("Preferences")
        self.setModal(False)
        self.resize(560, 0)
        cfg = session.cfg
        self.library_path = QLabel(str(cfg.library) if cfg.library else "Not chosen yet")
        self.inbox_path = QLabel(str(cfg.inbox))
        for label in (self.library_path, self.inbox_path):
            label.setObjectName("muted")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.library_button = QPushButton("Change…")
        self.inbox_button = QPushButton("Change…")
        self.library_button.clicked.connect(lambda: self._browse(self.set_library, cfg.library))
        self.inbox_button.clicked.connect(lambda: self._browse(self.set_inbox, cfg.inbox))
        self.never_spin = QSpinBox()
        self.never_spin.setRange(0, 10_000_000)
        self.never_spin.setSuffix(" seconds")
        self.never_spin.setValue(cfg.never_played_seconds)
        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 3650)
        self.days_spin.setSuffix(" days")
        self.days_spin.setValue(cfg.not_played_days)
        self.never_spin.valueChanged.connect(self._thresholds_changed)
        self.days_spin.valueChanged.connect(self._thresholds_changed)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        for title, path, button in (("Library folder:", self.library_path, self.library_button),
                                    ("Downloads folder:", self.inbox_path, self.inbox_button)):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(path, 1)
            row.addWidget(button)
            form.addRow(title, row)
        form.addRow("Count as never played under:", self.never_spin)
        form.addRow("Not played lately after:", self.days_spin)
        from PySide6.QtWidgets import QCheckBox
        self.states_check = QCheckBox("Back up save states")
        self.states_check.setChecked(cfg.backup_save_states)
        self.states_check.toggled.connect(self._states_changed)
        form.addRow("Saves:", self.states_check)
        self.settings_check = QCheckBox("Back up emulator settings")
        self.settings_check.setChecked(cfg.backup_settings)
        self.settings_check.toggled.connect(self._settings_changed)
        form.addRow("", self.settings_check)
        self.covers_check = QCheckBox("Download covers for games that have none")
        self.covers_check.setChecked(cfg.download_covers)
        self.covers_check.toggled.connect(controller.set_download_covers)
        # Two lines written out, not word-wrapped: a wrapped label under-reports its height to the form,
        # and the row below was then drawn over it.
        hint = QLabel("From the libretro thumbnail collection on GitHub.\n"
                      "Each request names the game and its system, nothing else.")
        hint.setObjectName("muted")
        self.covers_hint = hint
        covers_box = QVBoxLayout()
        covers_box.setSpacing(4)
        covers_box.addWidget(self.covers_check)
        covers_box.addWidget(hint)
        form.addRow("Covers:", covers_box)
        self.convert_check = QCheckBox("Convert archives when they are imported")
        self.convert_check.setChecked(cfg.convert_on_import)
        self.convert_check.toggled.connect(self._convert_changed)
        form.addRow("Importing:", self.convert_check)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(18)
        lay.addLayout(form)
        lay.addLayout(_button_row(self.close_button))
        controller.rows_changed.connect(self._update_enabled)
        controller.inbox_changed.connect(self._update_enabled)
        controller.job_finished.connect(lambda _state: self._update_enabled())
        self._update_enabled()

    def _browse(self, apply, start) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose Folder", str(start or Path.home()))
        if chosen:
            apply(Path(chosen))

    def _busy(self) -> bool:
        c = self.controller
        return c.job_running or c.inbox_busy

    def _update_enabled(self) -> None:
        """Folders stay put while a sync or an import is using them."""
        for b in (self.library_button, self.inbox_button):
            b.setEnabled(not self._busy())
            b.setToolTip("Wait for the sync to finish" if self._busy() else "")

    def set_library(self, folder: Path) -> None:
        if self._busy():
            return
        self.session.set_library(Path(folder))
        self.library_path.setText(str(folder))
        self.controller.refresh()

    def set_inbox(self, folder: Path) -> None:
        if self._busy():
            return
        self.session.set_inbox(Path(folder))
        self.inbox_path.setText(str(folder))
        self.controller.refresh()

    def _states_changed(self, on: bool) -> None:
        self.session.cfg.backup_save_states = bool(on)
        self.session.save_config()

    def _settings_changed(self, on: bool) -> None:
        self.session.cfg.backup_settings = bool(on)
        self.session.save_config()

    def _convert_changed(self, on: bool) -> None:
        self.session.cfg.convert_on_import = bool(on)
        self.session.save_config()

    def _thresholds_changed(self) -> None:
        cfg = self.session.cfg
        cfg.never_played_seconds = self.never_spin.value()
        cfg.not_played_days = self.days_spin.value()
        self.session.save_config()
        self.controller.reload()


def open_preferences(window) -> PreferencesDialog:
    existing = getattr(window, "_preferences", None)
    if existing is None:
        existing = PreferencesDialog(window.controller.session, window.controller, window)
        window._preferences = existing
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


# ---------------------------------------------------------------- the games folder, when detection got it wrong
class GamesFolderDialog(hig.MessageDialog):
    """The folders detection considered, with what is in each, and a field for typing another path.

    `apply(path, done, failed)` and `detect(done, failed)` do the change in the background and call `done()`,
    or `failed(error)` with the reason in words (they may also raise it at once); the dialog stays open and
    shows it. Both are given by the caller."""

    def __init__(self, parent, current: str, apply, detect, can_detect: bool):
        super().__init__(parent, "Choose the games folder",
                         "This is the folder on the device that holds one folder per system (psx, gba, ps2 …). "
                         "Pick the right one, or type its path.", "folder")
        from PySide6.QtWidgets import QLineEdit
        self.current, self._apply, self._detect, self._can_detect = current, apply, detect, can_detect
        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        self.list.addItem("Looking at the device…")
        self.list.setEnabled(False)
        self.path = QLineEdit()
        self.path.setPlaceholderText("Or type another path on the device")
        self.path.setClearButtonEnabled(True)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setObjectName("hint")
        self.error.setVisible(False)
        self.set_extra(self.list, 1)
        self.set_extra(self.path)
        self.set_extra(self.error)
        self.detect_button = self.add_button("Detect Again", "other")
        self.detect_button.setEnabled(can_detect)
        self.detect_button.setToolTip("Let Jewelcase find the folder by itself again" if can_detect
                                      else "The folder in use was found by Jewelcase already")
        self.add_button("Cancel", "cancel")
        self.use_button = self.add_button("Use Folder", "suggested")
        self.list.itemSelectionChanged.connect(self._picked)
        self.path.textChanged.connect(self._typed)
        self._update()

    def set_choices(self, found: list) -> None:
        """found: core.catalog.Candidate items. Called when the device has been read."""
        self.list.clear()
        self.list.setEnabled(True)
        for c in found:
            note = f"{plural(c.systems, 'system folder')}, {plural(c.games, 'game file')}"
            item = QListWidgetItem(f"{c.path}  —  {note}" + ("  (in use)" if c.path == self.current else ""))
            item.setData(Qt.UserRole, c.path)
            self.list.addItem(item)
            if c.path == self.current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        if not found:
            self.list.addItem("No folder with system folders was found on the device")
            self.list.setEnabled(False)
        self.list.clearSelection()
        self._update()

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def chosen(self) -> str:
        """What the buttons act on: the typed path, else the picked folder."""
        typed = self.path.text().strip()
        if typed:
            return typed
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None and item.isSelected() and item.data(Qt.UserRole) else ""

    def _picked(self) -> None:
        if self.list.selectedItems():
            self.path.blockSignals(True)
            self.path.clear()
            self.path.blockSignals(False)
        self.show_error("")
        self._update()

    def _typed(self, text: str) -> None:
        if text.strip():
            self.list.clearSelection()
        self.show_error("")
        self._update()

    def _update(self) -> None:
        self.use_button.setEnabled(bool(self.chosen()))

    def _clicked(self, role: str) -> None:
        """Use Folder and Detect Again read the device, so they run in the background; the dialog closes when
        that worked and shows the reason when it did not."""
        if role not in ("suggested", "other"):
            super()._clicked(role)
            return
        for b in (self.use_button, self.detect_button):
            b.setEnabled(False)

        def failed(e) -> None:
            self.detect_button.setEnabled(self._can_detect)
            self._update()
            self.show_error(str(e))
        try:
            if role == "suggested":
                self._apply(self.chosen(), lambda: super(GamesFolderDialog, self)._clicked(role), failed)
            else:
                self._detect(lambda: super(GamesFolderDialog, self)._clicked(role), failed)
        except Exception as e:      # the change cannot be made right now (core's FolderError, in words)
            failed(e)


def open_games_folder(window) -> GamesFolderDialog:
    """Change… beside the games folder on the Device page."""
    from ..core import gamesfolder
    from .workers import run_async
    c = window.controller
    s = c.session
    dlg = GamesFolderDialog(window, s.profile.rom_root if s.profile else "", c.set_games_folder,
                            c.detect_games_folder, gamesfolder.source(s) == "set")
    run_async(lambda: gamesfolder.choices(s), dlg.set_choices,
              lambda e: (dlg.set_choices([]), dlg.show_error(f"The device could not be read: {e}")))
    window._folder_dialog = dlg
    dlg.open()
    return dlg
