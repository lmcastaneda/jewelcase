"""The main window: toolbar with the status display, source list, game table, capacity bar."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMenu, QPushButton, QSplitter, QStackedWidget, QTableView, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import catalog, smartlists, systems
from ..core.fmt import human_size, plural
from . import hig, models, posters, style
from .widgets import CapacityBar, CheckDelegate, StatusCapsule

ID_ROLE = Qt.UserRole
COUNT_ROLE = Qt.UserRole + 1
NEW_LIST_ROLE = Qt.UserRole + 2         # the "New Smart List…" entry: it acts when clicked, it is not a view


class GamesTable(QTableView):
    def __init__(self, window):
        super().__init__()
        self._window = window

    def keyPressEvent(self, event) -> None:
        if not posters.games_key(self._window, self, event):    # Space ticks, typing starts a search
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, controller, library_roots=None):
        super().__init__()
        self.controller = controller
        self.library_roots = library_roots      # where to look for library folders (None: usual places)
        self.setWindowTitle("Jewelcase")
        self.pages: dict[str, QWidget] = {}
        self._scope = "all"
        self._progress = None
        self.games_view = "list"                # or "grid": the same games shown by their covers

        # ---- top bar
        self.sync_button = QPushButton("Sync…")
        self.sync_button.setObjectName("sync")
        self.sync_button.clicked.connect(self.on_sync_clicked)
        self.capsule = StatusCapsule()
        self.header_title = self.capsule
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search Games")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(hig.shortcut_tip("Search games", QKeySequence.Find))
        for clear in self.search.findChildren(QToolButton):
            clear.setToolTip("Clear")
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("headerButton")
        self.menu_button.setIcon(hig.icon("menu", size=18))
        self.menu_button.setToolTip("Menu")
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.menu_button)
        menu.addAction("Preferences…", self.open_preferences)
        menu.addAction("Open Sync Log", self.open_sync_log)
        menu.addAction("Download Missing Covers", controller.download_missing_covers)
        self._menu_entry_action = menu.addAction("Add to Applications Menu", self.add_to_menu)
        menu.aboutToShow.connect(self._update_menu)
        self.menu_button.setMenu(menu)
        # how the games are shown: a linked pair, one of them always down
        self.list_button, self.grid_button = QToolButton(), QToolButton()
        for button, view, name, key in ((self.list_button, "list", "View as List", "Ctrl+1"),
                                        (self.grid_button, "grid", "View as Grid", "Ctrl+2")):
            button.setObjectName("headerButton")
            button.setIcon(hig.icon(view, size=18))
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setAccessibleName(name)
            button.setToolTip(f"{name} ({key})")
            button.setShortcut(QKeySequence(key))
            button.clicked.connect(lambda _checked=False, view=view: self.set_games_view(view))
        self.list_button.setChecked(True)
        top = QWidget()
        top.setObjectName("topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 6, 12, 6)
        left = QHBoxLayout()
        left.addWidget(self.sync_button)
        left.addStretch(1)
        right = QHBoxLayout()
        right.setSpacing(6)
        right.addStretch(1)
        views = QHBoxLayout()
        views.setSpacing(0)
        views.addWidget(self.list_button)
        views.addWidget(self.grid_button)
        right.addLayout(views)
        right.addWidget(self.search)             # search sits at the end of the header bar,
        right.addWidget(self.menu_button)        # and the menu after it
        tl.addLayout(left, 1)
        tl.addWidget(self.capsule, 2)
        tl.addLayout(right, 1)

        # ---- source list
        self.sidebar = QTreeWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setColumnCount(2)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setIndentation(10)
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        self.sidebar.header().setStretchLastSection(False)
        self.sidebar.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sidebar.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)
        self.sidebar.itemClicked.connect(self._sidebar_clicked)
        self.sidebar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        self._collapsed: set[str] = set()

        # ---- game table
        self.games_model = models.GamesModel(controller)
        self.games_filter = models.GamesFilter(self.games_model, controller.session.cfg)
        self.table = GamesTable(self)
        self.table.setModel(self.games_filter)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        head = self.table.horizontalHeader()
        head.setHighlightSections(False)
        head.setSectionResizeMode(models.COL_TITLE, QHeaderView.Stretch)
        for col, width in ((models.COL_CHECK, 30), (models.COL_SYSTEM, 150), (models.COL_SIZE, 84),
                           (models.COL_PLAYS, 58), (models.COL_TIME, 78), (models.COL_LAST, 112),
                           (models.COL_STATUS, 128)):
            head.setSectionResizeMode(col, QHeaderView.Fixed if col == models.COL_CHECK else QHeaderView.Interactive)
            self.table.setColumnWidth(col, width)
        self.table.setItemDelegateForColumn(models.COL_CHECK, CheckDelegate(self.table))
        self.table.sortByColumn(models.COL_TITLE, Qt.AscendingOrder)
        self.search.textChanged.connect(self.games_filter.set_text)

        # ---- the same games as posters; what is selected is shared with the list
        self.posters = posters.PosterCache(lambda: controller.art)
        self.table.setItemDelegateForColumn(models.COL_TITLE, posters.ListCoverDelegate(self.posters, self.table))
        self.posters.changed.connect(self.table.viewport().update)
        self.grid = posters.PosterGrid(self, self.posters)
        self.grid.setModel(self.games_filter)
        self.grid.setModelColumn(models.COL_TITLE)
        self.grid.setSelectionModel(self.table.selectionModel())
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._table_menu)
        self.banner = posters.SystemBanner(self.posters, lambda: controller.art)
        controller.art_changed.connect(self._art_changed)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.grid)
        self.welcome = hig.WelcomeView("No Library Yet",
                                       "Choose the folder that holds your games, one folder per system.")
        self.stack.addWidget(self.welcome)
        self._welcome_for = None
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.banner)
        cl.addWidget(self.stack, 1)
        split = self.split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(content)
        split.setStretchFactor(1, 1)
        split.setSizes([215, 965])
        split.setChildrenCollapsible(False)
        self.state_store = None                 # shell.WindowState in the real app

        # ---- bottom bar
        self.capacity = CapacityBar()
        self.pending_label = QLabel("")
        self.pending_label.setObjectName("pending")
        bottom = QWidget()
        bottom.setObjectName("bottombar")
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(4)
        bl.addWidget(self.capacity)
        bl.addWidget(self.pending_label)

        self.infobar = hig.InfoBar()            # lasting conditions, most severe first
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(top)
        lay.addWidget(self.infobar)
        lay.addWidget(split, 1)
        lay.addWidget(bottom)
        self.setCentralWidget(root)
        self.toast = hig.Toast(root, bottom_margin=84)      # floats above the bottom bar
        controller.toast.connect(self.toast.show_message)

        find = QAction(self)
        find.setShortcut(QKeySequence.Find)
        find.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(find)

        from .views import EmulatorsView, InboxView, SummaryView
        self.add_page("device:summary", SummaryView(controller, self))
        self.add_page("device:emulators", EmulatorsView(controller, self))
        from .savesview import SavesView
        self.add_page("device:saves", SavesView(controller, self))
        from .biosview import BiosView
        self.add_page("device:bios", BiosView(controller, self))
        self.add_page("inbox", InboxView(controller, self))
        controller.rows_changed.connect(self._state_changed)
        controller.job_progress.connect(self._on_progress)
        controller.job_finished.connect(self._on_finished)
        controller.import_progress.connect(self._on_import_progress)
        controller.busy_changed.connect(self._update_sync_button)
        self._state_changed()

    # ------------------------------------------------------------ pages
    def add_page(self, page_id: str, widget: QWidget) -> None:
        self.pages[page_id] = widget
        self.stack.addWidget(widget)

    # ------------------------------------------------------------ source list
    def _header(self, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text, ""])
        item.setFlags(Qt.ItemIsEnabled)
        item.setData(0, COUNT_ROLE, "section")
        item.setToolTip(0, "Click to collapse or expand")
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.9))
        item.setFont(0, f)
        item.setForeground(0, QBrush(QColor(style.tokens()["muted"])))
        self.sidebar.addTopLevelItem(item)
        return item

    def _entry(self, parent: QTreeWidgetItem, scope: str, text: str, count: int | None = None) -> None:
        item = QTreeWidgetItem([text, "" if count is None else str(count)])
        item.setData(0, ID_ROLE, scope)
        item.setData(0, COUNT_ROLE, count)
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        item.setForeground(1, QBrush(QColor(style.tokens()["muted"])))
        parent.addChild(item)

    def _rebuild_sidebar(self) -> None:
        c = self.controller
        rows = c.rows
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        lib = self._header("Library")
        self._entry(lib, "all", "All Games", len(rows))
        info = c.info
        dev = self._header("Device")
        if info:
            self._entry(dev, "device:summary", info.model)
            if not c.session.is_card:           # a card has no apps: no emulators, no saves
                self._entry(dev, "device:emulators", "Emulators")
                self._entry(dev, "device:saves", "Saves")
            elif self._scope in ("device:emulators", "device:saves"):
                self._scope = "device:summary"
                self.stack.setCurrentWidget(self.pages["device:summary"])
            self._entry(dev, "device:bios", "BIOS")
        else:
            self._entry(dev, "device:summary", "No Device Yet")
        box = self._header("Inbox")
        self._entry(box, "inbox", "New Downloads", len(c.inbox_items))
        smart = self._header("Smart Lists")
        for sid, sl in catalog.SMART_LISTS.items():
            self._entry(smart, f"smart:{sid}", sl.title, len(catalog.smart(rows, sid, c.session.cfg)))
        for ul in c.session.cfg.user_lists:
            self._entry(smart, f"smart:{ul.id}", ul.name, len(catalog.smart(rows, ul.id, c.session.cfg)))
        self._new_list_entry(smart)
        # the long list goes last, so the entries above never scroll out of reach
        per_system: dict[str, int] = {}
        for r in rows:
            per_system[r.system] = per_system.get(r.system, 0) + 1
        if per_system:
            by_system = self._header("Systems")
            for folder in sorted(per_system, key=lambda f: systems.get(f).name.lower()):
                self._entry(by_system, f"system:{folder}", systems.get(folder).name, per_system[folder])
        self.sidebar.expandAll()
        for i in range(self.sidebar.topLevelItemCount()):
            top = self.sidebar.topLevelItem(i)
            top.setExpanded(top.text(0) not in self._collapsed)
        self._select_item(self._scope)
        self.sidebar.blockSignals(False)

    def _new_list_entry(self, parent: QTreeWidgetItem) -> None:
        """Last in the Smart Lists section, where the lists are: an entry that makes a new one."""
        item = QTreeWidgetItem(["New Smart List…", ""])
        item.setFlags(Qt.ItemIsEnabled)            # not selectable: the view stays what it was
        item.setData(0, NEW_LIST_ROLE, True)
        item.setForeground(0, QBrush(QColor(style.tokens()["muted"])))
        parent.addChild(item)

    def _items(self):
        for i in range(self.sidebar.topLevelItemCount()):
            top = self.sidebar.topLevelItem(i)
            for j in range(top.childCount()):
                yield top.child(j)

    def _select_item(self, scope: str) -> None:
        for item in self._items():
            if item.data(0, ID_ROLE) == scope:
                self.sidebar.setCurrentItem(item)
                return

    def sidebar_count(self, scope: str) -> int | None:
        for item in self._items():
            if item.data(0, ID_ROLE) == scope:
                return item.data(0, COUNT_ROLE)
        return None

    def toggle_section(self, name: str) -> None:
        (self._collapsed.discard if name in self._collapsed else self._collapsed.add)(name)
        for i in range(self.sidebar.topLevelItemCount()):
            top = self.sidebar.topLevelItem(i)
            if top.text(0) == name:
                top.setExpanded(name not in self._collapsed)

    def _sidebar_clicked(self, item, _column=0) -> None:
        if item.parent() is None:
            self.toggle_section(item.text(0))
        elif item.data(0, NEW_LIST_ROLE):
            self.new_smart_list()

    # ------------------------------------------------------------ the owner's smart lists
    @staticmethod
    def _user_list_id(scope) -> str | None:
        return scope[6:] if isinstance(scope, str) and scope.startswith("smart:user:") else None

    def _user_list(self, list_id: str):
        return next((u for u in self.controller.session.cfg.user_lists if u.id == list_id), None)

    def _sidebar_menu(self, pos) -> None:
        item = self.sidebar.itemAt(pos)
        menu = self.build_sidebar_menu(item.data(0, ID_ROLE) if item is not None else None)
        if menu is not None:
            menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def build_sidebar_menu(self, scope) -> QMenu | None:
        """The menu of one of the owner's lists; None for anything else in the sidebar."""
        list_id = self._user_list_id(scope)
        if list_id is None or self._user_list(list_id) is None:
            return None
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self.edit_smart_list(list_id))
        menu.addAction("Delete", lambda: self.delete_smart_list(list_id))
        return menu

    def new_smart_list(self):
        from .listdialog import ListDialog
        dlg = ListDialog(self, self.controller.rows, self.controller.session.cfg)
        dlg.accepted.connect(lambda: self._save_smart_list(dlg.current_list(), show=True))
        self._list_dialog = dlg
        dlg.open()
        return dlg

    def edit_smart_list(self, list_id: str):
        from .listdialog import ListDialog
        existing = self._user_list(list_id)
        if existing is None:
            return None
        dlg = ListDialog(self, self.controller.rows, self.controller.session.cfg, existing)
        dlg.accepted.connect(lambda: self._save_smart_list(dlg.current_list(), show=False))
        self._list_dialog = dlg
        dlg.open()
        return dlg

    def _set_user_lists(self, lists: list) -> None:
        s = self.controller.session
        s.cfg.user_lists = lists
        s.save_config()
        self._state_changed()           # the sidebar, the counts and the games shown are made again

    def _save_smart_list(self, ul, show: bool) -> None:
        """Add the list, or replace the one with the same id (an edit or a rename keeps the id)."""
        lists = list(self.controller.session.cfg.user_lists)
        if not ul.id:
            ul = replace(ul, id=smartlists.make_id(ul.name, [u.id for u in lists]))
        at = next((i for i, u in enumerate(lists) if u.id == ul.id), None)
        if at is None:
            lists.append(ul)
        else:
            lists[at] = ul
        self._set_user_lists(lists)
        if show or self._scope == f"smart:{ul.id}":
            self.select_scope(f"smart:{ul.id}")

    def delete_smart_list(self, list_id: str) -> None:
        """Gone at once; the toast's Undo puts it back where it was."""
        lists = list(self.controller.session.cfg.user_lists)
        at = next((i for i, u in enumerate(lists) if u.id == list_id), None)
        if at is None:
            return
        gone = lists.pop(at)
        was_shown = self._scope == f"smart:{list_id}"
        self._set_user_lists(lists)
        if was_shown:
            self.select_scope("all")

        def undo() -> None:
            now = list(self.controller.session.cfg.user_lists)
            back, renamed = gone, ""
            if any(u.id == gone.id or u.name.strip().casefold() == gone.name.strip().casefold() for u in now):
                n = 2                       # a list took the name meanwhile: this one comes back under a free one
                while any(u.name.strip().casefold() == f"{gone.name} {n}".casefold() for u in now):
                    n += 1
                back = replace(gone, name=f"{gone.name} {n}", id=smartlists.make_id(f"{gone.name} {n}", [u.id for u in now]))
                renamed = back.name
            now.insert(min(at, len(now)), back)
            self._set_user_lists(now)
            if renamed:
                self.toast.show_message(f"“{gone.name}” is back as “{renamed}”, because a list with that name exists")
            if was_shown:
                self.select_scope(f"smart:{back.id}")
        self.toast.show_message(f"Deleted “{gone.name}”", "Undo", undo)

    def view_title(self) -> str:
        for item in self._items():
            if item.data(0, ID_ROLE) == self._scope:
                return item.text(0)
        return "All Games"

    def _update_title(self) -> None:
        self.setWindowTitle(f"{self.view_title()} — Jewelcase")

    # ------------------------------------------------------------ first run: a welcome, not a dialog
    def _needs_library(self) -> bool:
        s = self.controller.session
        return s.demo_device is None and s.cfg.library is None

    # ------------------------------------------------------------ list or grid
    def _games_widget(self) -> QWidget:
        return self.grid if self.games_view == "grid" else self.table

    def _showing_games(self) -> bool:
        return self.stack.currentWidget() in (self.table, self.grid)

    def set_games_view(self, view: str) -> None:
        """Show the games as a list or as a grid of posters. The selection goes along."""
        view = "grid" if view == "grid" else "list"
        keys = self.selected_keys()
        showing = self._showing_games()
        self.games_view = view
        (self.grid_button if view == "grid" else self.list_button).setChecked(True)
        if showing:
            self.stack.setCurrentWidget(self._games_widget())
            self._games_widget().setFocus()
        self._reselect(keys)

    def _art_changed(self) -> None:
        self.grid.viewport().update()
        self.table.viewport().update()          # rows that had no cover only ask for one when painted
        self._update_banner()

    def _update_banner(self) -> None:
        system = self._scope[7:] if self._scope.startswith("system:") and self._showing_games() else None
        self.banner.show_system(system, self.controller.rows)

    def _update_search(self) -> None:
        games = self._showing_games()
        self.search.setEnabled(games)
        self.list_button.setEnabled(games)
        self.grid_button.setEnabled(games)
        self._update_banner()

    def _update_welcome(self) -> None:
        from . import dialogs
        if not self._needs_library():
            if self.stack.currentWidget() is self.welcome:
                self.stack.setCurrentWidget(self._games_widget())
            self._update_search()
            return
        if self._welcome_for != "library":
            self._welcome_for = "library"
            self.welcome.clear_actions()
            for folder in dialogs.suggest_libraries(self.library_roots)[:4]:
                self.welcome.add_action("folder", f"Use {folder.name}", str(folder),
                                        lambda folder=folder: self.choose_library(folder))
            self.welcome.add_action("folder", "Choose Library Folder…",
                                    "Pick any folder that has one folder per system inside it.",
                                    self.browse_library)
        if self._showing_games():
            self.stack.setCurrentWidget(self.welcome)
            self._update_search()

    def choose_library(self, folder) -> None:
        self.controller.session.set_library(folder)
        self._welcome_for = None
        self.controller.refresh()

    def browse_library(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        chosen = QFileDialog.getExistingDirectory(self, "Choose Library Folder", str(Path.home()))
        if chosen:
            self.choose_library(Path(chosen))

    # ------------------------------------------------------------ header menu
    def _update_menu(self) -> None:
        from ..core import integrate
        self._menu_entry_action.setVisible(
            integrate.appimage_path() is not None and not integrate.menu_entry_installed())

    def open_preferences(self) -> None:
        from . import dialogs
        dialogs.open_preferences(self)

    def open_sync_log(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.session.log_path)))

    def add_to_menu(self) -> None:
        from ..core import integrate
        exe = integrate.appimage_path()
        if exe:
            try:
                integrate.install_menu_entry(exe)
            except OSError as err:
                self.toast.show_message(f"Could not add Jewelcase to the applications menu ({err.strerror or err})")
            else:
                self.toast.show_message("Jewelcase was added to the applications menu")

    def _sidebar_changed(self, item, _previous=None) -> None:
        if item is not None and item.data(0, ID_ROLE):
            self.select_scope(item.data(0, ID_ROLE))

    def select_scope(self, scope: str) -> None:
        list_id = self._user_list_id(scope)
        if list_id is not None and self._user_list(list_id) is None:
            scope, list_id = "all", None        # a list that no longer exists
        self._scope = scope
        if scope in self.pages:
            self.stack.setCurrentWidget(self.pages[scope])
        elif scope == "all" or scope.startswith(("system:", "smart:")):
            self.stack.setCurrentWidget(self.welcome if self._needs_library() else self._games_widget())
            self.games_filter.set_scope(scope)
            if scope == "smart:most_played":
                self.table.sortByColumn(models.COL_TIME, Qt.DescendingOrder)
            elif scope == "smart:largest":
                self.table.sortByColumn(models.COL_SIZE, Qt.DescendingOrder)
            elif list_id is not None and self._user_list(list_id).sort:
                ul = self._user_list(list_id)
                column = {"title": models.COL_TITLE, "size": models.COL_SIZE, "played": models.COL_TIME,
                          "last": models.COL_LAST}[ul.sort]
                self.table.sortByColumn(column, Qt.DescendingOrder if ul.descending else Qt.AscendingOrder)
            elif list_id is not None:       # no sort of its own: not the previous list's
                self.table.sortByColumn(models.COL_TITLE, Qt.AscendingOrder)
        self._update_search()
        self.sidebar.blockSignals(True)
        self._select_item(scope)
        self.sidebar.blockSignals(False)
        self._update_title()

    # ------------------------------------------------------------ ticking
    def selected_keys(self) -> list[tuple[str, str]]:
        # the grid selects one cell of a game and the list its whole row: any selected cell counts
        rows = {self.games_filter.mapToSource(i).row() for i in self.table.selectionModel().selectedIndexes()}
        return [self.games_model.rows[i].key for i in sorted(rows)]

    def set_selection_wanted(self, want: bool) -> None:
        keys = self.selected_keys()
        wanted = set(keys)
        before = {r.key: r.want for r in self.games_model.rows if r.key in wanted}
        changed = self.controller.set_want(keys, want)
        self._reselect(keys)
        if changed > 1:         # one tick is undone by ticking it back; many deserve an Undo
            self.toast.show_message(f"{changed} games {'ticked' if want else 'unticked'}", "Undo",
                                    lambda: self.controller.restore_wants(before))

    def toggle_selection(self) -> None:
        keys = set(self.selected_keys())
        any_ticked = any(r.want for r in self.games_model.rows if r.key in keys)
        self.set_selection_wanted(not any_ticked)

    def _reselect(self, keys) -> None:
        wanted = set(keys)
        sel = self.table.selectionModel()
        sel.clearSelection()
        for i, r in enumerate(self.games_model.rows):
            if r.key in wanted:
                idx = self.games_filter.mapFromSource(self.games_model.index(i, 0))
                if idx.isValid():
                    sel.select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def _table_menu(self, pos) -> None:
        menu = self.build_table_menu(self.selected_keys())
        if menu is not None:
            menu.exec(self._games_widget().viewport().mapToGlobal(pos))

    def build_table_menu(self, keys) -> QMenu | None:
        """The menu for the selected games; None when nothing is selected."""
        if not keys:
            return None
        menu = QMenu(self)
        games = hig.title_case(plural(len(keys), "game"))
        menu.addAction(f"Tick {games}", lambda: self.set_selection_wanted(True))
        menu.addAction(f"Untick {games}", lambda: self.set_selection_wanted(False))
        only = self.copyable_keys(keys)
        if only:
            menu.addSeparator()
            act = menu.addAction(f"Copy {hig.title_case(plural(len(only), 'game'))} to Library",
                                 lambda: self.controller.copy_to_library(only))
            act.setEnabled(self.controller.connected and not self.controller.job_running)
        smaller = self.controller.convertible(keys)
        for form in dict.fromkeys(f for _, f in smaller):       # a disc image the library could keep smaller
            if not any(a.isSeparator() for a in menu.actions()[-1:]):
                menu.addSeparator()
            these = [k for k, f in smaller if f == form]
            act = menu.addAction(f"Convert to {form[1:].upper()}", lambda these=these: self.controller.convert_games(these))
            act.setEnabled(not self.controller.job_running and not self.controller.inbox_busy)
        if len(keys) == 1 and not self.controller.session.is_card:     # a game with backed-up saves can have one put back
            from ..core import saves
            c = self.controller
            found = saves.find_items(c.session, keys[0][0], keys[0][1], c.rows, read=False)
            c.read_game_ids([keys[0]])      # its ID, if saves named by an ID exist and it is not known yet
            page = self.pages["device:saves"]
            ready = c.connected and not c.job_running and not c.saves_busy
            for kind, label in (("save", "Restore Save…"), ("state", "Restore Save State…")):
                item = next((i for i in found if i.kind == kind), None)
                if item is not None:
                    if not any(a.isSeparator() for a in menu.actions()[-1:]):
                        menu.addSeparator()
                    act = menu.addAction(label, lambda key=item.key: page.restore_item(key))
                    act.setEnabled(ready)
        return menu

    def copyable_keys(self, keys) -> list[tuple[str, str]]:
        """Games on the device whose files the library does not have (in that form, or at all)."""
        wanted = set(keys)
        library = self.controller.session.library
        return [r.key for r in self.games_model.rows
                if r.key in wanted and r.on_device and catalog.files_missing_from_library(r, library)]

    def closeEvent(self, event) -> None:
        """Closing never loses work: a running sync, import or conversion finishes in the background, then the app quits."""
        c = self.controller
        if self.state_store is not None:
            self.state_store.save(self)
        prefs = getattr(self, "_preferences", None)
        if prefs is not None:
            prefs.close()
        if c.job_running or c.inbox_busy:       # a sync, an import or a conversion
            c.finish_in_background = True
            event.ignore()
            self.hide()
            return
        if c.saves_restoring:                   # a restore finishes first, then the app quits
            c.quit_when_restored = True
            event.ignore()
            self.hide()
            return
        c.stop_covers()
        super().closeEvent(event)
        c.quit_requested.emit()

    def _apply_restored_view(self) -> None:
        """Saved view and collapsed sections are applied once the lists they refer to exist."""
        if not self.controller.loaded:
            return
        pending, self._restore_scope = getattr(self, "_restore_scope", None), None
        sections = {self.sidebar.topLevelItem(i).text(0) for i in range(self.sidebar.topLevelItemCount())}
        if getattr(self, "_restore_collapsed", None) is not None:
            self._collapsed = {n for n in self._restore_collapsed if n in sections}
            self._restore_collapsed = None
            for i in range(self.sidebar.topLevelItemCount()):
                top = self.sidebar.topLevelItem(i)
                top.setExpanded(top.text(0) not in self._collapsed)
        if pending and pending != self._scope and any(i.data(0, ID_ROLE) == pending for i in self._items()):
            self.select_scope(pending)

    def reopen(self) -> None:
        """Jewelcase was started again while finishing a sync in the background: show the window again."""
        self.controller.finish_in_background = False
        self.controller.quit_when_restored = False
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ status
    def conditions(self) -> list[tuple]:
        """Things that are wrong and stay wrong until something is done: (kind, text, action, callback)."""
        c = self.controller
        s = c.session
        out = []
        if c.problem == "adb not found":
            out.append(("error", "adb was not found, so Jewelcase cannot talk to the device.", "Download adb",
                        self.pages["device:summary"]._download_adb))
        if c.last_error:
            out.append(("error", f"The sync stopped unexpectedly: {c.last_error}. Nothing is lost.",
                        "Show Details…", self.show_last_result))
        if c.problem and c.problem != "adb not found":
            out.append(("warning", c.problem + ".", None, None))
        if c.open_job() is not None and not c.job_running:
            if c.connected:
                out.append(("warning", "A sync was interrupted before it finished.", "Review and Resume…",
                            self.on_sync_clicked))
            else:
                out.append(("warning", "A sync was interrupted before it finished. Connect the handheld to "
                                       "resume it.", None, None))
        if s.cfg.library is not None and not s.library.online:
            out.append(("warning", "The library folder is not available right now. Showing what was last indexed.",
                        "Check Again", c.refresh))
        if c.connected and not c.plan.empty and not c.plan.fits:
            out.append(("warning", "These changes do not fit on the card. Untick some games to add, or remove more.",
                        None, None))
        result = c.last_result or {}
        failed = result.get("failed") or []
        if failed and not result.get("seen"):
            out.append(("warning", f"{plural(len(failed), 'step')} of the last sync did not work.",
                        "Show Details…", self.show_last_result))
        if s.cfg.unreadable:
            out.append(("info", s.cfg.UNREADABLE_TEXT, None, None))
        return out

    def _update_infobar(self) -> None:
        found = self.conditions()
        if found:
            self.infobar.set_message(*found[0])
        else:
            self.infobar.set_message(None)

    def show_last_result(self) -> None:
        """What did not work in the last sync, in a dialog that names its buttons."""
        c = self.controller
        failed = (c.last_result or {}).get("failed") or []
        lines = [f"• {f['label']}: {f['message']}" for f in failed[:12]]
        if len(failed) > 12:
            lines.append(f"…and {len(failed) - 12} more in the sync log.")
        if c.last_error:
            lines.insert(0, c.last_error)
        dlg = hig.MessageDialog(
            self, f"{plural(len(failed), 'step')} of the last sync did not work" if failed
            else "The last sync stopped unexpectedly",
            "\n".join(lines) + "\n\nNothing was removed without a verified copy in the library.")
        dlg.add_button("Open Sync Log", "other").clicked.connect(self.open_sync_log)
        dlg.add_button("Close", "cancel")
        dlg.finished.connect(lambda _code: c.acknowledge_result())
        self._details_dialog = dlg
        dlg.open()

    def pending_text(self) -> str:
        c = self.controller
        plan, info, card = c.plan, c.info, c.storage()
        if not c.loaded:
            return "Reading the library…"
        if not c.connected:
            if info:
                return f"Not connected. Showing the last known state of {info.model}."
            return "Connect a handheld to see what is on it."
        if plan.empty and not plan.skipped:
            return f"In sync · {human_size(card.free)} free" if card else "In sync"
        parts = []
        if plan.adds:
            parts.append(f"+ {plural(len(plan.adds), 'game')} ({human_size(plan.add_bytes)})")
        if plan.removes:
            parts.append(f"− {plural(len(plan.removes), 'game')} ({human_size(plan.remove_bytes)})")
        text = "Pending:  " + "    ".join(parts) if parts else "Nothing to sync"
        if parts:
            text += f"    →  {human_size(max(0, plan.free_after))} free after sync"
        if plan.skipped:
            text += f"    ·  {plural(len(plan.skipped), 'game')} cannot be synced (see the review)"
        return text

    def _state_changed(self) -> None:
        c = self.controller
        self._rebuild_sidebar()
        lid = self._user_list_id(self._scope)
        if lid is not None and self._user_list(lid) is None:
            self.select_scope("all")                       # the list being shown is gone
        elif self._scope.startswith("smart:"):
            self.games_filter.set_scope(self._scope)       # a list's games change with the rows and the lists
        self.games_filter.invalidate()
        info, card = c.info, c.storage()
        last = c.session.store.last_job(c.session.serial) if c.session.serial else None
        idle = ""
        if last and last.get("finished") and last["state"] == "done":
            idle = "Last sync " + last["finished"].replace("T", " ")[:16]
        self.capsule.set_device(info, c.connected, c.problem, c.loaded, idle)
        self.pending_label.setText(self.pending_text())
        self._update_infobar()
        self.toast.set_action_enabled(not c.job_running)
        self._update_welcome()
        self._apply_restored_view()
        self._update_title()
        segments = [(s.name, s.color, size) for s, size in catalog.usage_by_system(c.rows)]
        if card:
            self.capacity.set_usage(segments, card.total, card.used,
                                    c.plan.add_bytes if c.connected else 0,
                                    c.plan.remove_bytes if c.connected else 0)
        else:
            self.capacity.set_usage([], 0, 0)
        self._update_sync_button()

    def _update_sync_button(self) -> None:
        c = self.controller
        if c.job_running:
            self.sync_button.setText("Pause")
            self.sync_button.setEnabled(True)
        elif c.connected and c.open_job() is not None:
            self.sync_button.setText("Resume…")
            self.sync_button.setEnabled(True)
        else:
            self.sync_button.setText("Sync…")
            p = c.plan
            self.sync_button.setEnabled(c.connected and ((not p.empty and p.fits) or (p.empty and bool(p.skipped))))
        waiting = c.inbox_busy and not c.job_running        # an import or a conversion has the library
        if waiting:
            self.sync_button.setEnabled(False)
        self.sync_button.setToolTip("Wait for the import or conversion to finish" if waiting else "")

    def _on_progress(self, p) -> None:
        self._progress = p
        self.capsule.set_progress(p)
        self._update_sync_button()

    def _on_import_progress(self, p) -> None:
        if self._progress is None:              # a sync that is running keeps the header
            self.capsule.set_import(p)

    def _on_finished(self, state: str) -> None:
        self._progress = None
        self.capsule.set_progress(None)
        self._update_sync_button()
        result = self.controller.last_result or {}
        if state == "done" and not result.get("failed") and (result.get("added") or result.get("removed")):
            text = f"Sync finished: {result['added']} added, {result['removed']} removed"
            if result.get("esde_later"):        # a game was running, so ES-DE could not be restarted
                text += ". ES-DE shows the changes the next time it starts"
            self.toast.show_message(text)

    def on_sync_clicked(self) -> None:
        """Filled in by the sync dialog code (see dialogs.py)."""
        from . import dialogs
        dialogs.sync_clicked(self)
