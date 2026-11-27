import pytest

from jewelcase.ui.views import EmulatorsView

from uihelpers import make_window, wait_until


@pytest.fixture
def page(qapp, tmp_path):
    w = make_window(tmp_path)
    w.select_scope("device:emulators")
    p = w.pages["device:emulators"]
    assert isinstance(p, EmulatorsView)
    assert wait_until(lambda: p.row_count() >= 7 and not w.controller.emu_busy, 10)
    p.window_ref = w
    yield p
    wait_until(lambda: not w.controller.emu_busy, 5)
    w.close()


def idle(page):
    return wait_until(lambda: not page.controller.emu_busy, 10)


def test_emulators_view_lists_device(page):
    assert page.window_ref.stack.currentWidget() is page
    assert page.state_of("com.flycast.emulator").startswith("Disabled")
    assert page.state_of("com.armsx2") == "Enabled · installed by you"
    assert page.state_of("org.ppsspp.ppsspp") == "Enabled · came with the device, updated since"
    assert page.state_of("me.magnum.melonds") == "Not installed"
    assert page.version_of("com.armsx2") == "1.0.4"


def test_enable_and_disable(page):
    page.select("com.flycast.emulator")
    assert page.enable_button.isEnabled() and not page.disable_button.isEnabled()
    page.do_set_enabled(True)
    assert idle(page) and page.state_of("com.flycast.emulator").startswith("Enabled")
    page.select("com.flycast.emulator")
    page.do_set_enabled(False)
    assert idle(page) and page.state_of("com.flycast.emulator").startswith("Disabled")


def test_remove_user_app_and_refuse_system_app(page):
    page.select("com.armsx2")
    assert page.remove_button.isEnabled()
    page.do_remove(keep_data=True)
    assert idle(page) and page.state_of("com.armsx2") == "Not installed"
    page.select("com.retroarch.aarch64")
    assert not page.remove_button.isEnabled()          # came with the device
    page.do_remove(keep_data=False)
    assert idle(page) and "Disable it instead" in page.status.text()
    assert page.state_of("com.retroarch.aarch64").startswith("Enabled")


def test_factory_version_only_for_updated_system_apps(page):
    page.select("org.ppsspp.ppsspp")
    assert page.revert_button.isEnabled()
    page.select("com.retroarch.aarch64")
    assert not page.revert_button.isEnabled()


def test_install_from_file(page, tmp_path):
    apk = tmp_path / "thing.apk"
    apk.write_bytes(b"apk")
    page.do_install_file(apk)
    assert idle(page)
    assert page.controller.session.demo_device.installed == [apk]
    assert "was installed" in page.status.text()
    page.controller.session.demo_device.install_error = "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
    page.do_install_file(apk)
    assert idle(page) and "different publisher" in page.status.text()


def test_install_latest_needs_a_check_first(page):
    page.select("me.magnum.melonds")
    assert not page.install_button.isEnabled()
    assert "Check for updates" in page.install_button.toolTip()


# ---------------------------------------------------------------- what ES-DE opens games with
PSP_GRANT = ("PPSSPP has not been given the PSP folder yet. "
             "Open PPSSPP on the handheld and choose that folder in the app.")
PS2_PROBLEM = "ES-DE would open PlayStation 2 games with AetherSX2 (Standalone), which is not installed."


def esde_rows(page):
    return {r[0]: r for r in page.esde_rows()}


def test_page_lists_what_es_de_opens_each_system_with(page):
    rows = esde_rows(page)
    assert rows["PlayStation"][1] == "SwanStation"
    assert rows["PlayStation 2"][1:] == ("AetherSX2 (Standalone) (not installed)", "Not installed")
    assert "PlayStation 3" not in rows                  # no games, and nothing installed that could open them
    assert page.esde_choices("psx") == ["SwanStation", "PCSX ReARMed", "DuckStation (Standalone)"]
    assert page.esde_choices("ps2") == ["AetherSX2 (Standalone) (not installed)", "ARMSX2 (Standalone)"]


