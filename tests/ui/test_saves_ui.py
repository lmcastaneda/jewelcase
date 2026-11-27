"""Save backups in the window: automatic runs, restore with Undo, the check before an emulator is removed."""
import os
import time

import pytest

from jewelcase.core import saves
from jewelcase.ui import dialogs

from uihelpers import make_window, wait_until

CC = "/sdcard/RetroArch/saves/Chrono Compass (USA).srm"
KEY = "retroarch-saves/Chrono Compass (USA)"
CARD = "/sdcard/Android/data/com.armsx2/files/memcards/mcd001.ps2"


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.controller.shutdown(5)
    w.close()


def put(dev, path: str, data: bytes) -> None:
    p = dev._p(path)
    p.write_bytes(data)
    t = p.stat().st_mtime + 300 + len(data)
    os.utime(p, (t, t))


def slow(dev, method: str, seconds: float) -> None:
    real = getattr(dev, method)

    def slowed(*args, **kw):
        time.sleep(seconds)
        return real(*args, **kw)
    setattr(dev, method, slowed)


def backed_up(c) -> bool:
    return saves.last_backup(c.session) is not None and not c.saves_busy


def toasts(c) -> list:
    seen = []
    c.toast.connect(lambda text, label, action: seen.append((text, label)))
    return seen


# ---------------------------------------------------------------- automatic runs
def test_no_automatic_backups_unless_switched_on(win):
    c = win.controller
    c.refresh()
    assert wait_until(lambda: not c._refreshing) and not c.saves_busy
    assert saves.last_backup(c.session) is None


def test_backup_runs_when_a_device_connects(win):
    c = win.controller
    c.auto_backup = True
    seen = toasts(c)
    c.refresh()
    assert wait_until(lambda: backed_up(c))
    assert saves.last_backup(c.session).copied == 7
    assert seen == []                                       # quiet: nothing was asked for


def test_hourly_rerun(win):
    c = win.controller
    c.auto_backup = True
    c.refresh()
    assert wait_until(lambda: backed_up(c)) and c.saves_runs == 1
    c._maybe_backup()
    assert not c.saves_busy and c.saves_runs == 1           # the last one is fresh
    c.BACKUP_EVERY_S = 0
    c._maybe_backup()
    assert wait_until(lambda: c.saves_runs == 2 and not c.saves_busy)


def test_a_failed_automatic_backup_is_not_retried_at_once(win):
    c = win.controller
    (c.session.library.root / "_saves").write_text("in the way")
    c.auto_backup = True
    c._maybe_backup()
    assert wait_until(lambda: c.saves_runs == 1 and not c.saves_busy)
    c._maybe_backup()
    assert not c.saves_busy and c.saves_runs == 1
    c.BACKUP_RETRY_S = 0
    c._maybe_backup()
    assert wait_until(lambda: c.saves_runs == 2 and not c.saves_busy)


def test_no_automatic_backup_with_the_library_offline(win):
    c = win.controller
    c.auto_backup = True
    c.session.library.root = c.session.library.root / "missing"
    c._maybe_backup()
    assert not c.saves_busy and c.saves_runs == 0


def test_back_up_now_reports_a_problem(win):
    c = win.controller
    seen = toasts(c)
    c.session.library.root = c.session.library.root / "missing"
    assert c.backup_saves() is True
    assert wait_until(lambda: not c.saves_busy)
    assert seen == [("The library disk is not connected, so saves are not being backed up.", None)]


def test_sync_stops_a_running_backup_and_a_backup_follows(win):
    c = win.controller
    slow(c.session.device, "pull", 0.15)
    c.auto_backup = True
    assert c.backup_saves(auto=True)
    key = next(r.key for r in c.rows if r.state == "library_only" and r.lib_unit is not None)
    c.set_want([key], True)
    c.start_sync(c.plan)
    assert wait_until(lambda: c.last_result is not None and not c.job_running, 30)
    assert c.last_result["state"] == "done" and c.last_result["added"] == 1
    assert c.saves_results[0].stopped == "asked"                 # the backup gave way to the sync
    assert wait_until(lambda: backed_up(c), 30)             # and one ran afterwards


