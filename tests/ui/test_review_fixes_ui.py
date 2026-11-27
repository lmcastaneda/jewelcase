"""Window-side tests for the findings of the whole-branch review."""
import time

import pytest

from jewelcase.core import sync as syncmod
from jewelcase.core.fakedevice import FakeDevice

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.controller.stop_job("cancel")
    wait_until(lambda: not w.controller.job_running, 5)
    w.close()


def keys(win, n, predicate=lambda r: r.on_device and r.in_library):
    return [r.key for r in win.controller.rows if predicate(r)][:n]


def test_resume_shows_what_is_left_before_doing_anything(win):
    c = win.controller
    gone = keys(win, 1)
    c.set_want(gone, False)
    c.session.runner().start(c.plan)                # left over from an earlier run
    c.refresh_blocking()
    win.on_sync_clicked()
    dlg = win._resume_dialog
    assert not c.job_running                         # nothing happens until the user has seen it
    assert gone[0][1] in dlg.summary_text() and "remove" in dlg.summary_text().lower()
    dlg.accept()
    assert wait_until(lambda: not c.job_running and c.open_job() is None, 20)
    assert {r.key: r.state for r in c.rows}[gone[0]] == "library_only"


def test_resume_sheet_can_discard(win):
    c = win.controller
    c.set_want(keys(win, 1), False)
    c.session.runner().start(c.plan)
    c.refresh_blocking()
    win.on_sync_clicked()
    win._resume_dialog.discard()
    assert c.open_job() is None and not c.job_running
    assert win.sync_button.text() == "Sync…"


def test_unexpected_error_stays_visible(win, monkeypatch):
    c = win.controller
    c.set_want(keys(win, 1), False)

    def boom(self, job, resumed):
        raise RuntimeError("boom")
    monkeypatch.setattr(syncmod.Runner, "_run", boom)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running, 10)
    wait_until(lambda: False, 0.5)                   # let the follow-up refresh land
    text = win.infobar.label.text()
    assert "stopped unexpectedly" in text and "boom" in text


def test_copy_to_library_is_offered_when_the_library_lacks_this_form(win, tmp_path):
    c = win.controller
    key = keys(win, 1, lambda r: r.on_device and r.in_library and r.system == "ps2")[0]
    row = next(r for r in c.rows if r.key == key)
    lib_file = tmp_path / "demo" / "library" / "ps2" / row.dev_unit.head
    lib_file.rename(lib_file.with_suffix(".iso"))
    c.refresh_blocking()
    other = keys(win, 1, lambda r: r.on_device and r.in_library and r.system == "gba")[0]
    assert win.copyable_keys([key, other]) == [key]
    c.set_want([key], False)
    assert [(r.key, why) for r, why in c.plan.skipped] == [(key, "no library copy")]


# ================================================================ review of the HIG branch
import time as _time

from jewelcase.ui import dialogs as _dialogs
from jewelcase.ui import shell as _shell


def _slow(monkeypatch, seconds=0.12):
    real = FakeDevice.md5
    monkeypatch.setattr(FakeDevice, "md5", lambda self, path: (_time.sleep(seconds), real(self, path))[1])


def test_library_folder_cannot_change_during_a_sync(win, monkeypatch, tmp_path):
    c = win.controller
    c.set_want(keys(win, 3), False)
    _slow(monkeypatch)
    before = c.session.library.root
    c.start_sync(c.plan)
    dlg = _dialogs.open_preferences(win)
    assert not dlg.library_button.isEnabled() and not dlg.inbox_button.isEnabled()
    dlg.set_library(tmp_path / "elsewhere")              # even if something calls it directly
    assert c.session.library.root == before and c.session.cfg.library == before
    assert wait_until(lambda: not c.job_running, 30)
    assert wait_until(lambda: dlg.library_button.isEnabled(), 5)


def test_background_sync_pauses_when_a_game_is_being_played(win, monkeypatch):
    notes = []
    monkeypatch.setattr(_shell, "notify", lambda title, body="": notes.append(title) or True)
    monkeypatch.setattr(_shell, "launcher_progress", lambda fraction: None)
    c = win.controller
    c.desktop = True
    c.session.emulator_packages = {"org.ppsspp.ppsspp"}
    c.session.demo_device.foreground = "org.ppsspp.ppsspp"
    gone = keys(win, 1)
    c.set_want(gone, False)
    c.start_sync(c.plan)
    win.close()
    assert wait_until(lambda: not c.job_running, 30)
    assert c.last_result["state"] == "paused" and notes == ["Sync paused"]
    assert {r.key: r.on_device for r in c.rows}[gone[0]] is True        # nothing was removed unseen