def test_a_system_whose_emulator_is_missing_can_be_fixed_and_undone(page):
    assert (page.infobar.kind, page.infobar.label.text()) == ("warning", PS2_PROBLEM)
    assert page.infobar.action_button.text() == "Use ARMSX2 (Standalone)"
    page.infobar.action_button.click()
    assert idle(page) and wait_until(lambda: page.infobar.label.text() == PSP_GRANT, 5)
    assert esde_rows(page)["PlayStation 2"][1:] == ("ARMSX2 (Standalone)", "")
    toast = page.window_ref.toast
    assert toast.label.text() == "PlayStation 2 games now open with ARMSX2 (Standalone)"
    assert toast.action_button.text() == "Undo"
    toast.action_button.click()
    assert wait_until(lambda: not page.controller.emu_busy and page.infobar.kind == "warning", 10)
    assert esde_rows(page)["PlayStation 2"][1] == "AetherSX2 (Standalone) (not installed)"


def test_an_emulator_without_its_games_folder_is_noted_in_the_table(page):
    rows = esde_rows(page)
    assert rows["PSP"][2] == PSP_GRANT
    assert rows["PlayStation"][2] == ""                 # SwanStation is handed the game as a plain path


def test_the_missing_folder_is_an_info_bar_without_a_button_once_nothing_is_missing_an_emulator(page):
    assert page.infobar.label.text() == PS2_PROBLEM         # the more urgent condition comes first
    page.choose_esde("ps2", "ARMSX2 (Standalone)")
    assert idle(page) and wait_until(lambda: page.infobar.label.text() == PSP_GRANT, 5)
    assert page.infobar.kind == "warning" and not page.infobar.action_button.isVisibleTo(page.infobar)


def test_an_emulator_given_its_folder_has_no_note_and_no_bar(page):
    page.choose_esde("ps2", "ARMSX2 (Standalone)")
    assert idle(page)
    page.controller.session.demo_device.fake_grants["org.ppsspp.ppsspp"] = ["/storage/1A2B-3C4D/psp"]
    page.controller.load_emulators()
    assert idle(page) and wait_until(lambda: page.infobar.kind is None, 5)
    assert esde_rows(page)["PSP"][2] == ""


def test_choosing_another_emulator_for_a_system(page, tmp_path):
    page.choose_esde("psx", "DuckStation (Standalone)")
    assert idle(page) and esde_rows(page)["PlayStation"][1] == "DuckStation (Standalone)"
    written = (tmp_path / "demo/device/sdcard/ES-DE/gamelists/psx/gamelist.xml").read_text(encoding="utf-8")
    assert "<label>DuckStation (Standalone)</label>" in written
    assert page.window_ref.toast.label.text() == "PlayStation games now open with DuckStation (Standalone)"
    assert ("stop", "org.es_de.frontend") in page.controller.session.demo_device.calls       # ES-DE was restarted


def test_games_with_their_own_emulator_are_mentioned(page, tmp_path):
    path = tmp_path / "demo/device/sdcard/ES-DE/gamelists/psx/gamelist.xml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("</game>", "\t<altemulator>DuckStation (Standalone)</altemulator>\n\t</game>", 2),
                    encoding="utf-8")
    page.controller.load_emulators()
    assert idle(page) and esde_rows(page)["PlayStation"][2] == "2 games use DuckStation (Standalone)"


def test_installing_an_emulator_fills_the_gap_in_es_de(page, tmp_path):
    from jewelcase.core.device import PackageInfo
    dev = page.controller.session.demo_device
    apk = tmp_path / "aps3e.apk"
    apk.write_bytes(b"apk")
    dev.apk_packages["aps3e.apk"] = PackageInfo("aenu.aps3e", "2.43", False, False, True)
    assert "PlayStation 3" not in esde_rows(page)
    page.do_install_file(apk)
    assert idle(page) and esde_rows(page)["PlayStation 3"][1:] == ("aPS3e ISO (Standalone)", "")
    toast = page.window_ref.toast
    assert toast.label.text() == \
        "aps3e.apk was installed. PlayStation 3 games now open with aPS3e ISO (Standalone)"
    toast.action_button.click()                                    # Undo: back to what ES-DE had
    assert wait_until(lambda: not page.controller.emu_busy
                      and esde_rows(page)["PlayStation 3"][1].endswith("(not installed)"), 10)
    assert page.version_of("aenu.aps3e") == "2.43"                 # the emulator itself stays installed


