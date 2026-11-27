"""The elementary HIG's patterns as small Qt widgets: toast, infobar, welcome view, message dialog.

Rules these encode (docs.elementary.io/hig):
- Toasts affirm something the user just did, dismiss themselves, and may carry one action (Undo).
- Infobars state a lasting condition with at most one action; they are not dismissed automatically.
- Welcome views replace empty lists and setup dialogs: a situation line and action rows.
- Message dialogs have no title, a primary and a secondary text, explicit action buttons in the
  order other | Cancel | action, and at most one suggested or destructive button.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from . import style

# ---------------------------------------------------------------- writing style
_SMALL = {"a", "an", "the", "and", "but", "or", "nor", "for", "yet", "so", "at", "by", "in", "of", "on",
          "to", "as", "per", "via", "vs"}       # not "up": in our labels it belongs to a verb ("Back Up")
_KEEP_LOWER = {"adb"}


def title_case(text: str) -> str:
    """AP title case: first and last word, and every word but short articles, prepositions, conjunctions."""
    words = text.split(" ")
    last = len(words) - 1
    out = []
    for i, word in enumerate(words):
        core = word.rstrip("…:")
        tail = word[len(core):]
        low = core.lower()
        if not core or low in _KEEP_LOWER and core == low:
            out.append(word)
        elif any(c.isupper() for c in core[1:]) or any(c.isdigit() for c in core):
            out.append(word)                                    # names and numbers keep their own form
        elif 0 < i < last and low in _SMALL:
            out.append(low + tail)
        else:
            out.append(core[0].upper() + core[1:] + tail)
    return " ".join(out)


def is_title_case(text: str) -> bool:
    return title_case(text) == text


def shortcut_tip(text: str, key) -> str:
    """A tooltip in sentence case with its keyboard shortcut after it, as the HIG asks for tool items."""
    from PySide6.QtGui import QKeySequence
    seq = QKeySequence(key).toString(QKeySequence.NativeText)
    return f"{text} ({seq})" if seq else text


# ---------------------------------------------------------------- symbolic icons (no system theme needed)
ICONS = {
    "folder": "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "device": "M5 6h14a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3V9a3 3 0 0 1 3-3zm3.5 2.5v7h7v-7z",
    "download": "M11 3h2v9l3-3 1.4 1.4L12 15.8 6.6 10.4 8 9l3 3zM4 18h16v2H4z",
    "menu": "M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z",
    "list": "M4 5h3v3H4zm5 .5h11v2H9zM4 10.5h3v3H4zm5 .5h11v2H9zM4 16h3v3H4zm5 .5h11v2H9z",
    "grid": "M4 4h7v7H4zm9 0h7v7h-7zM4 13h7v7H4zm9 0h7v7h-7z",
    "close": "M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12 19 17.6 17.6 19 12 13.4 6.4 19 5 17.6 10.6 12 5 6.4z",
    "inbox": "M4 4h16l2 9v6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-6zm1.6 2-1.5 7H8a4 4 0 0 0 8 0h3.9l-1.5-7z",
    "warning": "M12 3 22 20H2zm-1 6v6h2V9zm0 8v2h2v-2z",
    "sync": "M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z",
}


def icon(name: str, colour: str | None = None, size: int = 24) -> QIcon:
    path = ICONS[name]
    colour = colour or style.tokens()["text"]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path fill="{colour}" fill-rule="evenodd" d="{path}"/></svg>')
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size * 2, size * 2))
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


# ---------------------------------------------------------------- toast
class Toast(QFrame):
    """A short confirmation floating over the content, with an optional single action."""

    TIMEOUT_MS = 5000
    TIMEOUT_WITH_ACTION_MS = 8000
    dismissed = Signal()

    def __init__(self, parent: QWidget, bottom_margin: int = 24):
        super().__init__(parent)
        self.setObjectName("toast")
        self._bottom = bottom_margin
        self._on_action: Callable | None = None
        self.label = QLabel()
        self.label.setObjectName("toastLabel")
        self.action_button = QPushButton()
        self.action_button.setObjectName("toastAction")
        self.action_button.setCursor(Qt.PointingHandCursor)
        self.action_button.clicked.connect(self._act)
        self.close_button = QToolButton()
        self.close_button.setObjectName("toastClose")
        self.close_button.setIcon(icon("close", "#ffffff", 14))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setToolTip("Dismiss")
        self.close_button.clicked.connect(self.dismiss)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(12)
        lay.addWidget(self.label)
        lay.addWidget(self.action_button)
        lay.addWidget(self.close_button)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, action: str | None = None, on_action: Callable | None = None) -> None:
        """Show (or replace) the message. The previous message's action is dropped, not run."""
        self._on_action = on_action if action else None
        self.label.setText(text)
        self.action_button.setText(action or "")
        self.action_button.setVisible(bool(action))
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(self.TIMEOUT_WITH_ACTION_MS if action else self.TIMEOUT_MS)

    def set_action_enabled(self, enabled: bool) -> None:
        self.action_button.setEnabled(enabled)

    def _act(self) -> None:
        callback, self._on_action = self._on_action, None
        self.dismiss()
        if callback:
            callback()

    def dismiss(self) -> None:
        self._timer.stop()
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    def _place(self) -> None:
        host = self.parentWidget()
        if host:
            self.adjustSize()
            self.move((host.width() - self.width()) // 2, host.height() - self.height() - self._bottom)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self._place()
        return False


# ---------------------------------------------------------------- infobar
KINDS = ("info", "question", "warning", "error")


class InfoBar(QFrame):
    """A strip under the header for a condition that lasts until something changes."""

    def __init__(self):
        super().__init__()
        self.setObjectName("infobar")
        self.kind: str | None = None
        self._on_action: Callable | None = None
        self.label = QLabel()
        self.label.setObjectName("infobarLabel")
        self.label.setWordWrap(True)
        self.action_button = QPushButton()
        self.action_button.clicked.connect(lambda: self._on_action and self._on_action())
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(12)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.action_button)
        self.hide()

    def set_message(self, kind: str | None, text: str = "", action: str | None = None,
                    on_action: Callable | None = None) -> None:
        if kind is None:
            self.kind = None
            self.hide()
            return
        if kind not in KINDS:
            raise ValueError(f"unknown infobar kind {kind!r}")
        self.kind = kind
        self._on_action = on_action
        self.label.setText(text)
        self.action_button.setText(action or "")
        self.action_button.setVisible(bool(action))
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()


