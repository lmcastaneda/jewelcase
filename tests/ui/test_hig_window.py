"""The window against the elementary HIG: wording, header bar, search, welcome screens."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QDialog, QLabel, QLineEdit, QPushButton, QToolButton

from jewelcase.ui import hig, models

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    w.select_scope("device:emulators")
    wait_until(lambda: w.pages["device:emulators"].row_count() >= 7 and not w.controller.emu_busy, 10)
    w.select_scope("all")
    yield w
    w.close()


def texts_of(win, kind):
    return [(w, w.text()) for w in win.findChildren(kind) if w.text()]


def test_every_button_and_action_is_title_case_and_never_ok(win):
    labels = [t for w, t in texts_of(win, QPushButton) if not w.property("hig-dynamic")]
    labels += [a.text() for a in win.findChildren(QAction) if a.text()]
    labels += [a.text() for a in win.menu_button.menu().actions() if a.text()]
    assert len(labels) > 12
    wrong = [t for t in labels if not hig.is_title_case(t)]
    assert wrong == []
    assert not [t for t in labels if t.strip().lower() in ("ok", "okay")]


def test_no_three_dot_ellipsis_anywhere(win):
    seen = [t for kind in (QAbstractButton, QLabel) for _, t in texts_of(win, kind)]
    seen += [e.placeholderText() for e in win.findChildren(QLineEdit)]
    seen += [a.text() for a in win.menu_button.menu().actions()]
    assert not [t for t in seen if "..." in t]


def test_sidebar_and_headers_are_title_case(win):
    items = [i.text(0) for i in win._items()]
    assert "All Games" in items and "New Downloads" in items and "Never Played" in items
    assert "Largest on Device" in items and "Not Played Lately" in items
    dynamic = {win.controller.info.model}
    assert [t for t in items if t not in dynamic and not hig.is_title_case(t)] == []
    headers = [win.games_model.headerData(c, Qt.Horizontal) for c in range(len(models.HEADERS))]
    assert headers[models.COL_LAST] == "Last Played"
    assert [h for h in headers if not hig.is_title_case(h)] == []


def test_search_placeholder_and_type_to_search(win):
    assert win.search.placeholderText() == "Search Games"
    win.table.setFocus()
    QTest.keyClicks(win.table, "lan")
    assert win.search.text() == "lan"
    assert 0 < win.games_filter.rowCount() < len(win.controller.rows)
    win.search.clear()
    win.table.selectRow(0)
    QTest.keyClick(win.table, Qt.Key_Space)             # space still toggles ticks, it does not search
    assert win.search.text() == ""


def test_window_title_follows_the_view(win):
    assert win.windowTitle() == "All Games — Jewelcase"
    win.select_scope("smart:never_played")
    assert win.windowTitle() == "Never Played — Jewelcase"
    win.select_scope("inbox")
    assert win.windowTitle() == "New Downloads — Jewelcase"


def test_sync_button_names_its_action(win):
    assert win.sync_button.text() == "Sync…"            # it opens the review, so it carries an ellipsis
    c = win.controller
    key = next(r.key for r in c.rows if r.on_device and r.in_library)
    c.set_want([key], False)
    c.session.runner().start(c.plan)
    c.refresh_blocking()
    assert win.sync_button.text() == "Resume…"


def test_icon_buttons_have_tooltips(win):
    bare = [b for b in win.findChildren(QToolButton) if not b.text() and not b.toolTip()]
    assert bare == []
    assert win.menu_button.toolTip() == "Menu"
    assert [a.text() for a in win.menu_button.menu().actions() if a.text()] == [
        "Preferences…", "Open Sync Log", "Download Missing Covers", "Add to Applications Menu"]


def test_header_title_is_flat_and_search_is_at_the_end(win):
    assert win.header_title is win.capsule
    assert "RG Demo" in win.header_title.title.text()
    assert win.search.geometry().left() > win.header_title.geometry().right()
    assert win.menu_button.geometry().left() > win.search.geometry().right()


def test_page_margins_are_at_least_12(win):
    for name, page in win.pages.items():
        m = page.layout().contentsMargins()
        assert min(m.left(), m.top(), m.right(), m.bottom()) >= 12, name


def test_no_dialog_opens_at_start(win):
    assert [d for d in win.findChildren(QDialog) if d.isVisible()] == []


def test_sidebar_sections_collapse(win):
    heads = {win.sidebar.topLevelItem(i).text(0): win.sidebar.topLevelItem(i)
             for i in range(win.sidebar.topLevelItemCount())}
    assert list(heads) == ["Library", "Device", "Inbox", "Smart Lists", "Systems"]
    systems = heads["Systems"]
    assert systems.isExpanded()
    win.toggle_section("Systems")
    assert not win.sidebar.topLevelItem(4).isExpanded()
    win.controller.refresh_blocking()                    # a refresh rebuilds the list: the choice sticks
    assert not win.sidebar.topLevelItem(4).isExpanded()
    win.toggle_section("Systems")
    assert win.sidebar.topLevelItem(4).isExpanded()


# ---------------------------------------------------------------- welcome screens instead of setup
@pytest.fixture
def fresh(qapp, tmp_path, monkeypatch):
    """A first launch: no settings, no library, no adb."""
    from jewelcase.core.session import open_session
    from jewelcase.ui.controller import Controller
    from jewelcase.ui.window import MainWindow
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("PATH", "/nonexistent")
    lib = tmp_path / "mnt" / "Disk" / "roms"
    for folder in ("psx", "ps2", "gba", "snes"):
        (lib / folder).mkdir(parents=True)
    (lib / "gba" / "A Game (USA).gba").write_bytes(b"rom")
    c = Controller(open_session(), poll=False)
    c.refresh_blocking()
    w = MainWindow(c, library_roots=[tmp_path / "mnt"])
    w.resize(1100, 700)
    w.show()
    yield w, lib
    w.close()


def test_first_run_shows_welcome_not_a_dialog(fresh):
    w, lib = fresh
    assert w.stack.currentWidget() is w.welcome
    assert [d for d in w.findChildren(QDialog) if d.isVisible()] == []
    assert w.welcome.title.text() == "No Library Yet"
    assert [a.title_text for a in w.welcome.actions] == ["Use roms", "Choose Library Folder…"]
    assert w.welcome.actions[0].description_text == str(lib)
    assert not w.sync_button.isEnabled()


def test_choosing_a_suggestion_sets_the_library(fresh, tmp_path):
    w, lib = fresh
    w.welcome.actions[0].click()
    assert wait_until(lambda: len(w.controller.rows) == 1, 10)
    assert w.stack.currentWidget() is w.table
    assert w.controller.session.cfg.library == lib
    assert "roms" in (tmp_path / "c" / "jewelcase" / "config.toml").read_text()


def test_device_page_before_any_device(fresh):
    w, _ = fresh
    page = w.pages["device:summary"]
    assert page.stack.currentWidget() is page.welcome
    assert page.welcome.title.text() == "Connect a Handheld"
    assert "Download adb" in [a.title_text for a in page.welcome.actions]


def test_empty_inbox_is_a_welcome(win, tmp_path):
    page = win.pages["inbox"]
    assert page.stack.currentWidget() is page.body
    for f in (tmp_path / "demo" / "inbox").iterdir():
        f.unlink()
    win.controller.refresh_blocking()
    assert page.stack.currentWidget() is page.welcome
    assert page.welcome.title.text() == "No New Downloads"
    assert [a.title_text for a in page.welcome.actions] == ["Change Downloads Folder…"]


def test_search_works_after_choosing_the_first_library(fresh):
    w, _ = fresh
    w.select_scope("all")                                  # touching the sidebar on the welcome screen
    w.welcome.actions[0].click()
    assert wait_until(lambda: len(w.controller.rows) == 1 and w.stack.currentWidget() is w.table, 10)
    assert w.search.isEnabled()
