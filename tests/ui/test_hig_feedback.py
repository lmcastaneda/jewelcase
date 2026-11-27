"""Feedback the HIG way: toasts for what just happened (with Undo), infobars for what lasts."""
import pytest

from jewelcase.core import sync as syncmod

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.controller.stop_job("cancel")
    wait_until(lambda: not w.controller.job_running and not w.controller.inbox_busy
               and not w.controller.emu_busy, 5)
    w.close()


def keys(win, n, predicate=lambda r: r.on_device and r.in_library):
    return [r.key for r in win.controller.rows if predicate(r)][:n]


def select(win, wanted):
    win.table.clearSelection()
    win._reselect(wanted)


# ---------------------------------------------------------------- toasts and undo
def test_bulk_untick_toasts_with_undo(win):
    gone = keys(win, 3)
    select(win, gone)
    win.set_selection_wanted(False)
    assert win.toast.isVisible() and win.toast.label.text() == "3 games unticked"
    assert win.toast.action_button.text() == "Undo"
    assert len(win.controller.plan.removes) == 3
    win.toast.action_button.click()
    assert win.controller.plan.empty and not win.toast.isVisible()


def test_undo_restores_each_game_to_what_it_was(win):
    c = win.controller
    on, off = keys(win, 1)[0], keys(win, 1, lambda r: r.state == "library_only")[0]
    c.set_want([off], True)                               # one game already queued to add
    select(win, [on, off])
    win.set_selection_wanted(False)
    assert win.toast.label.text() == "2 games unticked"
    win.toast.action_button.click()
    want = {r.key: r.want for r in c.rows}
    assert want[on] is True and want[off] is True


def test_single_tick_does_not_toast(win):
    select(win, keys(win, 1))
    win.set_selection_wanted(False)
    assert not win.toast.isVisible()


def test_import_toast_undo_puts_files_back(win, tmp_path):
    page = win.pages["inbox"]
    c = win.controller
    page.import_ready()
    assert wait_until(lambda: not c.inbox_busy and win.toast.isVisible(), 20)
    assert win.toast.label.text() == "2 games were imported" and win.toast.action_button.text() == "Undo"
    inbox, lib = tmp_path / "demo" / "inbox", tmp_path / "demo" / "library"
    assert not (inbox / "Violet Horizon (USA).gba").exists()
    win.toast.action_button.click()
    assert wait_until(lambda: not c.inbox_busy and (inbox / "Violet Horizon (USA).gba").exists(), 20)
    assert (inbox / "Rally Kings (USA).rvz").exists()
    assert not (lib / "gba" / "Violet Horizon (USA).gba").exists()
    assert wait_until(lambda: c.plan.empty and page.item_count() == 3, 10)


def test_undo_disabled_while_a_sync_runs(win, monkeypatch):
    import time
    from jewelcase.core.fakedevice import FakeDevice
    c = win.controller
    gone = keys(win, 3)
    select(win, gone)
    win.set_selection_wanted(False)
    real = FakeDevice.md5
    monkeypatch.setattr(FakeDevice, "md5", lambda self, path: (time.sleep(0.1), real(self, path))[1])
    c.start_sync(c.plan)
    assert c.job_running
    assert not win.toast.isVisible() or not win.toast.action_button.isEnabled()
    c.restore_wants({k: True for k in gone})               # the Undo callback, arriving late
    assert all(not r.want for r in c.rows if r.key in gone)


def test_discard_toast_undo_reopens_the_job(win):
    c = win.controller
    c.set_want(keys(win, 1), False)
    job = c.session.runner().start(c.plan)
    c.refresh_blocking()
    c.discard_job()
    assert c.open_job() is None
    assert win.toast.label.text() == "Unfinished sync was discarded"
    win.toast.action_button.click()
    assert c.open_job() == job


def test_emulator_disable_toast_undo(win):
    c = win.controller
    win.select_scope("device:emulators")
    page = win.pages["device:emulators"]
    assert wait_until(lambda: page.row_count() >= 7 and not c.emu_busy, 10)
    page.select("org.dolphinemu.dolphinemu")
    page.do_set_enabled(False)
    assert wait_until(lambda: not c.emu_busy and win.toast.isVisible(), 10)
    assert win.toast.label.text() == "Dolphin was disabled"
    win.toast.action_button.click()
    assert wait_until(lambda: not c.emu_busy and page.state_of("org.dolphinemu.dolphinemu").startswith("Enabled"), 10)


