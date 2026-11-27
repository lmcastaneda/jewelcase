"""The window with a card or folder as the device."""
import pytest
from PySide6.QtWidgets import QApplication

from jewelcase.core.config import Config
from jewelcase.core.db import Store
from jewelcase.core.library import Library
from jewelcase.core.session import Session
from jewelcase.ui.controller import Controller
from jewelcase.ui.window import MainWindow

from uihelpers import make_window, wait_until

def write_tree(root, tree):
    for folder, files in tree.items():
        (root / folder).mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (root / folder / name).write_bytes(body)
    return root


GOW, CH = "God of War II (USA)", "Champions (USA)"


def card_window(tmp_path, card):
    lib = write_tree(tmp_path / "library", {"ps2": {GOW + ".chd": b"gow2" * 1000, CH + ".chd": b"ch" * 800}})
    cfg = Config(library=lib, inbox=tmp_path / "inbox", card=card)
    store = Store(":memory:")
    session = Session(cfg, store, Library(lib, store), tmp_path / "data", adb_enabled=False)
    controller = Controller(session, poll=False)
    controller.refresh_blocking()
    window = MainWindow(controller)
    window.resize(1180, 760)
    window.show()
    QApplication.processEvents()
    return window


@pytest.fixture
def card(tmp_path):
    return write_tree(tmp_path / "SDCARD", {"roms/ps2": {GOW + ".chd": b"gow2" * 1000}})


@pytest.fixture
def win(qapp, tmp_path, card):
    w = card_window(tmp_path, card)
    yield w
    w.close()


def entries(win):
    return [i.text(0) for i in win._items()]


def test_a_card_is_shown_as_a_card_in_the_header(win):
    c = win.controller
    assert c.connected and c.session.is_card
    assert win.capsule.title.text() == "SDCARD · Card"
    assert win.capsule.sub.text() == "Card or folder connected"


def test_a_card_has_no_emulators_and_saves_pages_in_the_sidebar(win):
    assert "SDCARD" in entries(win) and "Emulators" not in entries(win) and "Saves" not in entries(win)


def test_the_device_page_describes_the_card(win):
    page = win.pages["device:summary"]
    f = page.fields
    assert f["Connection"].text() == "Card" and f["Battery"].text() == "No battery"
    assert "Card or folder" in f["Device"].text() and f["Games folder"].text().startswith("/roms")
    assert f["Saves"].text() == "Not available for a card"
    assert page.internal_bar.isHidden() and page.internal_text.isHidden()
    assert not page.card_button.isHidden() and not page.stop_card_button.isHidden()


def test_a_sync_to_a_card_is_offered_and_runs_without_emulator_checks(win, tmp_path, card):
    c = win.controller
    c.set_want([("ps2", CH)], True)
    assert win.sync_button.isEnabled() and "1 game" in win.pending_text()
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running and c.last_result is not None, 20)
    assert c.last_result["state"] == "done" and c.last_result["added"] == 1
    assert (card / "roms" / "ps2" / (CH + ".chd")).read_bytes() == b"ch" * 800


def test_stopping_the_card_goes_back_to_no_device(win, card):
    c = win.controller
    c.use_card(None)
    assert wait_until(lambda: not c.connected and c.session.cfg.card is None, 10)
    page = win.pages["device:summary"]
    assert wait_until(page.stop_card_button.isHidden, 5)
    assert not page.card_button.isHidden()


def test_a_card_can_be_chosen_when_no_handheld_is_connected(qapp, tmp_path, card):
    w = card_window(tmp_path, None)
    try:
        c = w.controller
        page = w.pages["device:summary"]
        assert not c.connected
        assert [a.title_text for a in page.welcome.actions] == ["Check Again", "Use a Card or Folder…"]
        c.use_card(card)
        assert wait_until(lambda: c.connected, 10)
        assert wait_until(lambda: "Emulators" not in entries(w) and "SDCARD" in entries(w), 5)
        assert c.session.cfg.card == card
    finally:
        w.close()