# ---------------------------------------------------------------- restoring
def two_versions(c) -> str:
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    first = saves.last_backup(c.session).stamp
    put(c.session.device, CC, b"second save")
    assert c.backup_saves() and wait_until(lambda: c.saves_runs == 2 and not c.saves_busy)
    return first


def test_restore_offers_undo(win):
    c = win.controller
    first = two_versions(c)
    c.restore_saves([KEY], at=first)
    assert wait_until(lambda: win.toast.label.text() == "Save restored for Chrono Compass")
    assert c.session.device.read_bytes(CC) != b"second save"
    assert win.toast.action_button.text() == "Undo"
    win.toast.action_button.click()
    assert wait_until(lambda: c.session.device.read_bytes(CC) == b"second save" and not c.saves_busy)


def test_restore_that_cannot_happen_says_why(win):
    c = win.controller
    first = two_versions(c)
    c.session.device.foreground = "com.retroarch.aarch64"
    c.restore_saves([KEY], at=first)
    assert wait_until(lambda: win.toast.label.text() == "Close RetroArch on the handheld first.")
    assert not win.toast.action_button.isVisible()
    assert c.session.device.read_bytes(CC) == b"second save"


def test_restore_all_summary(win):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    c.session.device.foreground = "com.armsx2"
    c.restore_all_saves()
    assert wait_until(lambda: win.toast.label.text() == "4 of 7 saves restored")
    assert win.toast.action_button.text() == "Undo"
    assert len(c.restore_failed) == 3 and c.restore_failed[0][1] == "Close ARMSX2 on the handheld first."


def test_restore_all_for_one_emulator(win):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    put(c.session.device, CARD, b"blank card")
    c.restore_all_saves("com.armsx2")
    assert wait_until(lambda: win.toast.label.text() == "3 of 3 saves restored")
    assert c.session.device.read_bytes(CARD) != b"blank card"


def test_overview_and_unreadable_count(win):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    assert c.unreadable_saves("org.ppsspp.ppsspp") == "2 save folders" and c.unreadable_saves("com.armsx2") == ""
    assert {e.label for e in c.saves_overview()} >= {"RetroArch", "ARMSX2", "PPSSPP"}


# ---------------------------------------------------------------- before an emulator is removed
def emulators_page(win):
    page = win.pages["device:emulators"]
    c = win.controller
    c.load_emulators()
    assert wait_until(lambda: page.row_count() >= 7 and not c.emu_busy, 10)
    return page


def idle(win) -> bool:
    return wait_until(lambda: not win.controller.emu_busy, 15)


def test_removal_backs_up_first(win):
    page = emulators_page(win)
    page.select("com.armsx2")
    page.do_remove(keep_data=False)
    assert idle(win) and page.state_of("com.armsx2") == "Not installed"
    latest = win.controller.session.saves_root / "latest" / "armsx2-saves" / "mcd001.ps2"
    assert latest.is_file()
    assert saves.last_backup(win.controller.session) is None        # not a full backup


def test_removal_stops_when_backup_fails(win):
    page = emulators_page(win)
    (win.controller.session.library.root / "_saves").write_text("in the way")
    page.select("com.armsx2")
    page.do_remove(keep_data=False)
    assert idle(win) and page.state_of("com.armsx2").startswith("Enabled")
    assert "ARMSX2 was not removed: its saves could not be backed up first" in page.status.text()


def test_removal_keeping_data_goes_ahead_and_says_so(win):
    page = emulators_page(win)
    (win.controller.session.library.root / "_saves").write_text("in the way")
    page.select("com.armsx2")
    page.do_remove(keep_data=True)
    assert idle(win) and page.state_of("com.armsx2") == "Not installed"
    assert "Its saves could not be backed up first" in page.status.text()


