"""Desktop behaviour: finishing in the background, notifications, one instance, remembered state."""
import os
import socket
import subprocess
import sys
import time
import uuid

import pytest
from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer

from jewelcase.core.fakedevice import FakeDevice
from jewelcase.ui import models, shell

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(shell, "notify", lambda title, body="": notes.append((title, body)) or True)
    monkeypatch.setattr(shell, "launcher_progress", lambda fraction: None)
    w = make_window(tmp_path)
    w.controller.desktop = True
    w.notes = notes
    w.quits = []
    w.controller.quit_requested.connect(lambda: w.quits.append(1))
    yield w
    w.controller.stop_job("cancel")
    wait_until(lambda: not w.controller.job_running, 5)
    w.close()


def keys(win, n):
    return [r.key for r in win.controller.rows if r.on_device and r.in_library][:n]


def slow_device(monkeypatch, seconds=0.12):
    real = FakeDevice.md5
    monkeypatch.setattr(FakeDevice, "md5", lambda self, path: (time.sleep(seconds), real(self, path))[1])


def test_notify_without_a_session_bus_is_silent():
    code = ("from jewelcase.ui import shell\n"
            "print(shell.notify('Sync finished', '3 added'))\n"
            "shell.launcher_progress(0.5)\nshell.launcher_progress(None)\nprint('fine')\n")
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/bus", QT_QPA_PLATFORM="offscreen")
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.split() == ["False", "fine"]


def test_single_instance_second_launch_activates_the_first(qapp):
    name = f"jewelcase-test-{uuid.uuid4().hex[:8]}"
    first = shell.SingleInstance(name)
    assert first.already_running() is False
    hits = []
    assert first.listen(lambda: hits.append(1)) is True
    second = shell.SingleInstance(name)
    assert second.already_running() is True
    assert wait_until(lambda: hits == [1], 5)
    first.close()


def test_stale_instance_socket_does_not_block_startup(qapp):
    name = f"jewelcase-test-{uuid.uuid4().hex[:8]}"
    probe = QLocalServer()
    assert probe.listen(name)
    path = probe.fullServerName()
    probe.close()
    left_behind = socket.socket(socket.AF_UNIX)        # what a crash leaves: a socket file nobody answers
    left_behind.bind(path)
    left_behind.close()
    assert os.path.exists(path)
    again = shell.SingleInstance(name)
    assert again.already_running() is False
    assert again.listen(lambda: None) is True
    again.close()


def test_window_state_roundtrip(win, tmp_path, qapp):
    win.resize(1300, 640)
    win.select_scope("smart:never_played")
    win.table.sortByColumn(models.COL_SIZE, Qt.DescendingOrder)
    win.toggle_section("Systems")
    win.table.setColumnWidth(models.COL_SYSTEM, 201)
    store = shell.WindowState(tmp_path / "window.ini")
    store.save(win)
    other = make_window(tmp_path / "second")
    try:
        store.restore(other)
        assert other._scope == "smart:never_played"
        assert other.table.horizontalHeader().sortIndicatorSection() == models.COL_SIZE
        assert other.table.horizontalHeader().sortIndicatorOrder() == Qt.DescendingOrder
        assert "Systems" in other._collapsed and not other.sidebar.topLevelItem(4).isExpanded()
        assert other.table.columnWidth(models.COL_SYSTEM) == 201
        assert (other.width(), other.height()) == (win.width(), win.height())
    finally:
        other.close()


def test_restore_ignores_state_that_no_longer_applies(win, tmp_path):
    ini = tmp_path / "window.ini"
    ini.write_text("[window]\nscope=system:vectrex\nsort_column=99\nsort_order=1\ncollapsed=Nope, Systems\n"
                   "size=20x10\n")
    shell.WindowState(ini).restore(win)
    assert win._scope == "all" and win.stack.currentWidget() is win.table
    assert win.table.horizontalHeader().sortIndicatorSection() == models.COL_TITLE
    assert win._collapsed == {"Systems"}
    assert win.width() >= 800                              # a nonsense size is not applied
    shell.WindowState(tmp_path / "missing.ini").restore(win)   # no file: nothing happens


def test_closing_during_a_sync_lets_it_finish(win, monkeypatch):
    c = win.controller
    gone = keys(win, 3)
    c.set_want(gone, False)
    slow_device(monkeypatch)
    c.start_sync(c.plan)
    assert wait_until(lambda: c.runner is not None and c.runner._job, 5)
    win.close()
    assert not win.isVisible() and c.job_running            # hidden, still working
    assert win.quits == []
    assert wait_until(lambda: not c.job_running, 30)
    assert c.last_result["state"] == "done" and c.open_job() is None
    assert {r.key: r.state for r in c.rows}[gone[0]] == "library_only"
    assert win.notes == [("Sync finished", "0 added, 3 removed")]
    assert win.quits == [1]


def test_closing_while_the_device_is_away_pauses_and_quits(win, monkeypatch):
    c = win.controller
    c.set_want(keys(win, 3), False)
    slow_device(monkeypatch)
    c.start_sync(c.plan)
    assert wait_until(lambda: c.runner is not None and c.runner._job, 5)
    win.close()
    c.session.demo_device.online = False                    # unplugged while nobody is watching
    assert wait_until(lambda: not c.job_running, 30)
    assert c.last_result["state"] == "paused" and c.open_job() is not None
    assert win.notes and win.notes[0][0] == "Sync paused"
    assert win.quits == [1]


def test_closing_with_nothing_running_quits(win):
    win.close()
    assert win.quits == [1] and win.notes == []


def test_reopening_brings_the_window_back_and_keeps_it(win, monkeypatch):
    c = win.controller
    c.set_want(keys(win, 2), False)
    slow_device(monkeypatch)
    c.start_sync(c.plan)
    assert wait_until(lambda: c.runner is not None and c.runner._job, 5)
    win.close()
    win.reopen()                                            # a second launch asked for the window
    assert win.isVisible()
    assert wait_until(lambda: not c.job_running, 30)
    assert win.quits == []                                  # it is in use again: do not quit under the user


def test_finished_sync_notifies_when_the_window_is_not_in_front(win, monkeypatch):
    c = win.controller
    monkeypatch.setattr(c, "_window_active", lambda: False)
    c.set_want(keys(win, 1), False)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running, 30)
    assert win.notes == [("Sync finished", "0 added, 1 removed")] and win.quits == []


def test_no_notification_when_the_window_is_in_front(win, monkeypatch):
    c = win.controller
    monkeypatch.setattr(c, "_window_active", lambda: True)
    c.set_want(keys(win, 1), False)
    c.start_sync(c.plan)
    assert wait_until(lambda: not c.job_running, 30)
    assert win.notes == []
