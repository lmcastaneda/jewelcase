"""The status display at the top and the capacity bar at the bottom."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QFrame, QLabel, QProgressBar, QSizePolicy, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from ..core.fmt import human_size
from . import style

POWER = {"ac": "on wall power", "usb": "charging from USB", "none": "on battery"}
KIND = {"push": "Copying", "pull": "Copying to library", "verify": "Checking", "capture": "Saving play history",
        "gamelist": "Updating game list", "delete": "Removing", "delete_art": "Removing art",
        "check": "Confirming", "refresh": "Refreshing the device", "convert": "Converting"}
WAIT = {"waiting_device": "Waiting for the device — the sync will pick up where it stopped",
        "waiting_emulator": "Waiting for the game on the device to close",
        "low_battery": "Battery is low — waiting for it to charge"}


class ChoiceCombo(QComboBox):
    """A chooser inside a list. A choice is only made by picking an entry from the opened list: the mouse
    wheel scrolls the list the chooser sits in, and the arrow keys open the list of entries."""

    def wheelEvent(self, event) -> None:
        event.ignore()                              # passes to the list underneath, which scrolls

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            self.showPopup()
            return
        if event.text().strip() and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            return                                  # typing a letter would otherwise jump to an entry and pick it
        super().keyPressEvent(event)


class StatusCapsule(QFrame):
    """Two lines in a rounded panel: who is connected, and what is happening."""

    def __init__(self):
        super().__init__()
        self.setObjectName("capsule")
        self.setMinimumWidth(380)
        self.setMaximumWidth(560)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.title = QLabel("Looking for a device…")
        self.title.setObjectName("capsuleTitle")
        self.sub = QLabel("")
        self.sub.setObjectName("capsuleSub")
        self.bar = QProgressBar()
        self.bar.setObjectName("capsuleBar")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(4)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 5, 14, 6)
        lay.setSpacing(1)
        for w in (self.title, self.sub):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w)
        lay.addWidget(self.bar)
        self.bar.setVisible(False)
        self._idle_sub = ""

    def set_device(self, info, connected: bool, problem: str, loaded: bool, idle: str = "") -> None:
        if not loaded:
            self.title.setText("Looking for a device…")
            self._idle_sub = ""
        elif connected and info and info.transport == "card":
            self.title.setText(f"{info.model} · Card")
            self._idle_sub = idle or "Card or folder connected"
        elif connected and info:
            self.title.setText(f"{info.model} · {info.battery}% · {POWER.get(info.power, info.power)}")
            self._idle_sub = idle or ("Connected over Wi-Fi" if info.transport == "wifi" else "Connected over USB")
        else:
            self.title.setText("No device")
            self._idle_sub = problem or (f"Last seen: {info.model}" if info else "Plug in your handheld to begin")
        if not self.bar.isVisible():
            self.sub.setText(self._idle_sub)

    def set_import(self, p) -> None:
        """p is a core.inbox.ImportProgress, or None when no import is running."""
        if p is None:
            self.bar.setVisible(False)
            self.sub.setText(self._idle_sub)
            return
        self.bar.setVisible(True)
        if p.fraction is None:                  # checking an archive: busy, no percentage
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * p.fraction))
        verb = CONVERT_VERBS.get(p.phase, "Importing")      # making the game from an archive says what it is doing
        text = f"{verb} {p.name}" + (f"  ·  file {p.file} of {p.files}" if p.files > 1 else "")
        self.sub.setText(QFontMetrics(self.sub.font()).elidedText(text, Qt.ElideMiddle, max(200, self.width() - 40)))

    def set_progress(self, p) -> None:
        """p is a core.sync.Progress, or None when no job is running."""
        if p is None or p.state in ("done", "cancelled", "paused"):
            self.bar.setVisible(False)
            self.sub.setText(self._idle_sub)
            return
        self.bar.setVisible(True)
        if p.bytes_total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * min(1.0, p.bytes_done / p.bytes_total)))
        else:
            self.bar.setRange(0, max(1, p.step_count))
            self.bar.setValue(p.step_idx + 1)
        if p.state in WAIT:
            self.sub.setText(WAIT[p.state])
        else:
            text = f"{KIND.get(p.kind, p.kind)} {p.label}".strip()
            if p.kind == "convert" and p.message:
                text += f"  ·  {p.message}"         # unpacking, compressing or checking
            self.sub.setText(QFontMetrics(self.sub.font()).elidedText(
                f"{text}  ·  step {p.step_idx + 1} of {p.step_count}", Qt.ElideMiddle, max(200, self.width() - 40)))


CONVERT_VERBS = {"unpacking": "Unpacking", "compressing": "Compressing", "verifying_game": "Verifying"}


def draw_check(painter, box: QRectF, checked: bool, checkable: bool, selected: bool) -> None:
    """The tick box, drawn by hand so it reads the same in light and dark, on every distro."""
    t = style.tokens()
    if checked:
        fill = QColor("#ffffff") if selected else QColor(t["sel"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3.5, 3.5)
        pen = QPen(QColor(t["sel"]) if selected else QColor("#ffffff"), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(box.left() + 3.4, box.top() + 7.4)
        path.lineTo(box.left() + 6.0, box.top() + 10.0)
        path.lineTo(box.left() + 10.8, box.top() + 4.4)
        painter.drawPath(path)
    else:
        edge = QColor("#ffffff") if selected else QColor(t["muted"])
        if not checkable:
            edge.setAlpha(70)
        painter.setPen(QPen(edge, 1.2))
        painter.setBrush(QColor(t["field"]) if checkable and not selected else Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.6, 0.6, -0.6, -0.6), 3.2, 3.2)


class CheckDelegate(QStyledItemDelegate):
    """The tick column of the list."""

    def paint(self, painter, option, index) -> None:
        selected = bool(option.state & QStyle.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if selected:
            painter.fillRect(option.rect, QColor(style.tokens()["sel"]))
        box = QRectF(0, 0, 14, 14)
        box.moveCenter(QRectF(option.rect).center())
        checked = getattr(index.data(Qt.CheckStateRole), "value", index.data(Qt.CheckStateRole)) == Qt.Checked.value
        draw_check(painter, box, checked, bool(index.flags() & Qt.ItemIsUserCheckable), selected)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if not (index.flags() & Qt.ItemIsUserCheckable):
            return False
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            current = getattr(index.data(Qt.CheckStateRole), "value", index.data(Qt.CheckStateRole))
            return model.setData(index, Qt.Unchecked if current == Qt.Checked.value else Qt.Checked,
                                 Qt.CheckStateRole)
        return event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick)


class CapacityBar(QWidget):
    """Card usage by system, with what a pending sync would add and free."""

    def __init__(self):
        super().__init__()
        self.segments: list[tuple[str, str, int]] = []      # (label, colour, bytes)
        self.total = 0
        self.used = 0
        self.pending_add = 0
        self.pending_remove = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(400, 40)

    def set_usage(self, segments, total: int, used: int, pending_add: int = 0, pending_remove: int = 0) -> None:
        self.segments, self.total, self.used = list(segments), total, used
        self.pending_add, self.pending_remove = pending_add, pending_remove
        self.setToolTip("\n".join(f"{name}: {human_size(size)}" for name, _, size in self.legend()))
        self.update()

    def legend(self) -> list[tuple[str, str, int]]:
        if not self.total:
            return []
        t = style.tokens()
        games = sum(s for _, _, s in self.segments)
        shown = self.segments[:5]
        rest = sum(s for _, _, s in self.segments[5:])
        out = list(shown)
        if rest:
            out.append(("Other games", "#9aa4b1", rest))
        other = max(0, self.used - games)
        if other:
            out.append(("Other files", t["other"], other))
        out.append(("Free", t["track"], max(0, self.total - self.used)))
        return out

    def paintEvent(self, event) -> None:
        t = style.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        bar = QRectF(0.5, 2.5, w - 1, 13)
        clip = QPainterPath()
        clip.addRoundedRect(bar, 6.5, 6.5)
        p.fillPath(clip, QColor(t["track"]))
        if self.total:
            p.save()
            p.setClipPath(clip)
            x = bar.left()
            for name, colour, size in self.legend()[:-1]:
                width = bar.width() * size / self.total
                p.fillRect(QRectF(x, bar.top(), width, bar.height()), QColor(colour))
                x += width
            if self.pending_add:
                width = bar.width() * self.pending_add / self.total
                c = QColor(t["add"])
                c.setAlpha(170)
                p.fillRect(QRectF(x, bar.top(), width, bar.height()), c)
            p.restore()
        p.setPen(QColor(t["line"]))
        p.drawPath(clip)
        # legend
        fm = p.fontMetrics()
        x, y = 2.0, 23.0
        for name, colour, size in self.legend():
            label = f"{name}  {human_size(size)}"
            width = fm.horizontalAdvance(label)
            if x + 14 + width > w:
                break
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(colour))
            p.drawRoundedRect(QRectF(x, y + 2, 9, 9), 2, 2)
            p.setPen(QColor(t["muted"]))
            p.drawText(QRectF(x + 14, y - 2, width + 2, 17), Qt.AlignLeft | Qt.AlignVCenter, label)
            x += 14 + width + 16
        p.end()