def test_remove_dialog_warns_about_unreadable_saves(win):
    dlg, _ = dialogs.build_remove_dialog(win, "PPSSPP", "10 save folders")
    assert "Jewelcase could not back up 10 save folders of PPSSPP. Removing it deletes them." in dlg.secondary.text()
    dlg, _ = dialogs.build_remove_dialog(win, "PPSSPP", "1 save folder")
    assert "Jewelcase could not back up 1 save folder of PPSSPP. Removing it deletes it." in dlg.secondary.text()
    dlg, _ = dialogs.build_remove_dialog(win, "AetherSX2", "15 files")
    assert "Jewelcase could not back up 15 files of AetherSX2. Removing it deletes them." in dlg.secondary.text()
    dlg, _ = dialogs.build_remove_dialog(win, "ARMSX2")
    assert "could not back up" not in dlg.secondary.text()


# ---------------------------------------------------------------- closing the window
def test_closing_during_backup_stops_it(win):
    c = win.controller
    slow(c.session.device, "pull", 0.15)
    quits = []
    c.quit_requested.connect(lambda: quits.append(1))
    assert c.backup_saves()
    win.close()
    assert quits == [1]
    assert c.shutdown(10) and not c.saves_busy
    assert c.saves_results[0].stopped == "asked"
    assert not list(c.session.library.root.rglob("*.part"))


def test_closing_during_restore_lets_it_finish(win):
    c = win.controller
    first = two_versions(c)
    slow(c.session.device, "push", 0.3)
    quits = []
    c.quit_requested.connect(lambda: quits.append(1))
    c.restore_saves([KEY], at=first)
    win.close()
    assert quits == [] and not win.isVisible() and c.saves_busy       # hidden, still working
    assert wait_until(lambda: quits == [1], 15)
    assert c.session.device.read_bytes(CC) != b"second save"


def test_shutdown_never_cuts_a_restore_short(win):
    c = win.controller
    first = two_versions(c)
    slow(c.session.device, "push", 0.4)
    c.restore_saves([KEY], at=first)
    assert c.shutdown(0.05) and not c.saves_busy                        # waited past its time limit
    assert c.session.device.read_bytes(CC) != b"second save"


# ---------------------------------------------------------------- the Saves page
def page_of(win):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    win.select_scope("device:saves")
    return win.pages["device:saves"]


def test_sidebar_has_saves_after_emulators(win):
    from PySide6.QtCore import Qt
    scopes = [i.data(0, Qt.UserRole) for i in win._items()]
    assert scopes.index("device:saves") == scopes.index("device:emulators") + 1


def test_saves_page_lists_emulators_and_items(win):
    rows = page_of(win).rows()
    assert any(r[0].startswith("RetroArch · Protected · 3 saves, 1 save state") for r in rows)
    assert any(r[0].startswith("PPSSPP · Not Protected · 2 save folders cannot be read") for r in rows)
    assert any(r[0].startswith("M64Plus FZ Pro · Not Reachable") for r in rows)
    cc = next(r for r in rows if r[0] == "Chrono Compass")
    assert cc[:2] == ("Chrono Compass", "Save") and cc[2] == "Today" and cc[3] == "1" and cc[4].endswith(" B")
    card = next(r for r in rows if r[0] == "mcd001.ps2")
    assert card[1] == "Memory Card" and card[4] == "8.0 MB"


def test_page_explains_unreadable_saves(win):
    page = page_of(win)
    assert page.infobar.kind == "info" and not page.infobar.isHidden()
    assert page.infobar.label.text() == ("PSP saves cannot be read where PPSSPP keeps them. Move its memory "
                                         "stick to /sdcard/PSP in PPSSPP's settings.")


def test_library_offline_infobar(win):
    page = page_of(win)
    win.controller.session.library.root = win.controller.session.library.root / "missing"
    page.refresh()
    assert page.infobar.kind == "warning"
    assert page.infobar.label.text() == "The library disk is not connected, so saves are not being backed up."
    assert not page.backup_button.isEnabled() and not page.restore_all_button.isEnabled()
    assert any(r[0] == "Chrono Compass" for r in page.rows())          # what is backed up is still listed


