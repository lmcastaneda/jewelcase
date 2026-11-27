"""Table models for the game list."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..core import catalog, systems
from ..core.fmt import human_ago, human_size, human_time
from . import style

COL_CHECK, COL_TITLE, COL_SYSTEM, COL_SIZE, COL_PLAYS, COL_TIME, COL_LAST, COL_STATUS = range(8)
HEADERS = ["", "Title", "System", "Size", "Plays", "Time", "Last Played", "Status"]
SORT_ROLE = Qt.UserRole
ROW_ROLE = Qt.UserRole + 1          # the GameRow itself, whatever the column
STATUS = {"on_device": "On device", "library_only": "In library", "device_only": "Device only",
          "needs_conversion": "Needs conversion"}


def cannot_convert(row) -> bool:
    """Ticked, but there is nothing a sync could make it from."""
    return (row.state == "needs_conversion" and row.want and not row.on_device
            and (row.convert is None or bool(row.convert.problem)))


def status_text(row) -> str:
    if cannot_convert(row):
        return "Cannot be converted"
    if row.want and not row.on_device:
        return "Will be converted and added" if row.state == "needs_conversion" else "Will be added"
    if row.on_device and not row.want:
        return "Will be removed"
    if row.state == "needs_conversion" and row.archive:
        return "Archive in library"
    return STATUS[row.state]


def conversion_tip(row) -> str:
    """The Status tooltip of a game the library has no playable form of."""
    if row.want and row.convert is not None:
        return row.convert.problem or f"{', '.join(row.convert.files)}, made from {row.convert.archive}"
    if row.archive:
        return ("The library has this game as an archive. Tick it and the next sync converts it first; "
                "the converted game is kept beside the archive.")
    return "The library has this game in a form the handheld cannot play, and Jewelcase cannot convert it."


def _is_checked(value) -> bool:
    return getattr(value, "value", value) == Qt.Checked.value


class GamesModel(QAbstractTableModel):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.rows = list(controller.rows)
        controller.rows_changed.connect(self.refresh)

    def refresh(self) -> None:
        new = list(self.controller.rows)
        if [r.key for r in new] == [r.key for r in self.rows]:
            self.rows = new
            if new:
                self.dataChanged.emit(self.index(0, 0), self.index(len(new) - 1, len(HEADERS) - 1))
        else:
            self.beginResetModel()
            self.rows = new
            self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return HEADERS[section]
            if role == Qt.TextAlignmentRole:
                side = Qt.AlignRight if section in (COL_SIZE, COL_PLAYS, COL_TIME) else Qt.AlignLeft
                return int(side | Qt.AlignVCenter)
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_CHECK:
            r = self.rows[index.row()]
            if self.controller.can_tick and (r.on_device or r.lib_unit is not None or r.archive):
                return base | Qt.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.DisplayRole):
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == COL_TITLE:
                return r.title
            if col == COL_SYSTEM:
                return systems.get(r.system).name
            if col == COL_SIZE:
                return human_size(r.size)
            if col == COL_PLAYS:
                return str(r.plays) if r.plays else "–"
            if col == COL_TIME:
                return human_time(r.seconds)
            if col == COL_LAST:
                return human_ago(r.last_played) if r.plays or r.last_played else ("never" if r.on_device else "–")
            if col == COL_STATUS:
                return status_text(r)
            return None
        if role == Qt.CheckStateRole and col == COL_CHECK:
            return Qt.Checked if r.want else Qt.Unchecked
        if role == ROW_ROLE:
            return r
        if role == SORT_ROLE:
            return (int(r.want), r.title.lower(), systems.get(r.system).name.lower(), r.size, r.plays, r.seconds,
                    r.last_played.timestamp() if r.last_played else 0.0, status_text(r))[col]
        if role == Qt.TextAlignmentRole and col in (COL_SIZE, COL_PLAYS, COL_TIME):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            t = style.tokens()
            if col == COL_STATUS:
                if cannot_convert(r):
                    return QColor(t["warn"])
                if r.want and not r.on_device:
                    return QColor(t["add"])
                if r.on_device and not r.want:
                    return QColor(t["remove"])
                if r.state == "device_only" or (r.state == "needs_conversion" and not r.archive):
                    return QColor(t["warn"])
                return QColor(t["muted"])
            if col in (COL_PLAYS, COL_TIME, COL_LAST, COL_SYSTEM, COL_SIZE):
                return QColor(t["muted"]) if not r.on_device and not r.want else None
        if role == Qt.ToolTipRole:
            unit = r.dev_unit or r.lib_unit
            if col == COL_STATUS and r.state == "needs_conversion":
                return conversion_tip(r)
            if col == COL_STATUS and r.state == "device_only":
                return "This game is on the device but not in the library."
            return "\n".join(unit.files) if unit else r.stem
        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role != Qt.CheckStateRole or index.column() != COL_CHECK:
            return False
        r = self.rows[index.row()]
        self.controller.set_want([r.key], _is_checked(value))
        return True


class GamesFilter(QSortFilterProxyModel):
    def __init__(self, model: GamesModel, cfg):
        super().__init__()
        self.cfg = cfg
        self._scope = "all"
        self._text = ""
        self._smart: set = set()
        self.setSourceModel(model)
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(False)
        model.modelReset.connect(self._recompute)

    def _recompute(self) -> None:
        if self._scope.startswith("smart:"):
            rows = self.sourceModel().rows
            self._smart = {r.key for r in catalog.smart(rows, self._scope[6:], self.cfg)}
        self.invalidate()

    def set_scope(self, scope: str) -> None:
        self._scope = scope
        self._recompute()

    def set_text(self, text: str) -> None:
        self._text = text.strip().lower()
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, parent) -> bool:
        r = self.sourceModel().rows[source_row]
        if self._scope.startswith("system:") and r.system != self._scope[7:]:
            return False
        if self._scope.startswith("smart:") and r.key not in self._smart:
            return False
        if self._text and self._text not in r.title.lower() and self._text not in r.stem.lower() \
                and self._text not in systems.get(r.system).name.lower():
            return False
        return True
