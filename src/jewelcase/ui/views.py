"""Pages other than the game table."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QProgressBar, QPushButton, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import systems
from ..core.fmt import human_size, plural
from . import hig
from .widgets import POWER, ChoiceCombo
from .workers import run_async


def _rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rule")
    line.setFixedHeight(1)
    return line


class SummaryView(QWidget):
    """The device at a glance: what it is, how full it is, and how the last sync went."""

    def __init__(self, controller, window=None):
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.controller = controller
        self._window = window
        self.title = QLabel()
        self.title.setObjectName("h1")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("muted")
        self.fields: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)
        for i, name in enumerate(("Device", "Connection", "Battery", "Games folder", "Play history",
                                  "Games", "Library", "Last sync", "Saves")):
            label = QLabel(name)
            label.setObjectName("h2")
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            self.fields[name] = value
            grid.addWidget(label, i, 0, Qt.AlignTop)
            grid.addWidget(value, i, 1)
            if name == "Games folder":
                self.change_button = QPushButton("Change…")
                self.change_button.clicked.connect(self.change_folder)
                grid.addWidget(self.change_button, i, 2, Qt.AlignTop)
        grid.setColumnStretch(1, 1)

        self.card_bar, self.card_text = QProgressBar(), QLabel()
        self.internal_bar, self.internal_text = QProgressBar(), QLabel()
        storage = QGridLayout()
        storage.setHorizontalSpacing(12)
        for row, (name, bar, text) in enumerate((("Card", self.card_bar, self.card_text),
                                                 ("Internal", self.internal_bar, self.internal_text))):
            label = QLabel(name)
            label.setObjectName("h2")
            if name == "Internal":
                self.internal_label = label
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            text.setObjectName("muted")
            storage.addWidget(label, row, 0)
            storage.addWidget(bar, row, 1)
            storage.addWidget(text, row, 2)
        storage.setColumnStretch(1, 1)

        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.clicked.connect(controller.refresh)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.rescan_button)
        self.card_button = QPushButton("Use a Card or Folder…")
        self.card_button.clicked.connect(self.choose_card)
        self.stop_card_button = QPushButton("Stop Using Card")
        self.stop_card_button.clicked.connect(lambda: controller.use_card(None))
        buttons.addWidget(self.card_button)
        buttons.addWidget(self.stop_card_button)
        buttons.addStretch(1)

        self.body = QWidget()
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)
        lay.addWidget(_rule())
        lay.addLayout(grid)
        lay.addWidget(_rule())
        lay.addLayout(storage)
        lay.addWidget(_rule())
        lay.addLayout(buttons)
        lay.addStretch(1)
        self.welcome = hig.WelcomeView("Connect a Handheld",
                                       "Switch on USB debugging on the device and plug it in.")
        self._welcome_state = None
        self.stack = QStackedWidget()
        self.stack.addWidget(self.body)
        self.stack.addWidget(self.welcome)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.addWidget(self.stack)
        controller.rows_changed.connect(self.refresh)
        controller.saves_changed.connect(self.refresh)
        self.refresh()

    def all_text(self) -> str:
        parts = [self.title.text(), self.subtitle.text(), self.card_text.text(), self.internal_text.text(),
                 ]
        parts += [v.text() for v in self.fields.values()]
        return "\n".join(parts)

    @staticmethod
    def _bar(bar: QProgressBar, text: QLabel, storage) -> None:
        if storage and storage.total:
            bar.setRange(0, 1000)
            bar.setValue(int(1000 * storage.used / storage.total))
            text.setText(f"{human_size(storage.free)} free of {human_size(storage.total)}")
        else:
            bar.setRange(0, 1)
            bar.setValue(0)
            text.setText("unknown")

    def _refresh_welcome(self) -> bool:
        """Before any device has been seen there is nothing to summarise: say what to do instead."""
        c = self.controller
        if c.info is not None:
            self.stack.setCurrentWidget(self.body)
            return False
        state = c.problem or "none"
        state = (state, str(c.session.cfg.card or ""))
        if state != self._welcome_state:
            self._welcome_state = state
            self.welcome.clear_actions()
            self.welcome.set_text("Connect a Handheld",
                                  c.problem + ("" if c.problem.endswith(".") else ".") if c.problem and c.problem != "adb not found"
                                  else "Switch on USB debugging on the device and plug it in.")
            if c.problem == "adb not found":
                self.welcome.add_action("download", "Download adb",
                                        "Jewelcase talks to the device through adb, which is not installed. "
                                        "This fetches Google’s copy into Jewelcase’s own folder.",
                                        self._download_adb)
            self.welcome.add_action("sync", "Check Again", "Look for a connected device now.", c.refresh)
            self.welcome.add_action("folder", "Use a Card or Folder…",
                                    "An SD card in a card reader, or any folder, instead of a handheld.",
                                    self.choose_card)
            if c.session.cfg.card:
                self.welcome.add_action("folder", "Stop Using Card",
                                        f"{c.session.cfg.card} is set as the device but is not mounted.",
                                        lambda: c.use_card(None))
        self.stack.setCurrentWidget(self.welcome)
        return True

    def refresh(self) -> None:
        c = self.controller
        s = c.session
        self._refresh_welcome()
        info, f = c.info, self.fields
        card = bool(info and info.transport == "card")
        if info:
            self.title.setText(info.model)
            self.subtitle.setText("Connected" if c.connected else "Not connected — this is how it looked last time")
            f["Device"].setText(f"Card or folder · serial {info.serial}" if card
                                else f"Android {info.android} · serial {info.serial}")
            f["Connection"].setText({"usb": "USB", "wifi": "Wi-Fi", "card": "Card"}.get(info.transport, info.transport))
            f["Battery"].setText("No battery" if card else f"{info.battery}% · {POWER.get(info.power, info.power)}")
        else:
            self.title.setText("No device yet")
            self.subtitle.setText(c.problem or "Plug in your handheld with USB debugging switched on.")
            for name in ("Device", "Connection", "Battery"):
                f[name].setText("–")
        p = s.profile
        from ..core import gamesfolder
        blocked = c.games_folder_blocked()
        f["Games folder"].setText("–" if not p else p.rom_root + (
            " · set by you" if gamesfolder.source(s) == "set" else " · detected") + (f"\n{blocked}" if blocked else ""))
        self.change_button.setEnabled(bool(p) and c.connected and not blocked)
        self.change_button.setToolTip(blocked or ("" if c.connected else "Connect the device to change this"))
        f["Play history"].setText("Read from ES-DE (" + p.gamelists_dir + ")" if p and p.gamelists_dir
                                  else "ES-DE was not found on the " + ("card" if card else "device")
                                  + ", so play counts are not available")
        on_dev = sum(1 for r in c.rows if r.on_device)
        in_lib = sum(1 for r in c.rows if r.in_library)
        f["Games"].setText(f"{on_dev} on the device · {in_lib} in the library")
        lib = s.cfg.library
        if not lib:
            f["Library"].setText("Not set")
        elif s.library.online:
            f["Library"].setText(f"{lib} · {human_size(s.library.free_bytes())} free on that disk")
        else:
            f["Library"].setText(f"{lib} · not available right now (showing what was last indexed)")
        last = s.store.last_job(s.serial) if s.serial else None
        result = getattr(c, "last_result", None)
        if last:
            when = (last.get("finished") or last.get("created") or "").replace("T", " ")[:16]
            text = f"{when} · {last['state']}"
            if result and result.get("job") == last["id"] and result.get("failed"):
                text += f" · {plural(len(result['failed']), 'step')} did not work"
            f["Last sync"].setText(text)
        else:
            f["Last sync"].setText("Never")
        from datetime import datetime
        from ..core import saves
        from ..core.fmt import human_since
        backed = saves.last_backup(s)
        if card:
            f["Saves"].setText("Not available for a card")
        elif lib and not s.library.online:
            f["Saves"].setText("Not backed up, the library disk is not connected")
        elif backed is None:
            f["Saves"].setText("Not backed up yet")
        else:
            f["Saves"].setText(f"Backed up {human_since(datetime.strptime(backed.stamp, '%Y%m%d-%H%M%S'))}")
        self._bar(self.card_bar, self.card_text, c.storage("card"))
        self._bar(self.internal_bar, self.internal_text, c.storage("internal"))
        for w in (self.internal_bar, self.internal_text, self.internal_label):
            w.setVisible(not card)
        handheld = c.connected and not s.is_card
        self.card_button.setVisible(not handheld)
        self.stop_card_button.setVisible(s.cfg.card is not None)

    def change_folder(self) -> None:
        from . import dialogs
        if self._window is not None:
            dialogs.open_games_folder(self._window)

    def choose_card(self) -> None:
        """Pick the folder an SD card is mounted at (or any folder) to use as the device."""
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        start = self.controller.session.cfg.card or Path("/run/media") / Path.home().name
        chosen = QFileDialog.getExistingDirectory(self, "Choose Card or Folder",
                                                  str(start if Path(start).is_dir() else Path.home()))
        if chosen:
            self.controller.use_card(Path(chosen))

    def _download_adb(self) -> None:
        from ..core import adb
        c = self.controller
        if getattr(self, "_adb_downloading", False):
            return
        self._adb_downloading = True
        c.toast.emit("Downloading adb…", None, None)

        def done(_):
            self._adb_downloading = False
            c.toast.emit("adb was downloaded", None, None)
            c.refresh()

        def failed(e):
            self._adb_downloading = False
            c.toast.emit(f"adb could not be downloaded: {e}", None, None)
        run_async(lambda: adb.download_platform_tools(c.session.data_dir), done, failed)


def _page_header(title: str, subtitle: QLabel) -> QVBoxLayout:
    head = QLabel(title)
    head.setObjectName("h1")
    subtitle.setObjectName("muted")
    subtitle.setWordWrap(True)
    lay = QVBoxLayout()
    lay.setSpacing(4)
    lay.addWidget(head)
    lay.addWidget(subtitle)
    return lay


def _table(headers: list[str]) -> QTreeWidget:
    t = QTreeWidget()
    t.setColumnCount(len(headers))
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setAlternatingRowColors(True)
    t.setUniformRowHeights(True)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection)
    t.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return t


class InboxView(QWidget):
    """New downloads that look like games, and a button to file them into the library."""

    STATES = {"ready": "Ready to import", "pick_system": "Choose a system", "downloading": "Still downloading"}

    def __init__(self, controller, window=None):
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.controller = controller
        self.folder_label = QLabel()
        self.table = _table(["File", "Size", "System", "Found By", "State"])
        self.table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 84), (2, 190), (3, 130), (4, 140)):
            self.table.setColumnWidth(col, width)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setObjectName("importBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        self.import_all = QPushButton("Import All")
        self.import_all.setObjectName("primary")
        self.import_selected = QPushButton("Import Selected")
        self.rescan = QPushButton("Rescan")
        self.import_all.clicked.connect(self.import_ready)
        self.import_selected.clicked.connect(self._import_selected)
        self.rescan.clicked.connect(controller.refresh)
        buttons = QHBoxLayout()
        buttons.addWidget(self.import_all)
        buttons.addWidget(self.import_selected)
        buttons.addWidget(self.rescan)
        buttons.addStretch(1)
        self.body = QWidget()
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(_page_header("New Downloads", self.folder_label))
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.bar)
        lay.addLayout(buttons)
        self.welcome = hig.WelcomeView("No New Downloads", "")
        self.welcome.add_action("folder", "Change Downloads Folder…",
                                "Watch a different folder for new games.", self._change_folder)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.body)
        self.stack.addWidget(self.welcome)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.addWidget(self.stack)
        self._combos: dict[str, QComboBox] = {}
        self._items: dict[str, object] = {}
        self._chosen: dict[str, str] = {}
        controller.inbox_changed.connect(self.refresh)
        controller.message.connect(self.status.setText)
        controller.import_progress.connect(self._on_import_progress)
        controller.rows_changed.connect(self._update_buttons)       # a sync starts and ends
        controller.busy_changed.connect(self._update_buttons)
        self.refresh()

    PHASES = {"checking": "Checking", "copying": "Copying", "verifying": "Verifying", "unpacking": "Unpacking",
              "compressing": "Compressing", "verifying_game": "Verifying"}

    def _on_import_progress(self, p) -> None:
        """p is a core.inbox.ImportProgress, or None when the import is over (the controller then says how it went)."""
        if p is None:
            self.bar.hide()
            return
        parts = [f"{self.PHASES.get(p.phase, p.phase)} {p.name}"]
        if p.files > 1:
            parts.append(f"file {p.file} of {p.files}")
        if p.fraction is None:                  # checking an archive: busy, no percentage
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * p.fraction))
            # every file is read twice, so the size of the import is half the work
            if p.converting:                    # the work of making the game is not a size on disk
                parts.append(f"{int(100 * p.fraction)}%")
            elif p.copy_total:                  # the size of the import, not of the work of converting it as well
                parts.append(f"{int(100 * min(1.0, p.copy_done / p.copy_total))}% of {human_size(p.copy_total // 2)}")
            else:
                parts.append(f"{int(100 * p.fraction)}% of {human_size(p.total // 2)}")
        self.bar.show()
        self.status.setText(" · ".join(parts))

    # ------------------------------------------------------------ contents
    def refresh(self) -> None:
        c = self.controller
        self.folder_label.setText(
            f"Watching {c.session.cfg.inbox}. Importing moves a file into the library and queues it for the "
            "device; the next Sync copies it over.")
        self.table.clear()
        self._combos.clear()
        self._items = {i.path.name: i for i in c.inbox_items}
        names = sorted((s.name, s.folder) for s in systems.all_systems())
        for item in c.inbox_items:
            row = QTreeWidgetItem([item.path.name, human_size(item.size), "", "–" if item.how in ("", "ambiguous") else item.how,
                                   self.STATES.get(item.state, item.state)])
            row.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            row.setToolTip(0, "\n".join(p.name for p in item.files))
            self.table.addTopLevelItem(row)
            combo = ChoiceCombo()
            combo.addItem("Choose…", None)
            for display, folder in names:
                combo.addItem(display, folder)
            chosen = self._chosen.get(item.path.name) or item.system
            if chosen:
                combo.setCurrentIndex(max(0, combo.findData(chosen)))
            combo.setEnabled(item.state != "downloading")
            combo.currentIndexChanged.connect(
                lambda _i, n=item.path.name, cb=combo: self._chosen.__setitem__(n, cb.currentData()))
            self.table.setItemWidget(row, 2, combo)
            self._combos[item.path.name] = combo
        self._update_buttons()
        self.welcome.set_text("No New Downloads", f"Games you download to {c.session.cfg.inbox} show up here.")
        self.stack.setCurrentWidget(self.body if c.inbox_items or c.inbox_busy else self.welcome)

    def _update_buttons(self) -> None:
        """The import buttons: off, with the reason, while something else has the library."""
        c = self.controller
        ready = [i for i in c.inbox_items if i.state != "downloading" and self._system_of(i.path.name)]
        why = ("Wait for the import or conversion to finish" if c.inbox_busy
               else "Wait for the sync to finish" if c.job_running and c.session.cfg.convert_on_import else "")
        self.import_all.setEnabled(bool(ready) and not why)
        self.import_selected.setEnabled(bool(c.inbox_items) and not why)
        for b in (self.import_all, self.import_selected):
            b.setToolTip(why)

    def _change_folder(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        c = self.controller
        chosen = QFileDialog.getExistingDirectory(self, "Choose Downloads Folder", str(c.session.cfg.inbox))
        if chosen:
            c.session.set_inbox(Path(chosen))
            c.refresh()

    def item_count(self) -> int:
        return self.table.topLevelItemCount()

    def _system_of(self, name: str) -> str | None:
        combo = self._combos.get(name)
        return combo.currentData() if combo else None

    def systems(self) -> dict[str, str | None]:
        return {name: self._system_of(name) for name in self._items}

    def set_system(self, name: str, folder: str) -> None:
        combo = self._combos[name]
        combo.setCurrentIndex(combo.findData(folder))

    # ------------------------------------------------------------ actions
    def _import(self, names: list[str]) -> None:
        pairs, missing = [], []
        for n in names:
            item = self._items.get(n)
            if item is None or item.state == "downloading":
                continue
            system = self._system_of(n)
            (pairs if system else missing).append((item, system) if system else n)
        if missing:
            self.status.setText("Choose a system for " + ", ".join(missing) + " first.")
        if pairs:
            self.status.setText(f"Importing {plural(len(pairs), 'file')}…")
            self.import_all.setEnabled(False)
            self.import_selected.setEnabled(False)
            self.controller.import_inbox(pairs)
            self._update_buttons()          # refused (a sync is running): the buttons come back

    def import_ready(self) -> None:
        self._import([n for n, i in self._items.items() if self._system_of(n)])

    def import_one(self, name: str) -> None:
        self._import([name])

    def _import_selected(self) -> None:
        self._import([item.text(0) for item in self.table.selectedItems()])


class EmulatorsView(QWidget):
    """Emulators on the device: versions, and installing, updating, disabling or removing them."""

    def __init__(self, controller, window=None):
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.controller = controller
        self.subtitle = QLabel()
        self.table = _table(["Emulator", "Systems", "Installed", "Latest", "State"])
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.header().setStretchLastSection(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for col, width in ((0, 170), (2, 120), (3, 130), (4, 280)):
            self.table.setColumnWidth(col, width)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        # which emulator ES-DE opens each system's games with
        self.infobar = hig.InfoBar()
        self.esde_title = QLabel("In ES-DE")
        self.esde_title.setObjectName("h2")
        self.esde_table = _table(["System", "ES-DE Opens Games With", "Note"])
        self.esde_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.esde_table.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.esde_table.header().setStretchLastSection(False)
        self.esde_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for col, width in ((0, 170), (1, 330)):
            self.esde_table.setColumnWidth(col, width)
        self._esde_combos: dict[str, QComboBox] = {}
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.check_button = QPushButton("Check for Updates")
        self.install_button = QPushButton("Install")
        self.install_button.setObjectName("primary")
        self.file_button = QPushButton("Install From File…")
        self.enable_button = QPushButton("Enable")
        self.disable_button = QPushButton("Disable")
        self.revert_button = QPushButton("Restore Factory Version…")
        self.remove_button = QPushButton("Remove…")
        self.check_button.clicked.connect(self.do_check)
        self.install_button.clicked.connect(self.do_install_latest)
        self.file_button.clicked.connect(self._choose_file)
        self.enable_button.clicked.connect(lambda: self.do_set_enabled(True))
        self.disable_button.clicked.connect(lambda: self.do_set_enabled(False))
        self.revert_button.clicked.connect(self._confirm_revert)
        self.remove_button.clicked.connect(self._confirm_remove)
        buttons = QHBoxLayout()
        for b in (self.check_button, self.install_button, self.file_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        for b in (self.enable_button, self.disable_button, self.revert_button, self.remove_button):
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        lay.addLayout(_page_header("Emulators", self.subtitle))
        lay.addWidget(self.infobar)
        lay.addWidget(self.table, 3)
        lay.addWidget(self.status)
        lay.addLayout(buttons)
        lay.addWidget(self.esde_title)
        lay.addWidget(self.esde_table, 2)
        self._rows: dict[str, object] = {}
        controller.emulators_changed.connect(self.refresh)
        controller.rows_changed.connect(self._update_buttons)
        self.refresh()

    # ------------------------------------------------------------ contents
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.controller.emulators and not self.controller.emu_busy:
            self.controller.load_emulators()

    def refresh(self) -> None:
        from ..core.emulators import describe
        c = self.controller
        keep = self.selected_package()
        self.subtitle.setText(
            "Updates come only from each project's own release page, and only when you ask. Emulators that "
            "came with the device can be disabled but not removed."
            if c.connected else "Connect the device to see and change its emulators.")
        self.table.blockSignals(True)
        self.table.clear()
        self._rows = {r.entry.package: r for r in c.emulators}
        for r in c.emulators:
            names = [systems.get(s).name for s in r.entry.systems]
            shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
            latest = r.latest or ("–" if not r.entry.source else "")
            if r.update_available:
                latest = f"{r.latest}  (newer)"
            item = QTreeWidgetItem([r.entry.name, shown or "Frontend", r.installed.version if r.installed else "–",
                                    r.error or latest, describe(r)])
            item.setData(0, Qt.UserRole, r.entry.package)
            item.setToolTip(0, r.entry.package)
            item.setToolTip(1, ", ".join(names))
            item.setToolTip(3, r.error or (r.entry.homepage if not r.entry.source else ""))
            self.table.addTopLevelItem(item)
            if r.entry.package == keep:
                self.table.setCurrentItem(item)
        self.table.blockSignals(False)
        self.status.setText(c.emu_message)
        self._refresh_esde()
        self._update_buttons()

    # ------------------------------------------------------------ what ES-DE opens games with
    def _refresh_esde(self) -> None:
        from ..core import esde
        c = self.controller
        states = [s for s in (c.esde_states or []) if s.matters] if c.connected else []
        self.esde_title.setVisible(bool(states))
        self.esde_table.setVisible(bool(states))
        self.esde_table.clear()
        self._esde_combos.clear()
        live = c.connected and not c.emu_busy and not c.job_running
        for s in states:
            note = esde.own_choices(s) if s.launch.working else "Not installed"
            if s.grant:
                note = f"{note} · {s.grant}" if note else s.grant
            row = QTreeWidgetItem([s.name, "", note])
            row.setData(0, Qt.UserRole, s.system)
            row.setToolTip(2, note)
            self.esde_table.addTopLevelItem(row)
            combo = ChoiceCombo()
            if not s.launch.working:
                combo.addItem(f"{s.launch.current} (not installed)", s.launch.current)
            for choice in s.launch.choices:
                combo.addItem(choice.label, choice.label)
            combo.setCurrentIndex(max(0, combo.findData(s.launch.current)))
            combo.setEnabled(live and combo.count() > 1)
            combo.activated.connect(lambda i, system=s.system, cb=combo: self.choose_esde(system, cb.itemData(i)))
            self.esde_table.setItemWidget(row, 1, combo)
            self._esde_combos[s.system] = combo
        broken = [s for s in states if s.fix]
        if len(broken) == 1:
            s = broken[0]
            self.infobar.set_message(
                "warning", f"ES-DE would open {s.name} games with {s.launch.current}, which is not installed.",
                f"Use {s.fix}", c.fix_esde)
        elif broken:
            self.infobar.set_message(
                "warning", f"ES-DE would open the games of {len(broken)} systems with emulators that are not "
                "installed.", "Fix All", c.fix_esde)
        else:
            gaps = [s for s in states if s.grant]       # Jewelcase cannot grant a folder itself: no button
            if len(gaps) == 1:
                self.infobar.set_message("warning", gaps[0].grant)
            elif gaps:
                self.infobar.set_message(
                    "warning", f"{len(gaps)} emulators have not been given the folder of their games yet: "
                    + ", ".join(s.name for s in gaps) + ". Open each one on the handheld and choose that folder "
                    "in the app.")
            else:
                self.infobar.set_message(None)
        self.infobar.action_button.setEnabled(live)

    def esde_rows(self) -> list[tuple[str, str, str]]:
        """(system name, the emulator shown as chosen, note) per row."""
        out = []
        for i in range(self.esde_table.topLevelItemCount()):
            item = self.esde_table.topLevelItem(i)
            out.append((item.text(0), self._esde_combos[item.data(0, Qt.UserRole)].currentText(), item.text(2)))
        return out

    def esde_choices(self, system: str) -> list[str]:
        combo = self._esde_combos[system]
        return [combo.itemText(i) for i in range(combo.count())]

    def choose_esde(self, system: str, label: str) -> None:
        state = next((s for s in self.controller.esde_states or [] if s.system == system), None)
        if state is not None and label != state.launch.current:
            self.controller.set_esde_emulator(system, label)

    def row_count(self) -> int:
        return self.table.topLevelItemCount()

    def _item(self, package: str):
        for i in range(self.table.topLevelItemCount()):
            if self.table.topLevelItem(i).data(0, Qt.UserRole) == package:
                return self.table.topLevelItem(i)
        return None

    def state_of(self, package: str) -> str:
        return self._item(package).text(4)

    def version_of(self, package: str) -> str:
        return self._item(package).text(2)

    def select(self, package: str) -> None:
        self.table.setCurrentItem(self._item(package))
        self._update_buttons()

    def selected_package(self) -> str | None:
        item = self.table.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def selected(self):
        return self._rows.get(self.selected_package())

    def _update_buttons(self) -> None:
        c = self.controller
        live = c.connected and not c.emu_busy and not c.job_running
        r = self.selected()
        info = r.installed if r else None
        self.check_button.setEnabled(live)
        self.file_button.setEnabled(live)
        self.install_button.setText("Update" if info else "Install")
        can_install = bool(live and r and r.apk_url)
        self.install_button.setEnabled(can_install)
        if r and not r.entry.source:
            tip = "This project publishes no build Jewelcase can fetch. Use Install from file."
        elif r and not r.apk_url:
            tip = "Press Check for updates first."
        else:
            tip = ""
        self.install_button.setToolTip(tip)
        self.enable_button.setEnabled(bool(live and info and not info.enabled))
        self.disable_button.setEnabled(bool(live and info and info.enabled))
        self.revert_button.setEnabled(bool(live and info and info.updated_system))
        self.remove_button.setEnabled(bool(live and info and not info.system))
        for combo in self._esde_combos.values():
            combo.setEnabled(live and combo.count() > 1)
        self.infobar.action_button.setEnabled(live)

    # ------------------------------------------------------------ actions
    def do_check(self) -> None:
        self.controller.check_emulator_updates()

    def do_install_latest(self) -> None:
        r = self.selected()
        if r:
            from ..core import emulators as emu
            cache = self.controller.session.cache_dir
            self.controller.emulator_action(lambda dev: emu.install_latest(dev, r, cache),
                                            f"{r.entry.name} {r.latest or ''} was installed".replace("  ", " "),
                                            f"Downloading {r.entry.name}…")

    def do_install_file(self, path) -> None:
        from ..core import emulators as emu
        self.controller.emulator_action(lambda dev: emu.install_from_file(dev, path),
                                        f"{getattr(path, 'name', path)} was installed", "Installing…")

    def do_set_enabled(self, enabled: bool, package: str | None = None, offer_undo: bool = True) -> None:
        from ..core import emulators as emu
        r = self._rows.get(package) if package else self.selected()
        if not r:
            return
        pkg = r.entry.package
        undo = (lambda: self.do_set_enabled(not enabled, pkg, offer_undo=False)) if offer_undo else None
        self.controller.emulator_action(lambda dev: emu.set_enabled(dev, r, enabled),
                                        f"{r.entry.name} was {'enabled' if enabled else 'disabled'}", undo=undo)

    def do_remove(self, keep_data: bool) -> None:
        r = self.selected()
        if r:
            from ..core import emulators as emu
            self.controller.emulator_action(lambda dev: emu.remove(dev, r, keep_data),
                                            f"{r.entry.name} was removed",
                                            backup_first=(r.entry.package, r.entry.name, not keep_data))

    def do_revert(self) -> None:
        r = self.selected()
        if r:
            from ..core import emulators as emu
            self.controller.emulator_action(lambda dev: emu.revert_to_factory(dev, r),
                                            f"{r.entry.name} was restored to its factory version",
                                            backup_first=(r.entry.package, r.entry.name, False))

    def _choose_file(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose an APK", str(Path.home()), "Android apps (*.apk)")
        if chosen:
            self.do_install_file(Path(chosen))

    def _confirm_remove(self) -> None:
        from . import dialogs
        r = self.selected()
        if not r:
            return

        def ask(unreadable: str, settings: str = "") -> None:   # what a look at the device just found
            go, keep = dialogs.confirm_remove_emulator(self, r.entry.name, unreadable,
                                                       **({"settings": settings} if settings else {}))
            if go:
                self.select(r.entry.package)
                self.do_remove(keep)
        self.controller.check_saves_before_removal(r.entry.package, ask)

    def _confirm_revert(self) -> None:
        from . import dialogs
        r = self.selected()
        if r and dialogs.confirm_factory_version(self, r.entry.name):
            self.do_revert()
