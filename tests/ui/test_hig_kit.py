import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

from jewelcase.ui import hig

from uihelpers import wait_until


@pytest.mark.parametrize("raw, expected", [
    ("check for updates", "Check for Updates"),
    ("add to applications menu", "Add to Applications Menu"),
    ("copy to library", "Copy to Library"),
    ("open sync log", "Open Sync Log"),
    ("choose library folder…", "Choose Library Folder…"),
    ("download adb", "Download adb"),
    ("install from file…", "Install From File…"),
    ("largest on device", "Largest on Device"),
    ("not played lately", "Not Played Lately"),
    ("restore factory version", "Restore Factory Version"),
    ("back up now", "Back Up Now"),                      # "up" belongs to the verb here
    ("restore all…", "Restore All…"),
    ("what is left to", "What Is Left To"),              # last word is always capitalised
])
def test_title_case(raw, expected):
    assert hig.title_case(raw) == expected
    assert hig.is_title_case(expected)


def test_is_title_case():
    assert not hig.is_title_case("Check for updates")
    assert not hig.is_title_case("Import all ready")
    assert hig.is_title_case("Sync…") and hig.is_title_case("Pause")
    assert hig.is_title_case("Import 3 Games")
    assert hig.is_title_case("")


@pytest.fixture
def host(qapp):
    w = QWidget()
    w.resize(800, 500)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def test_toast_shows_text_and_action(host):
    toast = hig.Toast(host)
    assert not toast.isVisible()
    done = []
    toast.show_message("40 games unticked", "Undo", lambda: done.append(1))
    assert toast.isVisible() and toast.label.text() == "40 games unticked"
    assert toast.action_button.isVisible() and toast.action_button.text() == "Undo"
    assert toast.geometry().center().x() == pytest.approx(host.width() / 2, abs=2)
    assert toast.geometry().bottom() < host.height()
    toast.action_button.click()
    assert done == [1] and not toast.isVisible()


def test_toast_without_action_hides_the_button(host):
    toast = hig.Toast(host)
    toast.show_message("Flycast was disabled")
    assert toast.isVisible() and not toast.action_button.isVisible()
    toast.close_button.click()
    assert not toast.isVisible()


def test_new_toast_replaces_the_old_one(host):
    toast = hig.Toast(host)
    first = []
    toast.show_message("one", "Undo", lambda: first.append(1))
    toast.show_message("two")
    assert toast.label.text() == "two" and not toast.action_button.isVisible()
    assert len([c for c in host.children() if isinstance(c, hig.Toast)]) == 1
    assert first == []


def test_toast_dismisses_itself(host, monkeypatch):
    monkeypatch.setattr(hig.Toast, "TIMEOUT_MS", 40)
    monkeypatch.setattr(hig.Toast, "TIMEOUT_WITH_ACTION_MS", 40)
    toast = hig.Toast(host)
    toast.show_message("gone soon", "Undo", lambda: None)
    assert wait_until(lambda: not toast.isVisible(), 2)


def test_infobar_kinds_and_action(host):
    bar = hig.InfoBar()
    bar.setParent(host)
    hit = []
    bar.set_message("warning", "A sync was interrupted before it finished.", "Review and Resume…",
                    lambda: hit.append(1))
    assert not bar.isHidden() and bar.kind == "warning"
    assert bar.label.text().startswith("A sync was interrupted")
    assert bar.action_button.text() == "Review and Resume…" and not bar.action_button.isHidden()
    bar.action_button.click()
    assert hit == [1]
    bar.set_message("error", "adb was not found.")
    assert bar.kind == "error" and bar.action_button.isHidden()
    with pytest.raises(ValueError):
        bar.set_message("shouting", "x")


def test_infobar_hidden_when_no_message(host):
    bar = hig.InfoBar()
    bar.setParent(host)
    assert bar.isHidden()
    bar.set_message("info", "x")
    bar.set_message(None)
    assert bar.isHidden() and bar.kind is None


def test_welcome_view_actions(host):
    view = hig.WelcomeView("No Library Yet", "Choose the folder that holds your games.")
    hit = []
    b = view.add_action("folder", "Choose Library Folder…", "Pick any folder with one folder per system.",
                        lambda: hit.append("choose"))
    assert view.title.text() == "No Library Yet" and view.subtitle.text().startswith("Choose the folder")
    assert view.actions == [b] and b.title_text == "Choose Library Folder…"
    assert b.description_text.startswith("Pick any folder")
    b.click()
    assert hit == ["choose"]
    view.clear_actions()
    assert view.actions == []
    view.set_text("No New Downloads", "Nothing yet.")
    assert view.title.text() == "No New Downloads"


def test_icons_render_without_a_system_theme(qapp):
    for name in hig.ICONS:
        assert not hig.icon(name).isNull(), name


def test_message_dialog_button_order_and_default(host):
    dlg = hig.MessageDialog(host, "Remove ARMSX2 from the device?", "This deletes the app and its private data.")
    assert dlg.windowTitle().strip() == ""
    assert dlg.primary.text() == "Remove ARMSX2 from the device?"
    keep = QCheckBox("Keep its data on the device")
    dlg.set_extra(keep)
    remove = dlg.add_button("Remove", "destructive")
    cancel = dlg.add_button("Cancel", "cancel")
    other = dlg.add_button("Open Help", "other")
    assert [b.text() for b in dlg.buttons_in_order()] == ["Open Help", "Cancel", "Remove"]
    assert cancel.isDefault() and not remove.isDefault()          # destructive is never the default
    assert remove.objectName() == "destructive"
    with pytest.raises(ValueError):
        dlg.add_button("Also Remove", "suggested")                # one coloured button per dialog
    dlg.show()
    remove.click()
    assert dlg.clicked_role == "destructive" and not dlg.isVisible()

    dlg2 = hig.MessageDialog(host, "Add 3 and remove 40 games?")
    dlg2.add_button("Cancel", "cancel")
    go = dlg2.add_button("Sync", "suggested")
    assert go.isDefault() and go.objectName() == "suggested"
    dlg2.show()
    dlg2.reject()
    assert dlg2.clicked_role == "cancel"
    assert other.text() == "Open Help"


def test_welcome_action_is_tall_enough_for_both_lines(host):
    view = hig.WelcomeView("No Library Yet", "Choose the folder that holds your games.")
    view.setParent(host)
    b = view.add_action("folder", "Choose Library Folder…", "Pick any folder with one folder per system.",
                        lambda: None)
    view.resize(700, 400)
    view.show()
    QApplication.processEvents()
    labels = b.findChildren(type(view.title))
    assert b.height() >= sum(l.sizeHint().height() for l in labels if l.text()) + 12
