"""Review fixes in the window: other handhelds' sets are read off the UI thread and only when chosen, the
words used for settings, undo, the remove dialog."""
import threading

import pytest

from jewelcase.core import saves
from jewelcase.core.db import RunRow
from jewelcase.core.savebackup import SaveBackup
from jewelcase.ui import dialogs

from test_saves_ui import CC, KEY, backed_up, page_of, win  # noqa: F401
from test_savesets import CFG_KEY, OLD, RA_CFG, make_set
from uihelpers import wait_until


def test_the_dialog_reads_another_handheld_only_when_it_is_chosen(win):
    old = object()
    asked = []
    sets = [(None, "RG Demo · DEMO0001 (this handheld)", 5, [RunRow("20261126-140500", True, 3, {})]),
            (old, "RG477V · OLD00001", None, None)]
    dlg = dialogs.build_restore_all_dialog(win, "", 5, [], "save", sets, asked.append)
    assert asked == [] and dlg.primary.text() == "Restore all 5 saves?"
    dlg.set_box.setCurrentIndex(1)
    go = dlg.buttons_in_order()[1]
    assert asked == [old] and dlg.primary.text() == "Reading the backups of RG477V · OLD00001…" and not go.isEnabled()
    dlg.set_box.setCurrentIndex(0)
    dlg.set_box.setCurrentIndex(1)
    assert asked == [old]                                   # asked once
    dlg.set_ready(old, 2, [RunRow("20261001-090000", True, 2, {})], "item")
    assert dlg.primary.text() == "Restore all 2 items?" and go.isEnabled() and dlg.from_box.count() == 2


def test_a_set_that_cannot_be_read_is_said_in_the_dialog(win):
    old = object()
    dlg = dialogs.build_restore_all_dialog(win, "", 5, [], "save", [(None, "This handheld", 5, []),
                                                                    (old, "RG477V · OLD00001", None, None)])
    dlg.set_box.setCurrentIndex(1)
    dlg.set_failed(old, "The backups of RG477V · OLD00001 could not be read.")
    assert dlg.primary.text() == "The backups of RG477V · OLD00001 could not be read."
    assert not dlg.buttons_in_order()[1].isEnabled()


def test_opening_restore_all_indexes_no_other_set_on_the_ui_thread(win, tmp_path, monkeypatch):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    make_set(tmp_path / "demo", {CC: b"old"}, registered=False)
    page = page_of(win)
    page.refresh()
    threads, seen = [], []
    real = SaveBackup.rebuild_index
    monkeypatch.setattr(SaveBackup, "rebuild_index",
                        lambda self: threads.append(threading.current_thread()) or real(self))

    def confirm(parent, name, count, runs, noun, sets, reader):
        seen.append(sets)
        ready = []
        reader(sets[1][0], lambda *a: ready.append(a), lambda text: ready.append(text))
        assert wait_until(lambda: bool(ready))
        seen.append(ready[0])
        return False, None, None
    monkeypatch.setattr(dialogs, "confirm_restore_all", confirm)
    page._restore_all_clicked()
    sets, ready = seen
    assert sets[1][2] is None                               # shown as "reading…" until it is ready
    assert ready[0] == 1 and ready[2] == "save"
    assert threads and all(t is not threading.main_thread() for t in threads)


def test_the_toast_says_settings_not_save(win):
    c = win.controller
    path = c.session.device._p(RA_CFG)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x = 1")
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    path.write_bytes(b"y = 2")
    c.restore_saves([CFG_KEY])
    assert wait_until(lambda: win.toast.label.text() == "Settings restored for retroarch.cfg")
    assert wait_until(lambda: not c.saves_busy)
    c.restore_saves([CFG_KEY, KEY])
    assert wait_until(lambda: win.toast.label.text() == "2 of 2 items restored")


def test_no_undo_for_a_file_the_handheld_did_not_have(win, tmp_path):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    c.session.device._p(CC).unlink()
    make_set(tmp_path / "demo", {CC: b"from the old handheld"})
    c.restore_saves([KEY], None, saves.other_set(c.session, OLD))
    assert wait_until(lambda: win.toast.label.text().startswith("Save restored for"))
    assert not win.toast.action_button.isVisible()


def test_remove_dialog_mentions_unreadable_settings(win):
    dlg, _ = dialogs.build_remove_dialog(win, "RetroArch", "", "2 settings files")
    assert "Jewelcase could not read 2 settings files of RetroArch, so they are not backed up." in dlg.secondary.text()
    dlg, _ = dialogs.build_remove_dialog(win, "RetroArch", "", "1 settings file")
    assert "1 settings file of RetroArch, so it is not backed up." in dlg.secondary.text()
    assert [b.text() for b in dlg.buttons_in_order()] == ["Cancel", "Remove"]
