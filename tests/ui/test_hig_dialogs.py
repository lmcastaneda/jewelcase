"""Dialogs by the HIG's rules, and preferences that apply the moment they change."""
import pytest
from PySide6.QtWidgets import QPushButton

from jewelcase.ui import dialogs
from jewelcase.ui.dialogs import ResumeDialog, ReviewDialog

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.close()


def keys(win, n, predicate=lambda r: r.on_device and r.in_library):
    return [r.key for r in win.controller.rows if predicate(r)][:n]


def labels(dialog):
    return [b.text() for b in dialog.buttons_in_order()]


def test_review_dialog_follows_dialog_rules(win):
    c = win.controller
    c.set_want(keys(win, 2), False)
    c.set_want(keys(win, 1, lambda r: r.state == "library_only"), True)
    dlg = ReviewDialog(c.plan, win)
    assert dlg.windowTitle().strip() == ""
    assert dlg.headline.text() == "Add 1 and remove 2 games?"
    assert labels(dlg) == ["Cancel", "Sync"]
    assert dlg.sync_button.isDefault() and dlg.sync_button.objectName() == "suggested"
    assert not [b for b in dlg.findChildren(QPushButton) if b.text().lower() in ("ok", "okay")]


def test_review_dialog_headlines(win):
    c = win.controller
    c.set_want(keys(win, 2), False)
    assert ReviewDialog(c.plan, win).headline.text() == "Remove 2 games from the device?"
    c.set_want(keys(win, 2), True)
    c.set_want(keys(win, 1, lambda r: r.state == "library_only"), True)
    assert ReviewDialog(c.plan, win).headline.text() == "Add 1 game to the device?"


def test_resume_dialog_buttons(win):
    dlg = ResumeDialog({"remove": ["ps2/Some Game (USA)"], "add": [], "copy": []}, win)
    assert dlg.windowTitle().strip() == ""
    assert labels(dlg) == ["Discard Sync", "Cancel", "Resume"]
    assert dlg.resume_button.isDefault() and dlg.resume_button.objectName() == "suggested"
    assert "Some Game (USA)" in dlg.summary_text()


def test_remove_emulator_dialog(win):
    dlg, keep = dialogs.build_remove_dialog(win, "ARMSX2")
    assert dlg.windowTitle().strip() == "" and dlg.primary.text() == "Remove ARMSX2 from the device?"
    assert "private data" in dlg.secondary.text()
    assert labels(dlg) == ["Cancel", "Remove"]
    remove = dlg.buttons_in_order()[1]
    assert remove.objectName() == "destructive" and not remove.isDefault()
    assert dlg.buttons_in_order()[0].isDefault()
    assert keep.text() == "Keep its data on the device" and not keep.isChecked()


def test_factory_version_dialog(win):
    dlg = dialogs.build_factory_dialog(win, "PPSSPP")
    assert dlg.primary.text() == "Restore the factory version of PPSSPP?"
    assert labels(dlg) == ["Cancel", "Restore Factory Version"]
    assert dlg.buttons_in_order()[1].objectName() == "destructive"


def test_preferences_apply_immediately(win, tmp_path):
    c = win.controller
    dlg = dialogs.open_preferences(win)
    assert dlg.isVisible() and not dlg.isModal()
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert "Close" in texts and not [t for t in texts if t in ("OK", "Apply", "Save")]
    on_device = sum(1 for r in c.rows if r.on_device)
    assert win.sidebar_count("smart:never_played") < on_device
    dlg.never_spin.setValue(1_000_000)                    # no confirm button: it has already happened
    assert win.sidebar_count("smart:never_played") == on_device
    assert c.session.cfg.never_played_seconds == 1_000_000
    dlg.days_spin.setValue(1)
    assert c.session.cfg.not_played_days == 1

    new_lib = tmp_path / "newlib"
    (new_lib / "gba").mkdir(parents=True)
    (new_lib / "gba" / "Only One (USA).gba").write_bytes(b"rom")
    (new_lib / "snes").mkdir()
    dlg.set_library(new_lib)
    assert dlg.library_path.text() == str(new_lib)
    assert wait_until(lambda: any(r.stem == "Only One (USA)" for r in c.rows), 10)
    new_inbox = tmp_path / "elsewhere"
    new_inbox.mkdir()
    dlg.set_inbox(new_inbox)
    assert c.session.cfg.inbox == new_inbox and dlg.inbox_path.text() == str(new_inbox)


def test_preferences_reachable_from_the_menu(win):
    action = next(a for a in win.menu_button.menu().actions() if a.text() == "Preferences…")
    action.trigger()
    first = win._preferences
    assert first.isVisible()
    action.trigger()
    assert win._preferences is first                       # raised, not opened twice


def test_no_setup_dialog_left(qapp):
    assert not hasattr(dialogs, "SetupDialog")


def test_the_resume_dialog_lists_covers_that_are_left_instead_of_only_the_refresh(qapp):
    from jewelcase.ui.dialogs import ResumeDialog
    dlg = ResumeDialog({"remove": [], "add": [], "copy": [], "cover": ["ps2/Champions (USA)"]})
    texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert texts == ["Copy the cover of: Champions (USA)   (ps2)"]
    assert "Only the final refresh" not in " ".join(texts)


def test_every_row_of_the_preferences_is_fully_visible(win):
    """The hint under the covers switch was cut off by the row below it once more rows were added."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication
    from jewelcase.ui import dialogs
    dialogs.open_preferences(win)
    prefs = win._preferences
    for _ in range(5):
        QApplication.processEvents()

    def box(widget):
        return QRect(widget.mapTo(prefs, QPoint(0, 0)), widget.size())

    hint, below = box(prefs.covers_hint), box(prefs.convert_check)
    assert hint.height() >= 2 * QFontMetrics(prefs.covers_hint.font()).height() - 2      # both lines of it
    assert hint.bottom() < below.top()                                                   # nothing drawn over it
    assert box(prefs.covers_check).bottom() < hint.top()
    assert prefs.rect().contains(hint) and prefs.rect().contains(box(prefs.close_button))
    prefs.close()
