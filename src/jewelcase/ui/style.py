"""Colours and the stylesheet, in a light and a dark variant."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# The elementary palette: Blueberry accent, Strawberry for destructive, Banana for warnings, Lime for success.
LIGHT = dict(
    bg="#f5f5f5", bar1="#f7f7f7", bar2="#f7f7f7", line="#d4d4d4", soft="#e4e4e4",
    text="#333333", muted="#667885", side="#ededed", base="#ffffff", alt="#f8f8f8", head="#f7f7f7",
    sel="#3689e6", sel_text="#ffffff", field="#ffffff",
    lcd1="#f7f7f7", lcd2="#f7f7f7", lcd_line="#d4d4d4", lcd_text="#333333", lcd_sub="#667885",
    btn1="#ffffff", btn2="#f4f4f4", btn_line="#c4c4c4",
    accent="#3689e6", accent_dark="#0d52bf", destructive="#c6262e", destructive_dark="#a10705",
    add="#3a9104", remove="#c6262e", warn="#ad5f00", track="#dadada", other="#abacae",
    info_bg="#ffffff", question_bg="#d9ecfc", warning_bg="#fff4c4", error_bg="#fbdcdc",
    toast_bg="#333333",
)
DARK = dict(
    bg="#2e2e2e", bar1="#262626", bar2="#262626", line="#1a1a1a", soft="#3d3d3d",
    text="#fafafa", muted="#abacae", side="#2a2a2a", base="#333333", alt="#383838", head="#2e2e2e",
    sel="#3689e6", sel_text="#ffffff", field="#262626",
    lcd1="#262626", lcd2="#262626", lcd_line="#1a1a1a", lcd_text="#fafafa", lcd_sub="#abacae",
    btn1="#484848", btn2="#404040", btn_line="#1a1a1a",
    accent="#3689e6", accent_dark="#0d52bf", destructive="#ed5353", destructive_dark="#c6262e",
    add="#9bdb4d", remove="#ed5353", warn="#ffe16b", track="#4a4a4a", other="#7e8087",
    info_bg="#3a3a3a", question_bg="#1f3f66", warning_bg="#5c4a12", error_bg="#5e2022",
    toast_bg="#1a1a1a",
)
_current = dict(LIGHT)
_name = "light"


def tokens() -> dict:
    return _current


def name() -> str:
    return _name


def detect(app: QApplication) -> str:
    try:
        return "dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    except AttributeError:
        return "light"


def apply(app: QApplication, theme: str | None = None) -> None:
    global _name
    _name = theme or detect(app)
    _current.clear()
    _current.update(DARK if _name == "dark" else LIGHT)
    t = _current
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "base"),
                      (QPalette.AlternateBase, "alt"), (QPalette.Text, "text"), (QPalette.Button, "bar2"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "sel"),
                      (QPalette.HighlightedText, "sel_text"), (QPalette.ToolTipBase, "base"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "muted")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(t["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(_SHEET % dict(t, check=_check_mark()))


def _check_mark() -> str:
    """A white tick for checked boxes, written once to the temp folder (stylesheets need a file)."""
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / "jewelcase-check.svg"
    try:
        if not path.exists():
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14"><path d="M3.2 7.4 5.9 10 '
                            '10.8 4.4" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" '
                            'stroke-linejoin="round"/></svg>')
    except OSError:
        return ""
    return str(path)


_SHEET = """
QWidget { color: %(text)s; font-size: 13px; }
QMainWindow, QDialog { background: %(bg)s; }
QWidget#topbar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(bar1)s, stop:1 %(bar2)s);
                 border-bottom: 1px solid %(line)s; }
QWidget#bottombar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(bar1)s, stop:1 %(bar2)s);
                    border-top: 1px solid %(line)s; }
QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(btn1)s, stop:1 %(btn2)s);
              border: 1px solid %(btn_line)s; border-radius: 5px; padding: 4px 14px; min-height: 18px; }
