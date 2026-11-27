import io
import zipfile

import pytest
from PySide6.QtCore import Qt

from jewelcase.core import adb as adbmod
from jewelcase.core.session import open_session
from jewelcase.ui import dialogs, models
from jewelcase.ui.dialogs import ReviewDialog
from jewelcase.ui.views import SummaryView

from uihelpers import make_window, wait_until

CARD = "device/storage/1A2B-3C4D"


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.controller.stop_job("cancel")
    wait_until(lambda: not w.controller.job_running, 5)
    w.close()


def keys(win, predicate, n):
    return [r.key for r in win.controller.rows if predicate(r)][:n]


def test_review_dialog_lists_plan(win):
    c = win.controller
    c.set_want(keys(win, lambda r: r.on_device and r.in_library, 2), False)
    c.set_want(keys(win, lambda r: r.state == "library_only", 1), True)
    c.set_want(keys(win, lambda r: r.state == "device_only", 1), False)
    dlg = ReviewDialog(c.plan, win)
    assert dlg.section_count("add") == 1 and dlg.section_count("remove") == 2
    assert dlg.section_count("skipped") == 1
    assert "free" in dlg.space_label.text() and "→" in dlg.space_label.text()
    assert dlg.sync_button.isEnabled()
    plan = dlg.accepted_plan()
    assert len(plan.removes) == 2 and plan.pulls == []


def test_review_copy_first_option(win):
    c = win.controller
    only = keys(win, lambda r: r.state == "device_only", 1)
    c.set_want(only, False)
    dlg = ReviewDialog(c.plan, win)
    assert not dlg.sync_button.isEnabled()           # nothing to do unless the copy is chosen
    dlg.set_copy_first(only[0], True)
    assert dlg.sync_button.isEnabled()
    plan = dlg.accepted_plan()
    assert [i.row.key for i in plan.pulls] == only and [i.row.key for i in plan.removes] == only


def test_sync_runs_and_table_updates(win, tmp_path):
    c = win.controller
    gone = keys(win, lambda r: r.on_device and r.in_library and r.system == "gba", 2)
    new = keys(win, lambda r: r.state == "library_only" and r.system == "snes", 1)
    c.set_want(gone, False)
    c.set_want(new, True)
    assert win.sync_button.isEnabled() and win.sync_button.text() == "Sync…"
    c.start_sync(c.plan)
    assert c.job_running and win.sync_button.text() == "Pause"
    i = next(n for n, r in enumerate(c.rows) if r.on_device)
    assert not (win.games_model.flags(win.games_model.index(i, models.COL_CHECK)) & Qt.ItemIsUserCheckable)
    assert wait_until(lambda: not c.job_running and "In sync" in win.pending_label.text(), 20)
    state = {r.key: r.state for r in c.rows}
    assert all(state[k] == "library_only" for k in gone) and state[new[0]] == "on_device"
    assert win.sync_button.text() == "Sync…" and not win.sync_button.isEnabled()
    assert "Last sync" in win.capsule.sub.text()
    assert c.last_result["state"] == "done" and c.last_result["failed"] == []


def test_sync_disabled_when_plan_does_not_fit(win):
    c = win.controller
    c.session.demo_device.storage_total = 520 * 1024 * 1024      # barely more than what is on it
    c.refresh_blocking()
    c.set_want(keys(win, lambda r: r.state == "library_only" and r.system == "ps2", 2), True)
    assert c.plan.adds and not c.plan.fits
    assert not win.sync_button.isEnabled()
    assert "do not fit on the card" in win.infobar.label.text()


def test_open_job_shows_resume_and_resumes(win):
    c = win.controller
    gone = keys(win, lambda r: r.on_device and r.in_library, 1)
    c.set_want(gone, False)
    job = c.session.runner().start(c.plan)          # an interrupted sync left from an earlier run
    c.refresh_blocking()
    assert win.sync_button.text() == "Resume…" and win.sync_button.isEnabled()
    win.on_sync_clicked()
    win._resume_dialog.accept()
    assert wait_until(lambda: not c.job_running and c.open_job() is None, 20)
    assert {r.key: r.state for r in c.rows}[gone[0]] == "library_only"
    assert c.session.store.last_job(c.session.serial)["id"] == job


def test_pause_keeps_the_job_open(win):
    c = win.controller
    c.set_want(keys(win, lambda r: r.on_device and r.in_library, 3), False)
    c.job_progress.connect(lambda p: c.stop_job("pause"))
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running, 20)
    assert c.open_job() is not None and c.last_result["state"] == "paused"
    assert wait_until(lambda: win.sync_button.text() == "Resume…", 5)
    c.discard_job()
    assert c.open_job() is None and win.sync_button.text() == "Sync…"


def test_summary_fields(win):
    page = win.pages["device:summary"]
    assert isinstance(page, SummaryView)
    text = page.all_text()
    for expected in ("RG Demo", "Android 14", "DEMO0001", "81%", "USB", "on the device", "in the library"):
        assert expected in text, expected
    assert page.card_bar.maximum() > 0 and page.card_bar.value() > 0
    win.select_scope("device:summary")
    assert win.stack.currentWidget() is page