def test_back_up_now(win):
    c = win.controller
    win.select_scope("device:saves")
    page = win.pages["device:saves"]
    assert page.status_label.text() == "Saves have not been backed up yet"
    assert page.backup_button.text() == "Back Up Now" and page.backup_button.isEnabled()
    page.backup_button.click()
    assert wait_until(lambda: backed_up(c))
    assert page.status_label.text() == "Last backed up just now"


def test_page_shows_a_running_backup(win):
    c = win.controller
    slow(c.session.device, "pull", 0.1)
    win.select_scope("device:saves")
    page = win.pages["device:saves"]
    page.backup_button.click()
    assert page.status_label.text() == "Backing up saves…" and not page.backup_button.isEnabled()
    assert wait_until(lambda: backed_up(c)) and page.backup_button.isEnabled()


def test_status_mentions_skipped_files(win):
    win.controller.session.device.mutate_on_pull = 2
    page = page_of(win)
    assert page.status_label.text() == "Last backed up just now · 1 file could not be copied"


def test_gone_saves_say_so(win):
    c = win.controller
    page = page_of(win)
    c.session.device._p(CC).unlink()
    assert c.backup_saves() and wait_until(lambda: c.saves_runs == 2 and not c.saves_busy)
    assert next(r for r in page.rows() if r[:2] == ("Chrono Compass", "Save"))[2] == "No longer on the handheld"


def test_restore_button_needs_one_item(win):
    page = page_of(win)
    assert not page.restore_button.isEnabled() and page.restore_all_button.isEnabled()
    assert page.restore_all_target() == (None, "", 7)
    page.select("com.armsx2")
    assert not page.restore_button.isEnabled() and page.restore_all_target() == ("com.armsx2", "ARMSX2", 3)
    page.select(KEY)
    assert page.restore_button.isEnabled() and page.restore_all_target() == ("com.retroarch.aarch64", "RetroArch", 4)
    page.select("org.ppsspp.ppsspp")
    assert not page.restore_all_button.isEnabled()                      # nothing of it was backed up


def test_restore_dialog(win):
    c = win.controller
    first = two_versions(c)
    win.select_scope("device:saves")
    dlg = win.pages["device:saves"].build_restore_dialog(KEY)
    assert dlg.windowTitle().strip() == "" and dlg.headline.text() == "Restore the save for Chrono Compass?"
    assert dlg.secondary.text() == "The save on the handheld now is backed up first, so this can be undone."
    assert [b.text() for b in dlg.buttons_in_order()] == ["Cancel", "Restore"]
    assert dlg.restore_button.objectName() == "suggested" and dlg.restore_button.isDefault()
    assert dlg.list.count() == 2 and "newest copy" in dlg.list.item(0).text()
    assert dlg.chosen() == saves.last_backup(c.session).stamp
    dlg.list.setCurrentRow(1)
    assert dlg.chosen() == first


def test_restore_from_the_page(win):
    c = win.controller
    first = two_versions(c)
    win.select_scope("device:saves")
    page = win.pages["device:saves"]
    page.select(KEY)
    page.restore_selected(at=first)
    assert wait_until(lambda: win.toast.label.text() == "Save restored for Chrono Compass")
    assert c.session.device.read_bytes(CC) != b"second save"


def test_restore_all_dialog_text(win):
    from jewelcase.core.db import RunRow
    runs = [RunRow("20261126-140500", True, 3, {}), RunRow("20261125-090000", True, 7, {})]
    dlg = dialogs.build_restore_all_dialog(win, "ARMSX2", 14, runs)
    assert dlg.windowTitle().strip() == "" and dlg.primary.text() == "Restore all 14 saves for ARMSX2?"
    assert dlg.secondary.text() == "The saves on the handheld now are backed up first."
    assert [b.text() for b in dlg.buttons_in_order()] == ["Cancel", "Restore All"]
    assert dlg.buttons_in_order()[1].objectName() == "suggested"
    assert [dlg.from_box.itemText(i) for i in range(dlg.from_box.count())] == [
        "The newest copies", "As of 26 Nov 2026, 14:05", "As of 25 Nov 2026, 09:00"]
    assert dlg.chosen() is None
    dlg.from_box.setCurrentIndex(2)
    assert dlg.chosen() == "20261125-090000"
    assert dialogs.build_restore_all_dialog(win, "", 23, []).primary.text() == "Restore all 23 saves?"