def test_resume_offer_needs_the_device(win):
    c = win.controller
    c.set_want(keys(win, 1), False)
    c.session.runner().start(c.plan)
    c.session.demo_device.online = False
    c.refresh_blocking()
    assert "Connect the handheld" in win.infobar.label.text()
    assert win.infobar.action_button.isHidden()
    assert not win.sync_button.isEnabled()


def test_state_is_restored_after_the_first_load(qapp, tmp_path):
    from jewelcase.core.session import open_session
    from jewelcase.ui.controller import Controller
    from jewelcase.ui.window import MainWindow
    c = Controller(open_session(demo_dir=tmp_path / "demo"), poll=False)
    w = MainWindow(c)                                      # as at launch: nothing loaded yet
    ini = tmp_path / "window.ini"
    ini.write_text("[window]\nscope=system:gba\ncollapsed=Systems\nsort_column=3\nsort_order=1\n")
    _shell.WindowState(ini).restore(w)
    c.refresh_blocking()
    try:
        assert w._scope == "system:gba"
        assert w.games_filter.rowCount() == len([r for r in c.rows if r.system == "gba"])
        assert "Systems" in w._collapsed and not w.sidebar.topLevelItem(4).isExpanded()
    finally:
        w.close()


def test_a_refresh_asked_for_during_another_is_not_lost(win, tmp_path):
    c = win.controller
    new_lib = tmp_path / "newlib"
    (new_lib / "gba").mkdir(parents=True)
    (new_lib / "gba" / "Only One (USA).gba").write_bytes(b"rom")
    (new_lib / "snes").mkdir()
    c._refreshing = True                                   # a refresh is in flight
    c.session.set_library(new_lib)
    c.refresh()                                            # asked for now, must not be dropped
    c._refreshing = False
    c._refresh_finished()
    assert wait_until(lambda: any(r.stem == "Only One (USA)" for r in c.rows), 10)


def test_failed_steps_survive_a_restart(win):
    from jewelcase.ui.controller import Controller
    c = win.controller
    store, serial = c.session.store, c.session.serial
    job = store.create_job(serial, [{"kind": "verify", "system": "ps2", "stem": "X (USA)", "name": "X (USA).chd"}])
    store.set_step(job, 0, "failed", "contents differ")
    store.close_job(job, "done")
    again = Controller(c.session, poll=False)              # the app was closed and opened again
    again.refresh_blocking()
    assert [f["label"] for f in again.last_result["failed"]] == ["X (USA).chd"]
    again.acknowledge_result()
    third = Controller(c.session, poll=False)
    third.refresh_blocking()
    assert not (third.last_result or {}).get("failed") or third.last_result.get("seen")


def test_notification_counts_one_step_properly(win, monkeypatch):
    said = []
    monkeypatch.setattr(_shell, "notify", lambda title, body="": said.append(body) or True)
    c = win.controller
    c.last_result = {"state": "done", "added": 1, "removed": 0, "failed": [{"label": "x", "message": "y"}]}
    c._notify_result("done")
    assert said == ["1 added, 0 removed, 1 step did not work"]


def test_undo_of_an_import_says_why_it_could_not(win, tmp_path):
    c = win.controller
    page = win.pages["inbox"]
    page.import_ready()
    assert wait_until(lambda: not c.inbox_busy and win.toast.isVisible(), 20)
    (tmp_path / "demo" / "library" / "gba" / "Violet Horizon (USA).gba").write_bytes(b"changed since")
    win.toast.action_button.click()
    assert wait_until(lambda: not c.inbox_busy and "changed since" in win.toast.label.text(), 20)


def test_adding_to_the_menu_says_so(win, monkeypatch, tmp_path):
    from jewelcase.core import integrate
    app = tmp_path / "Jewelcase.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setattr(integrate, "appimage_path", lambda: app)
    monkeypatch.setattr(integrate, "install_menu_entry", lambda exe, home=None: tmp_path / "jewelcase.desktop")
    win.add_to_menu()
    assert win.toast.label.text() == "Jewelcase was added to the applications menu"
