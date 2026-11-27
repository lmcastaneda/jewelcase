import os
import shutil
import subprocess
import sys

import pytest
from PySide6.QtCore import Qt

from jewelcase.core import catalog
from jewelcase.ui import models

from uihelpers import make_window


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.close()


def test_window_builds_and_lists_games(win):
    rows = win.controller.rows
    assert len(rows) >= 35
    assert win.games_model.rowCount() == len(rows)
    assert win.games_filter.rowCount() == len(rows)
    assert win.windowTitle() == "All Games — Jewelcase"


def test_sidebar_counts(win):
    rows, cfg = win.controller.rows, win.controller.session.cfg
    assert win.sidebar_count("all") == len(rows)
    assert win.sidebar_count("smart:never_played") == len(catalog.smart(rows, "never_played", cfg))
    assert win.sidebar_count("system:ps2") == len([r for r in rows if r.system == "ps2"])
    assert win.sidebar_count("smart:device_only") == 1


def test_smart_list_filters_rows(win):
    win.select_scope("smart:never_played")
    want = catalog.smart(win.controller.rows, "never_played", win.controller.session.cfg)
    assert win.games_filter.rowCount() == len(want)


def test_system_scope(win):
    win.select_scope("system:gba")
    assert win.games_filter.rowCount() == len([r for r in win.controller.rows if r.system == "gba"])


def test_search_filters(win):
    win.search.setText("lantern")
    hits = [r for r in win.controller.rows if "lantern" in (r.title + r.stem).lower()]
    assert 0 < win.games_filter.rowCount() == len(hits)
    win.search.setText("")
    assert win.games_filter.rowCount() == len(win.controller.rows)


def row_of(win, predicate):
    for i, r in enumerate(win.controller.rows):
        if predicate(r):
            return i
    raise AssertionError("no such row")


def test_tick_updates_pending_line(win):
    assert "In sync" in win.pending_label.text()
    i = row_of(win, lambda r: r.on_device and r.in_library)
    idx = win.games_model.index(i, models.COL_CHECK)
    assert win.games_model.setData(idx, Qt.Unchecked, Qt.CheckStateRole)
    assert "− 1 game" in win.pending_label.text()
    assert win.games_model.data(win.games_model.index(i, models.COL_STATUS), Qt.DisplayRole) == "Will be removed"
    assert win.games_model.rowCount() == len(win.controller.rows)
    j = row_of(win, lambda r: r.state == "library_only")
    win.games_model.setData(win.games_model.index(j, models.COL_CHECK), Qt.Checked, Qt.CheckStateRole)
    assert "+ 1 game" in win.pending_label.text() and "− 1 game" in win.pending_label.text()
    assert win.sync_button.isEnabled()


def status_of(win, i):
    return win.games_model.data(win.games_model.index(i, models.COL_STATUS), Qt.DisplayRole)


def tip_of(win, i):
    return win.games_model.data(win.games_model.index(i, models.COL_STATUS), Qt.ToolTipRole)


def test_an_archive_only_game_can_be_ticked_and_says_it_will_be_converted(win, monkeypatch):
    from jewelcase.core import convert
    monkeypatch.setattr(convert, "_chdman", lambda: None)
    i = row_of(win, lambda r: r.state == "needs_conversion")
    assert win.games_model.flags(win.games_model.index(i, models.COL_CHECK)) & Qt.ItemIsUserCheckable
    assert status_of(win, i) == "Archive in library"
    assert tip_of(win, i) == ("The library has this game as an archive. Tick it and the next sync converts it "
                              "first; the converted game is kept beside the archive.")
    win.games_model.setData(win.games_model.index(i, models.COL_CHECK), Qt.Checked, Qt.CheckStateRole)
    i = row_of(win, lambda r: r.state == "needs_conversion")
    if not shutil.which("7z"):
        assert status_of(win, i) == "Cannot be converted" and tip_of(win, i) == "7z is not installed."
        assert win.pending_label.text() == "" or "+ 1 game" not in win.pending_label.text()
        return
    assert status_of(win, i) == "Will be converted and added"
    assert tip_of(win, i) == "Northwind Saga (USA).iso, made from Northwind Saga (USA).zip"
    assert "+ 1 game" in win.pending_label.text() and win.sync_button.isEnabled()


def test_a_game_in_a_form_nothing_can_be_made_from_cannot_be_ticked(win):
    c = win.controller
    (c.session.library.root / "ps2" / "Odd Format (USA).nrg").write_bytes(b"nrg")
    c.refresh_blocking()
    i = row_of(win, lambda r: r.stem == "Odd Format (USA)")
    assert not (win.games_model.flags(win.games_model.index(i, models.COL_CHECK)) & Qt.ItemIsUserCheckable)
    assert status_of(win, i) == "Needs conversion"
    assert tip_of(win, i) == "The library has this game in a form the handheld cannot play, and Jewelcase cannot convert it."
    assert c.set_want([("ps2", "Odd Format (USA)")], True) == 0


def test_toggle_selection_changes_many(win):
    win.select_scope("smart:never_played")
    n = win.games_filter.rowCount()
    win.table.selectAll()
    win.set_selection_wanted(False)
    assert f"− {n} games" in win.pending_label.text()
    win.set_selection_wanted(True)
    assert "In sync" in win.pending_label.text()


def test_sort_by_size_uses_numbers(win):
    win.games_filter.sort(models.COL_SIZE, Qt.DescendingOrder)
    first = win.games_filter.index(0, models.COL_SIZE).data(models.SORT_ROLE)
    assert first == max(r.size for r in win.controller.rows)


def test_capsule_and_capacity_show_device(win):
    assert "RG Demo" in win.capsule.title.text() and "81%" in win.capsule.title.text()
    assert win.capacity.total == 2048 * 1024 * 1024
    assert sum(size for _, _, size in win.capacity.segments) > 0


def test_no_device_state(win):
    win.controller.session.demo_device.online = False
    win.controller.refresh_blocking()
    assert win.capsule.title.text() == "No device"
    assert win.games_model.rowCount() >= 35
    assert not win.sync_button.isEnabled()
    assert "last known" in win.pending_label.text()


def test_selftest_exits_zero():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    cp = subprocess.run([sys.executable, "-m", "jewelcase.cli", "--demo", "gui", "--selftest"],
                        capture_output=True, text=True, env=env, timeout=120)
    assert cp.returncode == 0, cp.stderr
    assert "selftest ok" in cp.stdout
    assert "pictures ok" in cp.stdout            # covers are PNG or JPEG, ES-DE's system images WebP