def test_game_row_menu_offers_restore(win):
    c = win.controller
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    labels = [a.text() for a in win.build_table_menu([("snes", "Chrono Compass (USA)")]).actions()]
    assert "Restore Save…" in labels and "Restore Save State…" in labels
    labels = [a.text() for a in win.build_table_menu([("gba", "Minish Hat (USA)")]).actions()]
    assert "Restore Save…" in labels and "Restore Save State…" not in labels
    labels = [a.text() for a in win.build_table_menu([("snes", "Super Plumber Land (USA)")]).actions()]
    assert not [t for t in labels if t.startswith("Restore")]
    both = [("snes", "Chrono Compass (USA)"), ("gba", "Minish Hat (USA)")]
    assert not [a.text() for a in win.build_table_menu(both).actions() if a.text().startswith("Restore")]


def test_summary_shows_last_backup(win):
    c = win.controller
    summary = win.pages["device:summary"]
    assert summary.fields["Saves"].text() == "Not backed up yet"
    assert c.backup_saves() and wait_until(lambda: backed_up(c))
    assert summary.fields["Saves"].text() == "Backed up just now"
    c.session.library.root = c.session.library.root / "missing"
    summary.refresh()
    assert summary.fields["Saves"].text() == "Not backed up, the library disk is not connected"


def test_preference_switch(win):
    c = win.controller
    dlg = dialogs.open_preferences(win)
    assert dlg.states_check.isChecked() and dlg.states_check.text() == "Back up save states"
    dlg.states_check.setChecked(False)
    assert c.session.cfg.backup_save_states is False
    dlg.close()


def test_human_since():
    from datetime import datetime, timedelta
    from jewelcase.core.fmt import human_since
    now = datetime(2026, 11, 26, 15, 0, 0)
    assert human_since(now - timedelta(seconds=20), now) == "just now"
    assert human_since(now + timedelta(seconds=5), now) == "just now"
    assert human_since(now - timedelta(minutes=1, seconds=5), now) == "1 minute ago"
    assert human_since(now - timedelta(minutes=12), now) == "12 minutes ago"
    assert human_since(now - timedelta(hours=1, minutes=10), now) == "1 hour ago"
    assert human_since(now - timedelta(hours=5), now) == "5 hours ago"
    assert human_since(now - timedelta(days=1, hours=2), now) == "yesterday"
    assert human_since(None, now) == "never"


# ---------------------------------------------------------------- found by the branch review
def test_reconnecting_backs_up_even_when_the_last_backup_is_fresh(win):
    c = win.controller
    c.auto_backup = True
    c.refresh()
    assert wait_until(lambda: backed_up(c)) and c.saves_runs == 1
    c.session.demo_device.online = False
    c.refresh()
    assert wait_until(lambda: not c._refreshing and not c.connected) and c.saves_runs == 1
    c.session.demo_device.online = True
    c.refresh()
    assert wait_until(lambda: c.saves_runs == 2 and not c.saves_busy)


def test_remove_dialog_scans_for_unreadable_saves_itself(win, monkeypatch):
    page = emulators_page(win)
    cards = "/sdcard/Android/data/com.armsx2/files/memcards/"
    win.controller.session.device.unreadable_files = {cards + "mcd001.ps2", cards + "mcd002.ps2"}
    asked = []
    monkeypatch.setattr(dialogs, "confirm_remove_emulator",
                        lambda parent, name, unreadable="": asked.append((name, unreadable)) or (False, False))
    page.select("com.armsx2")
    page._confirm_remove()                                   # no backup has ever run in this world
    assert wait_until(lambda: asked == [("ARMSX2", "2 files")])
    assert idle(win) and page.state_of("com.armsx2").startswith("Enabled")


