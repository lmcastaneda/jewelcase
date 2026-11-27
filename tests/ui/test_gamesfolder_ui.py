"""Change… beside the games folder: the dialog that corrects what detection picked."""
import pytest
from PySide6.QtWidgets import QDialog

from jewelcase.ui import hig

from uihelpers import make_window, wait_until

CARD = "/storage/1A2B-3C4D"
SD = "/sdcard/ROMs"


@pytest.fixture
def win(qapp, tmp_path):
    folder = tmp_path / "demo" / "device" / "sdcard" / "ROMs" / "gba"        # a second place with a system in it
    folder.mkdir(parents=True)
    (folder / "Only Here (USA).zip").write_bytes(b"zip")
    w = make_window(tmp_path)
    w.toasts = []
    w.controller.toast.connect(lambda text, label, action: w.toasts.append((text, label, action)))
    yield w
    w.close()


def open_dialog(win):
    page = win.pages["device:summary"]
    page.change_button.click()
    dlg = win._folder_dialog
    assert wait_until(lambda: dlg.list.isEnabled() and dlg.list.count() > 0, 10)
    return dlg


def folder_text(win):
    return win.pages["device:summary"].fields["Games folder"].text()


def settle(win):
    c = win.controller
    wait_until(lambda: not c._refreshing and not c._refresh_pending, 20)


def test_the_games_folder_row_says_how_it_was_chosen_and_offers_change(win):
    page = win.pages["device:summary"]
    assert folder_text(win) == f"{CARD} · detected"
    assert page.change_button.text() == "Change…" and page.change_button.isEnabled()


def test_the_dialog_follows_the_hig_and_lists_the_candidates_with_the_current_one_marked(win):
    dlg = open_dialog(win)
    assert dlg.windowTitle().strip() == ""
    assert dlg.primary.text() == "Choose the games folder"
    assert [b.text() for b in dlg.buttons_in_order()] == ["Detect Again", "Cancel", "Use Folder"]
    assert dlg.use_button.objectName() == "suggested"
    rows = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert rows[0].startswith(CARD) and "(in use)" in rows[0] and "system folders" in rows[0]
    assert rows[1].startswith(SD) and "1 system folder," in rows[1] and "1 game file" in rows[1]
    assert "(in use)" not in rows[1]
    assert not dlg.use_button.isEnabled()                   # nothing chosen yet
    assert not dlg.detect_button.isEnabled()                # the folder in use was detected
    assert all(hig.is_title_case(b.text()) for b in dlg.buttons_in_order())
    dlg.reject()


def test_picking_a_candidate_and_using_it_changes_the_folder_with_undo(win):
    dlg = open_dialog(win)
    dlg.list.setCurrentRow(1)
    assert dlg.chosen() == SD and dlg.use_button.isEnabled()
    dlg.use_button.click()
    assert wait_until(lambda: dlg.result() == QDialog.Accepted, 20)
    assert win.controller.session.profile.rom_root == SD
    assert wait_until(lambda: win.toasts, 5)
    text, label, undo = win.toasts[-1]
    assert text == f"The games folder is now {SD}" and label == "Undo"
    assert wait_until(lambda: folder_text(win) == f"{SD} · set by you", 20)
    settle(win)
    assert {r.system for r in win.controller.rows if r.on_device} == {"gba"}
    undo()
    assert wait_until(lambda: win.controller.session.profile.rom_root == CARD, 20)
    assert wait_until(lambda: folder_text(win) == f"{CARD} · detected", 20)


def test_a_typed_path_that_is_wrong_is_refused_in_the_dialog_with_the_reason(win):
    dlg = open_dialog(win)
    dlg.path.setText("/sdcard/Nowhere")
    assert dlg.use_button.isEnabled() and dlg.list.selectedItems() == []
    dlg.use_button.click()
    assert wait_until(dlg.error.isVisible, 10)
    assert dlg.isVisible() and "does not exist" in dlg.error.text()
    dlg.path.setText("/sdcard/ES-DE")
    assert not dlg.error.isVisible()                        # the old complaint goes when the text changes
    dlg.use_button.click()
    assert wait_until(dlg.error.isVisible, 10)
    assert dlg.isVisible() and "no system folders" in dlg.error.text()
    assert win.controller.session.profile.rom_root == CARD
    dlg.reject()


def test_a_typed_path_that_is_right_is_used(win):
    dlg = open_dialog(win)
    dlg.path.setText(SD + "/")
    dlg.use_button.click()
    assert wait_until(lambda: dlg.result() == QDialog.Accepted, 20)
    assert win.controller.session.profile.rom_root == SD


def test_detect_again_in_the_dialog_goes_back_to_automatic_detection(win):
    win.controller.set_games_folder(SD)
    assert wait_until(lambda: win.controller.session.profile.rom_root == SD, 20)
    settle(win)
    dlg = open_dialog(win)
    assert dlg.detect_button.isEnabled()
    rows = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert "(in use)" in rows[1] and rows[1].startswith(SD)
    dlg.detect_button.click()
    assert wait_until(lambda: dlg.result() == 2, 20)
    assert win.controller.session.profile.rom_root == CARD
    assert wait_until(lambda: win.toasts, 5)
    assert win.toasts[-1][0].startswith("Detected the games folder again")
    assert wait_until(lambda: folder_text(win) == f"{CARD} · detected", 20)


def test_while_a_sync_is_unfinished_the_folder_cannot_be_changed_and_it_says_why(win):
    c = win.controller
    s = c.session
    s.store.create_job(s.serial, [{"kind": "refresh"}])
    c.reload()
    page = win.pages["device:summary"]
    assert not page.change_button.isEnabled()
    assert "A sync has not finished yet" in folder_text(win)
    from jewelcase.core.catalog import FolderError
    with pytest.raises(FolderError, match="sync"):
        c.set_games_folder(SD)
    with pytest.raises(FolderError, match="sync"):
        c.detect_games_folder()
    assert s.profile.rom_root == CARD


def test_the_dialog_can_be_cancelled_without_changing_anything(win):
    dlg = open_dialog(win)
    dlg.list.setCurrentRow(1)
    [b for b in dlg.buttons_in_order() if b.text() == "Cancel"][0].click()
    assert dlg.result() == QDialog.Rejected and win.controller.session.profile.rom_root == CARD


def test_a_typed_path_outside_the_device_is_refused_in_the_dialog_not_with_a_crash(win):
    dlg = open_dialog(win)
    dlg.path.setText("/../x")
    dlg.use_button.click()
    assert wait_until(dlg.error.isVisible, 10)
    assert dlg.isVisible() and dlg.use_button.isEnabled()
    dlg.reject()


def test_changing_the_games_folder_reads_the_device_off_the_ui_thread(win, monkeypatch):
    import threading
    from jewelcase.core import gamesfolder
    main, seen = threading.current_thread(), []
    real = gamesfolder.set_folder
    monkeypatch.setattr(gamesfolder, "set_folder", lambda s, p: (seen.append(threading.current_thread()), real(s, p))[1])
    win.controller.set_games_folder(SD)
    assert wait_until(lambda: win.controller.session.profile.rom_root == SD, 20)
    assert seen and all(t is not main for t in seen)


def test_the_emulators_page_is_worked_out_again_for_the_new_games_folder(win):
    c = win.controller
    calls = []
    real = c.load_emulators
    c.load_emulators = lambda: (calls.append(1), real())[1]
    assert wait_until(lambda: not c.emu_busy, 20)
    c.set_games_folder(SD)
    assert wait_until(lambda: c.session.profile.rom_root == SD, 20)
    assert wait_until(lambda: calls, 20)
