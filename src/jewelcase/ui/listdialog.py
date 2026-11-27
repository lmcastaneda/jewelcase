"""The dialog that makes or changes one of the owner's smart lists: a fixed form, each condition with an "any" state."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import smartlists, systems
from ..core.smartlists import UserList
from .dialogs import _button_row


def _combo(*items) -> QComboBox:
    box = QComboBox()
    for text, data in items:
        box.addItem(text, data)
    return box


def _row(*widgets) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return holder


class ListDialog(QDialog):
    """`rows` are the games the live count is made from; `existing` is the list being edited, if any."""

    def __init__(self, parent, rows, cfg, existing: UserList | None = None):
        super().__init__(parent)
        self.setWindowTitle(" ")                # HIG: dialogs carry no window title (blank, not the app name)
        self.setModal(True)
        self.rows, self.cfg, self.existing = rows, cfg, existing
        self.primary = QLabel("Which games belong in this list?")
        self.primary.setObjectName("dialogPrimary")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name of the list")
        self.hint = QLabel("")
        self.hint.setObjectName("warn")

        self.systems_list = QListWidget()
        self.systems_list.setMaximumHeight(104)
        present = {r.system for r in rows} | set(existing.systems if existing else ())
        for folder in sorted(present, key=lambda f: systems.get(f).name.lower()):
            item = QListWidgetItem(systems.get(folder).name)
            item.setData(Qt.UserRole, folder)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.systems_list.addItem(item)
        self.systems_hint = QLabel("No system ticked means any system.")
        self.systems_hint.setObjectName("muted")

        yes_no = (("Any", None), ("Yes", True), ("No", False))
        self.device_combo, self.library_combo = _combo(*yes_no), _combo(*yes_no)
        self.play_combo = _combo(("Any", "any"), ("Never played", "never"), ("Less than", "under"), ("At least", "over"))
        self.play_spin = self._spin(1, 100_000, 30)
        self.last_combo = _combo(("Any", "any"), ("More than", "before"), ("Within the last", "within"))
        self.last_spin = self._spin(1, 36_500, 90)
        self.size_combo = _combo(("Any", "any"), ("At least", "at_least"), ("At most", "at_most"))
        self.size_spin = self._spin(0, 100_000, 1)
        self.size_unit = _combo(("MB", "mb"), ("GB", "gb"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Part of the title")
        self.sort_combo = _combo(("Not sorted", ""), ("Title", "title"), ("Size", "size"),
                                 ("Play time", "played"), ("Last played", "last"))
        self.direction_combo = _combo(("Ascending", False), ("Descending", True))
        self.match_label = QLabel("")
        self.match_label.setObjectName("h2")

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("Name:", self.name_edit)
        form.addRow("", self.hint)
        systems_box = QVBoxLayout()
        systems_box.setSpacing(4)
        systems_box.addWidget(self.systems_list)
        systems_box.addWidget(self.systems_hint)
        form.addRow("Systems:", systems_box)
        form.addRow("On the handheld:", _row(self.device_combo))
        form.addRow("In the library:", _row(self.library_combo))
        form.addRow("Play time:", _row(self.play_combo, self.play_spin))
        form.addRow("Last played:", _row(self.last_combo, self.last_spin))
        form.addRow("Size:", _row(self.size_combo, self.size_spin, self.size_unit))
        form.addRow("Title contains:", self.title_edit)
        form.addRow("Sort by:", _row(self.sort_combo, self.direction_combo))

        self.cancel_button = QPushButton("Cancel")
        self.create_button = QPushButton("Save" if existing else "Create List")
        self.create_button.setObjectName("suggested")
        self.cancel_button.setAutoDefault(False)
        self.create_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.create_button.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.match_label)
        bottom.addLayout(_button_row(self.cancel_button, self.create_button), 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(12)
        lay.addWidget(self.primary)
        lay.addLayout(form)
        lay.addLayout(bottom)
        self.setMinimumWidth(520)

        if existing:
            self._load(existing)
        for signal in (self.name_edit.textChanged, self.title_edit.textChanged, self.systems_list.itemChanged,
                       self.device_combo.currentIndexChanged, self.library_combo.currentIndexChanged,
                       self.play_combo.currentIndexChanged, self.play_spin.valueChanged,
                       self.last_combo.currentIndexChanged, self.last_spin.valueChanged,
                       self.size_combo.currentIndexChanged, self.size_spin.valueChanged,
                       self.size_unit.currentIndexChanged, self.sort_combo.currentIndexChanged):
            signal.connect(self._update)
        self._update()

    @staticmethod
    def _spin(low: int, high: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        return spin

    # ------------------------------------------------------------ list <-> form
    def _load(self, ul: UserList) -> None:
        self.name_edit.setText(ul.name)
        for i in range(self.systems_list.count()):
            item = self.systems_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in ul.systems else Qt.Unchecked)
        self.device_combo.setCurrentIndex(self.device_combo.findData(ul.on_device))
        self.library_combo.setCurrentIndex(self.library_combo.findData(ul.in_library))
        self.play_combo.setCurrentIndex(self.play_combo.findData(ul.play_time))
        if ul.play_amount:
            self.play_spin.setValue(ul.play_amount)
        self.last_combo.setCurrentIndex(self.last_combo.findData(ul.last_played))
        if ul.last_days:
            self.last_spin.setValue(ul.last_days)
        self.size_combo.setCurrentIndex(self.size_combo.findData(ul.size))
        gb = ul.size_mb >= 1024 and ul.size_mb % 1024 == 0
        self.size_unit.setCurrentIndex(self.size_unit.findData("gb" if gb else "mb"))
        self.size_spin.setValue(ul.size_mb // 1024 if gb else ul.size_mb)
        self.title_edit.setText(ul.title_has)
        self.sort_combo.setCurrentIndex(self.sort_combo.findData(ul.sort))
        self.direction_combo.setCurrentIndex(self.direction_combo.findData(ul.descending))

    def current_list(self) -> UserList:
        """The list the form describes. A new list's id is made from its name when the window saves it."""
        play, last, size = (self.play_combo.currentData(), self.last_combo.currentData(),
                            self.size_combo.currentData())
        chosen = tuple(self.systems_list.item(i).data(Qt.UserRole) for i in range(self.systems_list.count())
                       if self.systems_list.item(i).checkState() == Qt.Checked)
        mb = self.size_spin.value() * (1024 if self.size_unit.currentData() == "gb" else 1)
        sort = self.sort_combo.currentData()
        return UserList(
            self.existing.id if self.existing else "", self.name_edit.text().strip(), chosen,
            self.device_combo.currentData(), self.library_combo.currentData(),
            play, self.play_spin.value() if play in ("under", "over") else 0,
            last, self.last_spin.value() if last != "any" else 0,
            size, mb if size != "any" else 0,
            self.title_edit.text().strip(), sort, bool(self.direction_combo.currentData()) if sort else False)

    # ------------------------------------------------------------ what the form shows
    def name_taken(self) -> bool:
        name = self.name_edit.text().strip().casefold()
        mine = self.existing.id if self.existing else None
        others = [u.name for u in self.cfg.user_lists if u.id != mine]
        from ..core.catalog import SMART_LISTS
        return name in {n.strip().casefold() for n in others + [s.title for s in SMART_LISTS.values()]}

    def _update(self) -> None:
        self.play_spin.setEnabled(self.play_combo.currentData() in ("under", "over"))
        self.play_spin.setSuffix(" hours" if self.play_combo.currentData() == "over" else " minutes")
        self.last_spin.setEnabled(self.last_combo.currentData() != "any")
        self.last_spin.setSuffix(" days ago" if self.last_combo.currentData() == "before" else " days")
        self.size_spin.setEnabled(self.size_combo.currentData() != "any")
        self.size_unit.setEnabled(self.size_combo.currentData() != "any")
        self.direction_combo.setEnabled(bool(self.sort_combo.currentData()))
        taken = self.name_taken()
        self.hint.setText("Another list already has this name." if taken else "")
        self.hint.setVisible(taken)
        self.create_button.setEnabled(bool(self.name_edit.text().strip()) and not taken)
        n = len(smartlists.apply(self.rows, self.current_list(), None, self.cfg.never_played_seconds))
        self.match_label.setText("No games match" if n == 0 else "1 game matches" if n == 1 else f"{n} games match")

    def buttons_in_order(self) -> list[QPushButton]:
        return [self.cancel_button, self.create_button]
