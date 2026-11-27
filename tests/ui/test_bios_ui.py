"""The BIOS page: loaded off the UI thread, shows verified, unrecognised and missing systems."""
import pytest

from jewelcase.ui.biosview import BiosView

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.controller.shutdown(5)
    w.close()


def open_page(win) -> BiosView:
    win.select_scope("device:bios")
    page = win.pages["device:bios"]
    assert wait_until(lambda: not page.busy and page.results)
    return page


def sidebar_names(win):
    dev = next(win.sidebar.topLevelItem(i) for i in range(win.sidebar.topLevelItemCount())
               if win.sidebar.topLevelItem(i).text(0) == "Device")
    return [dev.child(i).text(0) for i in range(dev.childCount())]


def test_the_sidebar_has_a_bios_page_after_saves(win):
    names = sidebar_names(win)
    assert names.index("BIOS") == names.index("Saves") + 1


def test_the_page_says_each_emulator_must_be_pointed_at_the_folder(win):
    open_page(win)
    assert "pointed at the folder in its own settings" in win.pages["device:bios"].subtitle.text()


def test_the_page_shows_a_verified_an_unrecognised_and_a_missing_system(win):
    page = open_page(win)
    states = {r[0]: r[2] for r in page.rows() if r[1]}
    assert states["PlayStation"] == "Verified"
    assert states["PlayStation 2"] == "Found, Not Recognised"
    assert states["Dreamcast"] == "Missing"


def test_systems_with_games_on_the_handheld_are_listed_first(win):
    page = open_page(win)
    tops = [r[0] for r in page.rows() if r[1]]
    assert tops.index("Dreamcast") < tops.index("Saturn")      # the demo has Dreamcast games, no Saturn ones


def test_check_again_looks_again_and_shows_a_new_file(win):
    page = open_page(win)
    dev = win.controller.session.device
    p = dev._p("/sdcard/RetroArch/system/dc/dc_boot.bin")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(2 * 1024 * 1024)                 # a Dreamcast-sized dc_boot.bin nobody has listed
    page.check_button.click()
    assert wait_until(lambda: next(r for r in page.rows() if r[0] == "Dreamcast")[2] == "Found, Not Recognised")


def test_without_a_device_the_page_says_to_connect_it(win):
    win.controller.session.device = None
    page = win.pages["device:bios"]
    page.load()
    assert page.results == [] and not page.check_button.isEnabled()
    assert "Connect the handheld" in page.infobar.label.text()


# ---------------------------------------------------------------- once per connection
def count_checks(monkeypatch):
    from jewelcase.core import bios
    calls = []
    real = bios.check
    monkeypatch.setattr(bios, "check", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_the_handheld_is_looked_at_once_per_connection_not_every_time_the_page_is_shown(win, monkeypatch):
    calls = count_checks(monkeypatch)
    page = open_page(win)
    win.select_scope("device:emulators")
    win.select_scope("device:bios")
    win.controller.rows_changed.emit()
    assert wait_until(lambda: not page.busy) and page.results
    assert len(calls) == 1


def test_check_again_looks_once_more(win, monkeypatch):
    calls = count_checks(monkeypatch)
    page = open_page(win)
    page.check_button.click()
    assert wait_until(lambda: len(calls) == 2 and not page.busy)


def test_nothing_is_looked_at_during_a_sync_and_the_last_result_stays_with_a_note(win, monkeypatch):
    calls = count_checks(monkeypatch)
    page = open_page(win)
    win.controller.job_running = True
    win.select_scope("device:emulators")
    win.select_scope("device:bios")
    page.load(force=True)
    assert len(calls) == 1 and page.results and not page.check_button.isEnabled()
    assert "sync is running" in page.infobar.label.text() and "found before" in page.infobar.label.text()
    win.controller.job_running = False


def test_a_system_with_only_some_of_its_files_is_incomplete_and_names_the_rest(win):
    from jewelcase.core import bios
    import hashlib
    boot = b"made-up boot"
    win.controller.session.bios_data = lambda: bios.load({"system": [{"folder": "dreamcast", "need": "needed", "file": [
        {"name": "dc_boot.bin", "size": len(boot), "md5": hashlib.md5(boot).hexdigest(), "role": "boot"},
        {"name": "flash.bin", "size": 9, "md5": "f" * 32, "role": "flash"}]}]})
    p = win.controller.session.device._p("/sdcard/RetroArch/system/dc/dc_boot.bin")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(boot)
    page = open_page(win)
    row = next(r for r in page.rows() if r[0] == "Dreamcast")
    assert row[2] == "Incomplete" and "flash.bin" in row[3]