QPushButton:pressed { background: %(btn2)s; }
QPushButton:disabled { color: %(muted)s; }
QPushButton#sync { font-weight: 600; padding: 6px 18px; min-width: 76px; }
QPushButton#primary { font-weight: 600; }
QFrame#capsule { background: transparent; border: none; }
QLabel#capsuleTitle { color: %(lcd_text)s; font-weight: 600; background: transparent; }
QLabel#capsuleSub { color: %(lcd_sub)s; font-size: 12px; background: transparent; }
QProgressBar#capsuleBar { background: %(lcd_line)s; border: none; border-radius: 2px; max-height: 4px; }
QProgressBar#capsuleBar::chunk { background: %(lcd_text)s; border-radius: 2px; }
QProgressBar#importBar { background: %(soft)s; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar#importBar::chunk { background: %(accent)s; border-radius: 3px; }
QLineEdit { background: %(field)s; border: 1px solid %(line)s; border-radius: 4px; padding: 3px 6px; }
QLineEdit#search { border-radius: 11px; padding: 3px 10px; min-width: 170px; }
QTreeWidget#sidebar { background: %(side)s; border: none; border-right: 1px solid %(line)s; outline: 0;
                      font-size: 13px; }
QTreeWidget#sidebar::item { height: 24px; }
QTreeWidget#sidebar::item:selected { background: %(sel)s; color: %(sel_text)s; }
QTableView { background: %(base)s; alternate-background-color: %(alt)s; border: none; outline: 0;
             selection-background-color: %(sel)s; selection-color: %(sel_text)s; }
QTableView::item { padding: 0 6px; border: none; }
QHeaderView::section { background: %(head)s; border: none; border-right: 1px solid %(soft)s;
                       border-bottom: 1px solid %(line)s; padding: 4px 8px; font-weight: 600; }
QLabel#pending { color: %(text)s; }
QLabel#muted, QLabel#hint, QLabel#bannerDetail { color: %(muted)s; }
QFrame#systemBanner { background: %(base)s; border: none; border-bottom: 1px solid %(soft)s; }
QListView#posterGrid { background: %(base)s; border: none; }
QLabel#h1 { font-size: 20px; font-weight: 600; }
QLabel#h2 { font-size: 13px; font-weight: 600; color: %(muted)s; }
QLabel#warn { color: %(warn)s; }
QWidget#page { background: %(base)s; }
QFrame#rule { background: %(soft)s; max-height: 1px; border: none; }
QSplitter::handle { background: %(line)s; width: 1px; }
QToolTip { background: %(base)s; color: %(text)s; border: 1px solid %(line)s; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator, QTreeWidget::indicator { width: 14px; height: 14px; border: 1px solid %(muted)s;
                                               border-radius: 3px; background: %(field)s; }
QCheckBox::indicator:checked, QTreeWidget::indicator:checked { background: %(accent)s;
                                                               border-color: %(accent_dark)s;
                                                               image: url(%(check)s); }
QPushButton#suggested { background: %(accent)s; color: #ffffff; border: 1px solid %(accent_dark)s;
                        font-weight: 600; }
QPushButton#suggested:disabled { background: %(soft)s; color: %(muted)s; border-color: %(line)s; }
QPushButton#destructive { background: %(destructive)s; color: #ffffff; border: 1px solid %(destructive_dark)s;
                          font-weight: 600; }
QFrame#toast { background: %(toast_bg)s; border-radius: 6px; }
QLabel#toastLabel { color: #ffffff; background: transparent; }
QPushButton#toastAction { background: transparent; border: none; color: #8cd5ff; font-weight: 600;
                          padding: 2px 4px; min-height: 0; }
QPushButton#toastAction:disabled { color: #7e8087; }
QToolButton#toastClose { background: transparent; border: none; padding: 2px; }
QFrame#infobar { border: none; border-bottom: 1px solid %(line)s; background: %(info_bg)s; }
QFrame#infobar[kind="question"] { background: %(question_bg)s; }
QFrame#infobar[kind="warning"] { background: %(warning_bg)s; }
QFrame#infobar[kind="error"] { background: %(error_bg)s; }
QLabel#infobarLabel { background: transparent; }
QWidget#welcome { background: %(base)s; }
QLabel#welcomeTitle { font-size: 22px; font-weight: 300; }
QPushButton#welcomeAction { background: transparent; border: 1px solid transparent; border-radius: 6px;
                            text-align: left; }
QPushButton#welcomeAction:hover { background: %(alt)s; border-color: %(soft)s; }
QLabel#welcomeActionTitle { font-weight: 600; background: transparent; }
QLabel#dialogPrimary { font-size: 15px; font-weight: 600; }
QToolButton#headerButton { border: 1px solid transparent; border-radius: 5px; padding: 5px; }
QToolButton#headerButton:hover { background: %(btn2)s; border-color: %(btn_line)s; }
QToolButton#headerButton:checked { background: %(soft)s; border-color: %(sel)s; }
QToolButton#headerButton::menu-indicator { image: none; }
QListWidget { background: %(base)s; alternate-background-color: %(alt)s; border: 1px solid %(soft)s; }
"""