# ---------------------------------------------------------------- welcome view
class _WelcomeAction(QPushButton):
    def __init__(self, icon_name: str, title: str, description: str):
        super().__init__()
        self.setObjectName("welcomeAction")
        self.setCursor(Qt.PointingHandCursor)
        self.title_text, self.description_text = title, description
        self.setProperty("hig-dynamic", True)
        picture = QLabel()
        picture.setPixmap(icon(icon_name, style.tokens()["accent"], 32).pixmap(32, 32))
        picture.setFixedSize(32, 32)
        name = QLabel(title)
        name.setObjectName("welcomeActionTitle")
        about = QLabel(description)
        about.setObjectName("muted")
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(name)
        text.addWidget(about)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(12)
        lay.addWidget(picture)
        lay.addLayout(text, 1)
        for child in (picture, name, about):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # a push button sizes itself for its (empty) text; this one must fit the two lines inside it
        self._height = name.sizeHint().height() + about.sizeHint().height() + 22
        self.setMinimumHeight(self._height)

    def sizeHint(self) -> QSize:
        return QSize(360, self._height)


class WelcomeView(QWidget):
    """What to show instead of an empty list or a setup dialog: the situation, then what can be done."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setObjectName("welcome")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.title = QLabel(title)
        self.title.setObjectName("welcomeTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("muted")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.actions: list[QPushButton] = []
        self._rows = QVBoxLayout()
        self._rows.setSpacing(6)
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addStretch(2)
        column.addWidget(self.title)
        column.addWidget(self.subtitle)
        column.addSpacing(18)
        column.addLayout(self._rows)
        column.addStretch(3)
        holder = QWidget()
        holder.setLayout(column)
        holder.setMaximumWidth(520)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        outer.addWidget(holder, 4)
        outer.addStretch(1)

    def set_text(self, title: str, subtitle: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)

    def add_action(self, icon_name: str, title: str, description: str, callback: Callable) -> QPushButton:
        button = _WelcomeAction(icon_name, title, description)
        button.clicked.connect(lambda: callback())
        self._rows.addWidget(button)
        self.actions.append(button)
        return button

    def clear_actions(self) -> None:
        for b in self.actions:
            self._rows.removeWidget(b)
            b.deleteLater()
        self.actions = []


# ---------------------------------------------------------------- message dialog
class MessageDialog(QDialog):
    """Primary text, secondary text, optional extra widget, and buttons that say what they do."""

    def __init__(self, parent: QWidget | None, primary: str, secondary: str = "", icon_name: str = "warning"):
        super().__init__(parent)
        self.setWindowTitle(" ")        # blank, so the toolkit does not put the app name there instead
        self.setModal(True)
        self.clicked_role: str | None = None
        self._buttons: list[tuple[str, QPushButton]] = []
        picture = QLabel()
        picture.setPixmap(icon(icon_name, style.tokens()["muted"], 48).pixmap(48, 48))
        picture.setFixedSize(48, 48)
        self.primary = QLabel(primary)
        self.primary.setObjectName("dialogPrimary")
        self.primary.setWordWrap(True)
        self.secondary = QLabel(secondary)
        self.secondary.setWordWrap(True)
        self.secondary.setVisible(bool(secondary))
        self._text = QVBoxLayout()
        self._text.setSpacing(6)
        self._text.addWidget(self.primary)
        self._text.addWidget(self.secondary)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(picture, 0, Qt.AlignTop)
        top.addLayout(self._text, 1)
        self._row = QHBoxLayout()
        self._row.setSpacing(6)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(18)
        lay.addLayout(top, 1)
        lay.addLayout(self._row)
        self.setMinimumWidth(440)

    def set_extra(self, widget: QWidget, stretch: int = 0) -> None:
        self._text.addWidget(widget, stretch)

    def add_button(self, text: str, role: str) -> QPushButton:
        if role not in ("cancel", "suggested", "destructive", "other"):
            raise ValueError(role)
        if role in ("suggested", "destructive") and any(r in ("suggested", "destructive") for r, _ in self._buttons):
            raise ValueError("a dialog has at most one suggested or destructive button")
        button = QPushButton(text)
        button.setAutoDefault(False)
        if role in ("suggested", "destructive"):
            button.setObjectName(role)
        button.clicked.connect(lambda: self._clicked(role))
        self._buttons.append((role, button))
        self._relayout()
        return button

    def buttons_in_order(self) -> list[QPushButton]:
        rank = {"other": 0, "cancel": 1, "suggested": 2, "destructive": 2}
        return [b for _, b in sorted(self._buttons, key=lambda rb: rank[rb[0]])]

    def _relayout(self) -> None:
        while self._row.count():
            self._row.takeAt(0)
        ordered = self.buttons_in_order()
        roles = {b: r for r, b in self._buttons}
        placed_stretch = False
        for b in ordered:
            if roles[b] != "other" and not placed_stretch:
                self._row.addStretch(1)
                placed_stretch = True
            self._row.addWidget(b)
        if not placed_stretch:
            self._row.addStretch(1)
        default = next((b for r, b in self._buttons if r == "suggested"), None) or \
            next((b for r, b in self._buttons if r == "cancel"), None)
        for _, b in self._buttons:
            b.setDefault(b is default)
        if default:
            default.setFocus()

    def _clicked(self, role: str) -> None:
        self.clicked_role = role
        if role == "cancel":
            super().reject()
        elif role == "other":
            self.done(2)
        else:
            self.accept()

    def reject(self) -> None:
        self.clicked_role = "cancel"
        super().reject()