def test_page_lists_files_the_last_backup_could_not_copy(win, monkeypatch):
    from jewelcase.core import savesources
    monkeypatch.setattr(savesources, "MAX_FILE", 9 * 1024 * 1024)
    rows = page_of(win).rows()
    assert any(r[0].startswith("ARMSX2 · Partly Protected · 2 memory cards; 1 file could not be copied") for r in rows)
    assert ("SLUS-20312 (D224D348).00.p2s", "Not Backed Up", "Larger than 256 MB", "", "") in rows


def test_page_shows_why_saves_were_not_restored(win):
    c = win.controller
    page = page_of(win)
    c.session.device.foreground = "com.armsx2"
    c.restore_all_saves()
    assert wait_until(lambda: win.toast.label.text() == "4 of 7 saves restored")
    assert page.infobar.kind == "warning"
    assert page.infobar.label.text() == "3 saves were not restored. The list says why for each."
    assert next(r for r in page.rows() if r[0] == "mcd001.ps2")[2] == \
        "Not restored: Close ARMSX2 on the handheld first."
    assert page.infobar.action_button.text() == "Dismiss"
    page.infobar.action_button.click()
    assert page.infobar.kind == "info" and next(r for r in page.rows() if r[0] == "mcd001.ps2")[2] == "Today"


# ---------------------------------------------------------------- emulator settings
RA_CFG = "/sdcard/Android/data/com.retroarch.aarch64/files/retroarch.cfg"
RA_CFG_KEY = "retroarch-settings/retroarch.cfg"


def with_settings(win):
    dev = win.controller.session.device
    dev._p(RA_CFG).parent.mkdir(parents=True, exist_ok=True)
    dev._p(RA_CFG).write_bytes(b"video_vsync = true")


def test_settings_show_as_their_own_kind(win):
    with_settings(win)
    page = page_of(win)
    row = next(r for r in page.rows() if r[:2] == ("retroarch.cfg", "Settings"))
    assert row[3] == "1"
    assert next(r for r in page.rows() if r[0].startswith("RetroArch ·"))[0].endswith("1 settings file")


def test_a_settings_file_is_restored_from_the_page_with_undo(win):
    c = win.controller
    with_settings(win)
    page = page_of(win)
    put(c.session.device, RA_CFG, b"video_vsync = false")
    page.select(RA_CFG_KEY)
    page.restore_selected()
    assert wait_until(lambda: win.toast.label.text() == "Settings restored for retroarch.cfg")
    assert c.session.device.read_bytes(RA_CFG) == b"video_vsync = true"
    dlg = page.build_restore_dialog(RA_CFG_KEY)
    assert dlg.headline.text() == "Restore retroarch.cfg?"


def test_restore_all_says_items_when_settings_are_among_them(win):
    dlg = dialogs.build_restore_all_dialog(win, "RetroArch", 5, [], "item")
    assert dlg.primary.text() == "Restore all 5 items for RetroArch?"
    assert dlg.secondary.text() == "What is on the handheld now is backed up first."
    assert dialogs.build_restore_all_dialog(win, "RetroArch", 1, [], "item").primary.text() \
        == "Restore 1 item for RetroArch?"


def test_preferences_switch_for_settings(win):
    c = win.controller
    dlg = dialogs.open_preferences(win)
    assert dlg.settings_check.isChecked() and dlg.settings_check.text() == "Back up emulator settings"
    dlg.settings_check.setChecked(False)
    assert c.session.cfg.backup_settings is False
    dlg.close()