def test_library_folders_are_suggested(qapp, tmp_path):
    lib = tmp_path / "mnt" / "Disk" / "roms"
    for folder in ("psx", "ps2", "gba", "snes"):
        (lib / folder).mkdir(parents=True)
    (tmp_path / "mnt" / "Disk" / "photos").mkdir()
    assert dialogs.suggest_libraries([tmp_path / "mnt"]) == [lib]


def test_unauthorized_message(win, monkeypatch):
    s = win.controller.session
    s.demo_device.online = False

    def connect():
        s.device = None
        s.problem = "Tap Allow USB debugging on the device"
        return False
    monkeypatch.setattr(s, "connect", connect)
    win.controller.refresh_blocking()
    assert win.capsule.title.text() == "No device"
    assert win.capsule.sub.text() == "Tap Allow USB debugging on the device"


def test_session_reports_unauthorized_and_missing_adb(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("PATH", "/nonexistent")
    s = open_session()
    assert s.connect() is False and s.problem == "adb not found"
    monkeypatch.setattr(adbmod, "find_adb", lambda *a, **k: "/bin/adb")
    monkeypatch.setattr(adbmod, "list_targets",
                        lambda exe, **k: adbmod.parse_targets("List of devices attached\nABC\tunauthorized usb:1-2\n"))
    assert s.connect() is False and s.problem == "Tap Allow USB debugging on the device"


def test_download_platform_tools(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("platform-tools/adb", "#!/bin/sh\necho fake adb\n")
        z.writestr("platform-tools/NOTICE.txt", "notice")
    seen = []

    def fetch(url):
        seen.append(url)
        return io.BytesIO(buf.getvalue())
    exe = adbmod.download_platform_tools(tmp_path / "data", fetch=fetch)
    assert exe == str(tmp_path / "data" / "platform-tools" / "adb")
    assert seen == [adbmod.PLATFORM_TOOLS_URL] and seen[0].startswith("https://dl.google.com/")
    assert adbmod.find_adb(None, tmp_path / "data") == exe or adbmod.find_adb(None, tmp_path / "data")


# ---------------------------------------------------------------- converting archives
import shutil                                               # noqa: E402

needs_7z = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")
SAGA = ("ps2", "Northwind Saga (USA)")


@pytest.fixture
def no_chdman(monkeypatch):
    from jewelcase.core import convert
    monkeypatch.setattr(convert, "_chdman", lambda: None)


@needs_7z
def test_review_says_which_additions_are_converted_first(win, no_chdman):
    c = win.controller
    c.set_want([SAGA], True)
    c.set_want(keys(win, lambda r: r.state == "library_only" and r.system == "snes", 1), True)
    dlg = ReviewDialog(c.plan, win)
    sec = dlg._sections["add"]
    notes = {sec.child(i).text(0): sec.child(i).text(3) for i in range(sec.childCount())}
    assert notes == {"Northwind Saga": "Converted from its archive first; the library keeps the converted game",
                     "Star Vixen": "its cover is copied to ES-DE if it has none"}   # it has a cover here
    sizes = {sec.child(i).text(0): sec.child(i).text(2) for i in range(sec.childCount())}
    assert sizes["Northwind Saga"] == "8.0 MB"              # what it takes on the handheld, not the archive's size


def test_review_gives_the_reason_an_archive_cannot_be_converted(win, monkeypatch):
    from jewelcase.core import library as libmod
    c = win.controller
    monkeypatch.setattr(libmod, "_seven_zip", lambda: (_ for _ in ()).throw(libmod.ToolMissing("7z is not installed")))
    from jewelcase.core import convert
    convert._listings.clear()
    c.set_want([SAGA], True)
    dlg = ReviewDialog(c.plan, win)
    sec = dlg._sections["skipped"]
    assert (sec.child(0).text(0), sec.child(0).text(3)) == ("Northwind Saga", "7z is not installed.")
    assert sec.child(0).flags() & Qt.ItemIsUserCheckable == Qt.NoItemFlags


def test_header_says_what_a_conversion_is_doing(win):
    from jewelcase.core.sync import Progress
    win.controller.job_progress.emit(Progress(1, 0, 3, "convert", "Northwind Saga (USA)", 10, 100, "running",
                                              "Compressing"))
    assert win.capsule.sub.text() == "Converting Northwind Saga (USA)  ·  Compressing  ·  step 1 of 3"
    win.controller.job_progress.emit(Progress(1, 1, 3, "push", "Northwind Saga (USA).iso", 60, 100, "running"))
    assert win.capsule.sub.text() == "Copying Northwind Saga (USA).iso  ·  step 2 of 3"
    win._on_finished("done")


@needs_7z
def test_a_sync_from_the_window_converts_and_copies(win, tmp_path, no_chdman):
    c = win.controller
    c.set_want([SAGA], True)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running, 30)
    made = tmp_path / "demo" / CARD / "ps2" / "Northwind Saga (USA).iso"
    assert made.exists() and (tmp_path / "demo/library/ps2/Northwind Saga (USA).iso").exists()
    row = next(r for r in c.rows if r.key == SAGA)
    assert row.state == "on_device" and row.want
