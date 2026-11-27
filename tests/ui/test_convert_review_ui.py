"""Review findings in the window: importing and syncing exclude each other, closing during a conversion,
what Undo says."""
import dataclasses
import threading
import time

import pytest

from jewelcase.core.inbox import ImportRecord, import_item
from jewelcase.core.sync import job_lock

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    wait_until(lambda: not w.controller.inbox_busy, 5)
    w.controller.finish_in_background = False
    w.close()


def told(c):
    out = []
    c.toast.connect(lambda text, action, callback: out.append(text))
    return out


# ---------------------------------------------------------------- 2: import and sync exclude each other
def test_the_import_buttons_say_why_they_are_off_while_a_sync_runs(win):
    page, c = win.pages["inbox"], win.controller
    assert page.import_all.isEnabled()
    c.job_running = True
    page.refresh()
    assert not page.import_all.isEnabled() and not page.import_selected.isEnabled()
    assert "sync" in page.import_all.toolTip().lower()
    c.job_running = False
    page.refresh()
    assert page.import_all.isEnabled() and page.import_all.toolTip() == ""


def test_an_import_that_would_convert_is_refused_while_a_sync_runs(win, tmp_path):
    page, c = win.pages["inbox"], win.controller
    said = told(c)
    c.job_running = True
    page.import_ready()
    assert not page.import_all.isEnabled()
    c.job_running = False
    assert said == ["Wait for the sync to finish before importing"] and not c.inbox_busy
    assert (tmp_path / "demo" / "inbox" / "Violet Horizon (USA).gba").exists()
    page.refresh()
    assert page.import_all.isEnabled()


def test_an_import_is_refused_when_another_window_holds_the_lock(win, tmp_path):
    page, c = win.pages["inbox"], win.controller
    with job_lock(c.session.data_dir):
        page.import_ready()
        assert wait_until(lambda: not c.inbox_busy, 10)
    assert "Another Jewelcase window or command" in page.status.text()
    assert (tmp_path / "demo" / "inbox" / "Violet Horizon (USA).gba").exists()


def test_a_sync_asked_for_during_an_import_is_refused_with_a_message(win):
    c = win.controller
    said = told(c)
    c.inbox_busy = True
    c.start_sync(c.plan)
    c.inbox_busy = False
    assert said == ["Wait for the import or conversion to finish before syncing"] and not c.job_running


def test_the_sync_button_is_off_with_a_reason_while_an_import_runs(win):
    c = win.controller
    win._update_sync_button()
    c.inbox_busy = True
    assert not win.sync_button.isEnabled() and "import" in win.sync_button.toolTip().lower()
    c.inbox_busy = False
    assert win.sync_button.toolTip() == ""


# ---------------------------------------------------------------- 4: closing while it runs
def test_closing_the_window_during_an_import_hides_it_and_quits_when_it_is_done(win):
    c = win.controller
    quits = []
    c.quit_requested.connect(lambda: quits.append(1))
    c.undo_imports([])                                  # busy for a moment, like any import
    assert c.inbox_busy
    win.close()
    assert not win.isVisible() and c.finish_in_background and quits == []
    assert wait_until(lambda: quits, 10)
    assert not c.finish_in_background and not c.inbox_busy


def test_starting_jewelcase_again_meanwhile_brings_the_window_back(win):
    c = win.controller
    quits = []
    c.quit_requested.connect(lambda: quits.append(1))
    c.inbox_busy = True
    win.close()
    assert not win.isVisible()
    win.reopen()
    c.inbox_busy = False
    c._after_inbox()
    assert win.isVisible() and quits == [] and not c.finish_in_background


def test_closing_with_nothing_running_still_quits_at_once(win):
    quits = []
    win.controller.quit_requested.connect(lambda: quits.append(1))
    win.close()
    assert quits == [1]


def test_shutdown_waits_for_an_import_to_finish(win):
    c = win.controller
    c.inbox_busy = True
    threading.Timer(0.3, lambda: setattr(c, "inbox_busy", False)).start()
    start = time.time()
    assert c.shutdown(0.05) is True and time.time() - start >= 0.25


# ---------------------------------------------------------------- 7: what Undo says
def test_undo_says_the_archive_went_back_when_only_a_changed_converted_file_stayed(win, tmp_path):
    c = win.controller
    s = c.session
    box = tmp_path / "demo" / "inbox"
    item = next(i for i in c.inbox.scan() if i.path.name == "Violet Horizon (USA).gba")
    record = import_item(item, "gba", s.library, s.store, None)
    extra = s.library.root / "gba" / "Violet Horizon (USA).extra"
    extra.write_bytes(b"made")
    st = extra.stat()
    record = dataclasses.replace(record, made=(("Violet Horizon (USA).extra", st.st_size, st.st_mtime_ns),))
    extra.write_bytes(b"made, then edited")
    said = told(c)
    c.undo_imports([record])
    assert wait_until(lambda: not c.inbox_busy and said, 10)
    assert (box / "Violet Horizon (USA).gba").exists() and extra.exists()
    assert said == ["Violet Horizon (USA) was moved back. Violet Horizon (USA).extra was left in the library "
                    "because it has changed"]