def test_a_card_that_is_not_mounted_is_not_connected_and_can_be_dropped(qapp, tmp_path):
    w = card_window(tmp_path, tmp_path / "unplugged")
    try:
        c = w.controller
        assert not c.connected and c.problem == ""
        page = w.pages["device:summary"]
        assert [a.title_text for a in page.welcome.actions] == ["Check Again", "Use a Card or Folder…",
                                                                "Stop Using Card"]
    finally:
        w.close()


def test_a_handheld_that_is_connected_hides_the_card_action(qapp, tmp_path):
    w = make_window(tmp_path)
    try:
        page = w.pages["device:summary"]
        assert page.card_button.isHidden()
        assert "Emulators" in entries(w) and "Card" not in w.capsule.title.text()
    finally:
        w.close()


def count_loads(c, monkeypatch):
    loads = []
    real = c._load
    monkeypatch.setattr(c, "_load", lambda: (loads.append(1), real())[1])
    return loads


def poll_until_quiet(c, rounds=4):
    """Poll a few times, letting each look and any refresh it asks for finish."""
    for _ in range(rounds):
        c._poll()
        wait_until(lambda: not c._refreshing and not c._refresh_pending, 10)


def test_the_poll_settles_when_a_handheld_without_rom_folders_is_plugged_in_beside_a_card(qapp, tmp_path, card,
                                                                                          monkeypatch):
    from jewelcase.core import adb
    w = card_window(tmp_path, card)
    try:
        c = w.controller
        s = c.session
        s.adb_enabled = True
        target = adb.AdbTarget("H1", "device", "RG477V", "usb")
        monkeypatch.setattr(adb, "find_adb", lambda *a, **k: "adb")
        monkeypatch.setattr(adb, "list_targets", lambda exe: [target])
        monkeypatch.setattr(type(s), "_adb_candidates", lambda self: [])      # it has no ROM folders: not usable
        c.refresh()
        assert wait_until(lambda: not c._refreshing and not c._refresh_pending, 10)
        loads = count_loads(c, monkeypatch)
        poll_until_quiet(c)
        assert len(loads) <= 1
        before = len(loads)
        poll_until_quiet(c)
        assert len(loads) == before             # nothing changed: nothing is read again
    finally:
        w.close()


def test_the_poll_settles_when_a_ready_card_still_cannot_be_connected(qapp, tmp_path, card, monkeypatch):
    from jewelcase.core import catalog
    from jewelcase.core.device import DeviceGone
    w = card_window(tmp_path, card)
    try:
        c = w.controller
        c.session.cfg.card = card
        c.session.device = None
        monkeypatch.setattr(catalog, "detect_profile", lambda dev: (_ for _ in ()).throw(DeviceGone("x")))
        c.session.store.set_meta(c.session.serial, "profile", None)
        c.refresh()
        assert wait_until(lambda: not c._refreshing and not c._refresh_pending, 10)
        assert not c.connected
        loads = count_loads(c, monkeypatch)
        poll_until_quiet(c)
        assert len(loads) <= 1
        before = len(loads)
        poll_until_quiet(c)
        assert len(loads) == before
    finally:
        w.close()


def test_the_bios_page_does_not_break_with_a_card_as_the_device(win):
    page = win.pages["device:bios"]
    page.load()
    assert wait_until(lambda: not page.busy, 10)
    assert page.infobar.isHidden() or "could not be read" not in page.infobar.text()


def test_an_empty_card_says_what_to_do_on_the_device_page(qapp, tmp_path):
    empty = tmp_path / "EMPTY"
    empty.mkdir()
    w = card_window(tmp_path, empty)
    try:
        page = w.pages["device:summary"]
        page.refresh()
        assert page.stack.currentWidget() is page.welcome
        assert page.welcome.subtitle.text() == "The card is empty. Make a folder named ROMs on it to use it."
        assert w.controller.problem == "The card is empty. Make a folder named ROMs on it to use it."
    finally:
        w.close()