# ---------------------------------------------------------------- another handheld's backups
def test_the_restore_all_dialog_offers_handhelds_only_when_there_are_several(win):
    from jewelcase.core.db import RunRow
    assert dialogs.build_restore_all_dialog(win, "", 5, []).set_box is None
    own_runs = [RunRow("20261126-140500", True, 3, {})]
    old_runs = [RunRow("20261001-090000", True, 2, {}), RunRow("20260901-090000", True, 2, {})]
    old = object()
    sets = [(None, "RG Demo · DEMO0001 (this handheld)", 5, own_runs), (old, "RG477V · OLD00001", 2, old_runs)]
    dlg = dialogs.build_restore_all_dialog(win, "", 5, own_runs, "save", sets)
    assert [dlg.set_box.itemText(i) for i in range(dlg.set_box.count())] == [
        "RG Demo · DEMO0001 (this handheld)", "RG477V · OLD00001"]
    assert dlg.chosen_set() is None and dlg.chosen() is None                   # the default is its own set
    assert dlg.primary.text() == "Restore all 5 saves?"
    assert dlg.from_box.count() == 2
    dlg.set_box.setCurrentIndex(1)
    assert dlg.chosen_set() is old and dlg.primary.text() == "Restore all 2 saves?"
    assert [dlg.from_box.itemText(i) for i in range(dlg.from_box.count())] == [
        "The newest copies", "As of 1 Oct 2026, 09:00", "As of 1 Sep 2026, 09:00"]
    dlg.from_box.setCurrentIndex(2)
    assert dlg.chosen() == "20260901-090000"


def test_a_handheld_without_backups_can_only_restore_from_another(win):
    old = object()
    dlg = dialogs.build_restore_all_dialog(win, "", 0, [], "save", [(None, "This handheld", 0, []),
                                                                    (old, "RG477V · OLD00001", 2, [])])
    go = dlg.buttons_in_order()[1]
    assert dlg.primary.text() == "This handheld has no backups to restore yet" and not go.isEnabled()
    dlg.set_box.setCurrentIndex(1)
    assert go.isEnabled() and dlg.primary.text() == "Restore all 2 saves?"


def test_restore_all_from_another_handheld_in_the_window(win, tmp_path):
    from test_savesets import OLD, make_set
    c = win.controller
    page = page_of(win)
    own_save = c.session.device.read_bytes(CC)
    assert not page._others
    make_set(tmp_path / "demo", {CC: b"progress from the old handheld"})
    page.refresh()
    assert page._others and page.restore_all_button.isEnabled()
    c.restore_all_saves(None, None, saves.other_set(c.session, OLD))
    assert wait_until(lambda: not c.saves_busy and c.session.device.read_bytes(CC) == b"progress from the old handheld")
    assert win.toast.action_button.text() == "Undo"
    win.toast.action_button.click()
    assert wait_until(lambda: c.session.device.read_bytes(CC) == own_save and not c.saves_busy)


# ---------------------------------------------------------------- saves named by a game ID
def test_an_id_named_save_state_is_linked_to_its_game_once_the_page_has_read_the_id(tmp_path, qapp):
    from jewelcase.core.demo import make_demo
    from gameimages import ps2_disc
    make_demo(tmp_path / "demo")
    (tmp_path / "demo" / "library" / "ps2" / "Wrath of Kings (USA).iso").write_bytes(ps2_disc())
    w = make_window(tmp_path)
    try:
        c = w.controller
        assert c.backup_saves() and wait_until(lambda: backed_up(c))
        page = w.pages["device:saves"]
        name = "SLUS-20312 (D224D348).00.p2s — Wrath of Kings"
        assert wait_until(lambda: any(r[0] == name and r[1] == "Save State" for r in page.rows()))
        assert any(r[0] == "mcd001.ps2" for r in page.rows())
        game = [("ps2", "Wrath of Kings (USA)")]
        assert wait_until(lambda: "Restore Save State…" in [a.text() for a in w.build_table_menu(game).actions()])
        assert not [a for a in w.build_table_menu([("ps2", "Clockwork Citadel (USA)")]).actions()
                    if a.text().startswith("Restore")]
    finally:
        w.controller.shutdown(5)
        w.close()