def test_installing_an_emulator_leaves_a_working_system_alone(page, tmp_path):
    from jewelcase.core.device import PackageInfo
    dev = page.controller.session.demo_device
    apk = tmp_path / "melonds.apk"
    apk.write_bytes(b"apk")
    dev.apk_packages["melonds.apk"] = PackageInfo("me.magnum.melonds", "1.0", False, False, True)
    before = page.esde_rows()
    page.do_install_file(apk)
    assert idle(page) and page.esde_rows() == before
    assert page.window_ref.toast.label.text() == "melonds.apk was installed"
    assert ("stop", "org.es_de.frontend") not in dev.calls


def test_a_change_that_breaks_off_part_way_leaves_the_page_true_and_says_what_happened(page, monkeypatch):
    from jewelcase.core import esde, device
    real = esde.use_many

    def breaks(dev, profile, defs, changes, backups, emulators):
        real(dev, profile, defs, changes, backups, emulators)       # the list is written...
        raise device.DeviceGone("the handheld went away")           # ...and then the link drops
    monkeypatch.setattr(esde, "use_many", breaks)
    page.choose_esde("ps2", "ARMSX2 (Standalone)")
    assert idle(page)
    assert "went away" in page.status.text() and "That did not work" not in page.status.text()
    assert esde_rows(page)["PlayStation 2"][1:] == ("ARMSX2 (Standalone)", "")      # what is true, not what was shown


def test_a_change_is_refused_while_a_game_is_running(page):
    page.controller.session.demo_device.foreground = "com.armsx2"
    page.choose_esde("psx", "DuckStation (Standalone)")
    assert idle(page) and esde_rows(page)["PlayStation"][1] == "SwanStation"
    assert page.status.text() == "Close the game on the handheld first."


def test_without_es_de_the_section_is_hidden(page):
    del page.controller.session.demo_device.fake_packages["org.es_de.frontend"]
    page.controller.load_emulators()
    assert idle(page) and page.esde_table.isHidden() and page.infobar.kind is None


def test_an_emulator_action_still_succeeds_when_es_de_cannot_be_read(page, monkeypatch):
    from jewelcase.core import esde

    def broken(session):
        raise RuntimeError("definitions unreadable")
    monkeypatch.setattr(esde, "for_session", broken)
    page.select("com.flycast.emulator")
    page.do_set_enabled(True)
    assert idle(page) and page.state_of("com.flycast.emulator").startswith("Enabled")
    assert page.status.text() == "Flycast was enabled" and page.esde_table.isHidden()


def test_the_choosers_follow_a_sync_starting_and_ending(page):
    c = page.controller
    combo = page._esde_combos["psx"]
    assert combo.isEnabled()
    c.job_running = True
    c.rows_changed.emit()
    assert not combo.isEnabled() and not page.infobar.action_button.isEnabled()
    c.job_running = False
    c.rows_changed.emit()
    assert combo.isEnabled() and page.infobar.action_button.isEnabled()


def test_scrolling_over_a_chooser_changes_nothing_on_the_handheld(page):
    from uihelpers import wheel
    wheel(page._esde_combos["psx"])
    assert idle(page) and esde_rows(page)["PlayStation"][1] == "SwanStation"
    assert ("stop", "org.es_de.frontend") not in page.controller.session.demo_device.calls


def test_arrow_keys_on_a_chooser_open_the_list_instead_of_picking(page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    combo = page._esde_combos["psx"]
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key_Down)
    assert idle(page) and esde_rows(page)["PlayStation"][1] == "SwanStation"
    assert ("stop", "org.es_de.frontend") not in page.controller.session.demo_device.calls
    combo = page._esde_combos["psx"]
    assert combo.view().isVisible()
    combo.hidePopup()


def test_typing_a_letter_on_a_chooser_picks_nothing(page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    combo = page._esde_combos["psx"]
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key_D)                 # "D" as in DuckStation
    assert idle(page) and esde_rows(page)["PlayStation"][1] == "SwanStation"
    assert ("stop", "org.es_de.frontend") not in page.controller.session.demo_device.calls


def test_several_emulators_without_their_folder_are_one_info_bar_message(page):
    page.choose_esde("ps2", "ARMSX2 (Standalone)")
    assert idle(page)
    page.controller.session.demo_device.fake_grants["com.armsx2"] = ["/storage/1A2B-3C4D/bios"]
    page.controller.load_emulators()
    assert idle(page) and wait_until(lambda: "2 emulators" in page.infobar.label.text(), 5)
    assert "PSP" in page.infobar.label.text() and "PlayStation 2" in page.infobar.label.text()