def test_sync_finished_toast(win):
    c = win.controller
    c.set_want(keys(win, 2, lambda r: r.on_device and r.in_library and r.system == "gba"), False)
    c.set_want(keys(win, 1, lambda r: r.state == "library_only" and r.system == "snes"), True)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running and win.toast.isVisible(), 20)
    assert win.toast.label.text() == "Sync finished: 1 added, 2 removed"


# ---------------------------------------------------------------- infobars
def test_no_infobar_when_all_is_well(win):
    assert win.infobar.isHidden()


def test_unfinished_sync_infobar_opens_the_resume_sheet(win):
    c = win.controller
    c.set_want(keys(win, 1), False)
    c.session.runner().start(c.plan)
    c.refresh_blocking()
    assert win.infobar.kind == "warning"
    assert win.infobar.label.text() == "A sync was interrupted before it finished."
    assert win.infobar.action_button.text() == "Review and Resume…"
    win.infobar.action_button.click()
    assert win._resume_dialog.isVisible()
    win._resume_dialog.reject()


def test_library_offline_infobar(win, tmp_path):
    c = win.controller
    c.session.library.root = tmp_path / "unmounted"
    c.reload()
    assert win.infobar.kind == "warning" and "library folder is not available" in win.infobar.label.text()
    assert win.infobar.action_button.text() == "Check Again"
    assert len(c.rows) >= 35                                # still showing what was last indexed


def test_does_not_fit_infobar(win):
    c = win.controller
    c.session.demo_device.storage_total = 520 * 1024 * 1024
    c.refresh_blocking()
    c.set_want(keys(win, 2, lambda r: r.state == "library_only" and r.system == "ps2"), True)
    assert win.infobar.kind == "warning" and "do not fit on the card" in win.infobar.label.text()
    assert "does not fit" not in win.pending_label.text()
    assert not win.sync_button.isEnabled()


def test_most_severe_infobar_wins(win):
    c = win.controller
    c.set_want(keys(win, 1), False)
    c.session.runner().start(c.plan)                        # a warning: unfinished sync
    c.session.demo_device.online = False
    c.session.problem = "adb not found"                     # and an error
    c.reload()
    assert win.infobar.kind == "error" and "adb" in win.infobar.label.text()
    assert win.infobar.action_button.text() == "Download adb"
    assert [k for k, *_ in win.conditions()][:2] == ["error", "warning"]


def test_failed_steps_infobar_shows_details(win):
    c = win.controller
    c.last_result = {"state": "done", "job": 1, "added": 0, "removed": 0,
                     "failed": [{"label": "God of War II (USA).chd", "kind": "verify", "message": "contents differ"}]}
    c.reload()
    assert win.infobar.kind == "warning" and win.infobar.label.text() == "1 step of the last sync did not work."
    assert win.infobar.action_button.text() == "Show Details…"
    win.infobar.action_button.click()
    assert "God of War II (USA).chd" in win._details_dialog.secondary.text()
    assert "contents differ" in win._details_dialog.secondary.text()
    win._details_dialog.reject()
    assert win.infobar.isHidden()                           # seen once, then it goes away


def test_unexpected_error_is_an_infobar(win, monkeypatch):
    c = win.controller
    c.set_want(keys(win, 1), False)

    def boom(self, job, resumed):
        raise RuntimeError("boom")
    monkeypatch.setattr(syncmod.Runner, "_run", boom)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running, 10)
    wait_until(lambda: False, 0.5)
    assert win.infobar.kind == "error" and "stopped unexpectedly" in win.infobar.label.text()
    assert "boom" in win.infobar.label.text()


def test_sync_finished_while_a_game_runs_says_es_de_was_not_restarted(win):
    c = win.controller
    c.session.demo_device.foreground = "org.ppsspp.ppsspp"
    c.set_want(keys(win, 1, lambda r: r.state == "library_only" and r.system == "snes"), True)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running and win.toast.isVisible(), 20)
    assert win.toast.label.text() == \
        "Sync finished: 1 added, 0 removed. ES-DE shows the changes the next time it starts"
    assert ("stop", "org.es_de.frontend") not in c.session.demo_device.calls
