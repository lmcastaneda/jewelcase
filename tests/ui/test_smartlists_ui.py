import pytest
from PySide6.QtCore import Qt

from jewelcase.core import catalog, config
from jewelcase.core.smartlists import UserList
from jewelcase.ui import shell

from uihelpers import make_window

BIG = UserList("user:big-ones", "Big Ones", size="at_least", size_mb=30)
PS2 = UserList("user:ps2-games", "PS2 Games", systems=("ps2",), sort="size", descending=True)


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    w.controller.session.config_path = tmp_path / "config.toml"      # lists are written here
    yield w
    w.close()


def with_lists(win, *lists):
    win.controller.session.cfg.user_lists = list(lists)
    win.controller.reload()


def section(win, name):
    top = next(win.sidebar.topLevelItem(i) for i in range(win.sidebar.topLevelItemCount())
               if win.sidebar.topLevelItem(i).text(0) == name)
    return [(top.child(j).text(0), top.child(j).text(1), top.child(j).data(0, Qt.UserRole))
            for j in range(top.childCount())]


def ids(win):
    return [i for _, _, i in section(win, "Smart Lists")]


def pick(combo, data):
    combo.setCurrentIndex(combo.findData(data))


# ---------------------------------------------------------------- the sidebar
def test_the_sidebar_shows_the_built_in_lists_then_the_owners_then_a_way_to_make_one(win):
    with_lists(win, BIG, PS2)
    assert ids(win) == ["smart:never_played", "smart:not_played", "smart:most_played", "smart:largest",
                        "smart:device_only", "smart:user:big-ones", "smart:user:ps2-games", None]
    assert section(win, "Smart Lists")[-1][0] == "New Smart List…"


def test_an_owners_list_has_a_count_like_the_others(win):
    with_lists(win, BIG)
    cfg, rows = win.controller.session.cfg, win.controller.rows
    want = len(catalog.smart(rows, "user:big-ones", cfg))
    assert 0 < want < len(rows)
    assert win.sidebar_count("smart:user:big-ones") == want
    assert ("Big Ones", str(want), "smart:user:big-ones") in section(win, "Smart Lists")


def test_choosing_an_owners_list_shows_its_games(win):
    with_lists(win, PS2)
    win.select_scope("smart:user:ps2-games")
    assert win.games_filter.rowCount() == len([r for r in win.controller.rows if r.system == "ps2"])
    assert win.windowTitle() == "PS2 Games — Jewelcase"
    head = win.table.horizontalHeader()                       # the list's own sort is applied
    assert head.sortIndicatorSection() == 3 and head.sortIndicatorOrder() == Qt.DescendingOrder


def test_clicking_new_smart_list_opens_the_dialog_and_keeps_the_view(win):
    win.select_scope("system:gba")
    new = next(i for i in win._items() if i.text(0) == "New Smart List…")
    win.sidebar.itemClicked.emit(new, 0)
    assert win._list_dialog.isVisible()
    assert win._scope == "system:gba"
    win._list_dialog.reject()


# ---------------------------------------------------------------- the dialog
def test_the_dialog_follows_the_dialog_rules(win):
    dlg = win.new_smart_list()
    assert dlg.windowTitle().strip() == ""
    assert dlg.primary.text() == "Which games belong in this list?"
    assert [b.text() for b in dlg.buttons_in_order()] == ["Cancel", "Create List"]
    assert dlg.buttons_in_order()[1].objectName() == "suggested"
    assert not dlg.create_button.isEnabled()                  # a list needs a name
    dlg.name_edit.setText("Mine")
    assert dlg.create_button.isEnabled()
    dlg.reject()


def test_editing_says_save(win):
    with_lists(win, BIG)
    dlg = win.edit_smart_list("user:big-ones")
    assert [b.text() for b in dlg.buttons_in_order()] == ["Cancel", "Save"]
    assert dlg.name_edit.text() == "Big Ones" and dlg.size_combo.currentData() == "at_least"
    assert dlg.size_spin.value() == 30
    dlg.reject()


def test_the_dialog_counts_the_games_that_match_as_you_go(win):
    rows = win.controller.rows
    dlg = win.new_smart_list()
    assert dlg.match_label.text() == f"{len(rows)} games match"
    dlg.name_edit.setText("Mine")
    pick(dlg.device_combo, True)
    on_device = len([r for r in rows if r.on_device])
    assert dlg.match_label.text() == f"{on_device} games match"
    dlg.title_edit.setText("zzzz nothing")
    assert dlg.match_label.text() == "No games match"
    dlg.title_edit.setText("")
    dlg.systems_list.item(0).setCheckState(Qt.Checked)
    first = dlg.systems_list.item(0).data(Qt.UserRole)
    n = len([r for r in rows if r.on_device and r.system == first])
    assert dlg.match_label.text() == ("1 game matches" if n == 1 else f"{n} games match")
    dlg.reject()


def test_a_name_another_list_has_is_not_accepted(win):
    with_lists(win, BIG)
    dlg = win.new_smart_list()
    dlg.name_edit.setText("big ones")
    assert not dlg.create_button.isEnabled() and dlg.hint.text() == "Another list already has this name."
    dlg.name_edit.setText("Big Ones 2")
    assert dlg.create_button.isEnabled() and dlg.hint.text() == ""
    dlg.reject()


def test_the_unit_follows_the_kind_of_play_time_and_size(win):
    dlg = win.new_smart_list()
    assert not dlg.play_spin.isEnabled() and not dlg.size_spin.isEnabled() and not dlg.last_spin.isEnabled()
    pick(dlg.play_combo, "under")
    assert dlg.play_spin.isEnabled() and dlg.play_spin.suffix() == " minutes"
    pick(dlg.play_combo, "over")
    assert dlg.play_spin.suffix() == " hours"
    pick(dlg.play_combo, "never")
    assert not dlg.play_spin.isEnabled()
    dlg.reject()


def test_making_a_list_with_the_dialog(win, tmp_path):
    rows = win.controller.rows
    dlg = win.new_smart_list()
    dlg.name_edit.setText("Small on the handheld")
    pick(dlg.device_combo, True)
    pick(dlg.size_combo, "at_most")
    dlg.size_spin.setValue(3)
    pick(dlg.size_unit, "mb")
    pick(dlg.sort_combo, "size")
    pick(dlg.direction_combo, True)
    dlg.create_button.click()
    cfg = win.controller.session.cfg
    assert cfg.user_lists == [UserList("user:small-on-the-handheld", "Small on the handheld", on_device=True,
                                       size="at_most", size_mb=3, sort="size", descending=True)]
    want = [r for r in rows if r.on_device and r.size <= 3 * 1024 * 1024]
    assert win._scope == "smart:user:small-on-the-handheld"           # the new list is shown
    assert win.sidebar_count(win._scope) == len(want) == win.games_filter.rowCount() > 0
    assert config.load(tmp_path / "config.toml").user_lists == cfg.user_lists     # and written to the file


def test_sizes_in_gigabytes_are_kept_in_megabytes(win):
    dlg = win.new_smart_list()
    dlg.name_edit.setText("Huge")
    pick(dlg.size_combo, "at_least")
    pick(dlg.size_unit, "gb")
    dlg.size_spin.setValue(2)
    assert dlg.current_list().size_mb == 2048
    dlg.reject()
    with_lists(win, UserList("user:huge", "Huge", size="at_least", size_mb=2048))
    again = win.edit_smart_list("user:huge")
    assert again.size_unit.currentData() == "gb" and again.size_spin.value() == 2
    again.reject()


def test_cancel_changes_nothing(win):
    dlg = win.new_smart_list()
    dlg.name_edit.setText("Nope")
    dlg.cancel_button.click()
    assert win.controller.session.cfg.user_lists == []
    assert ids(win)[-2] == "smart:device_only"


def test_two_lists_with_the_same_slug_get_their_own_ids(win):
    with_lists(win, UserList("user:mine", "Mine"))
    dlg = win.new_smart_list()
    dlg.name_edit.setText("Mine!")
    dlg.create_button.click()
    assert [u.id for u in win.controller.session.cfg.user_lists] == ["user:mine", "user:mine-2"]


# ---------------------------------------------------------------- editing keeps the view
def test_a_list_being_shown_stays_shown_after_it_is_edited(win):
    with_lists(win, BIG)
    win.select_scope("smart:user:big-ones")
    before = win.games_filter.rowCount()
    dlg = win.edit_smart_list("user:big-ones")
    dlg.name_edit.setText("Really Big")
    dlg.size_spin.setValue(1000)
    dlg.create_button.click()
    assert win._scope == "smart:user:big-ones"                       # the id does not follow the name
    assert win.view_title() == "Really Big" and win.windowTitle() == "Really Big — Jewelcase"
    want = len(catalog.smart(win.controller.rows, "user:big-ones", win.controller.session.cfg))
    assert win.games_filter.rowCount() == want == win.sidebar_count("smart:user:big-ones") != before
    assert win.sidebar.currentItem().text(0) == "Really Big"


# ---------------------------------------------------------------- the right-click menu
def test_the_menu_of_an_owners_list_offers_edit_and_delete(win):
    with_lists(win, BIG)
    menu = win.build_sidebar_menu("smart:user:big-ones")
    assert [a.text() for a in menu.actions()] == ["Edit…", "Delete"]


def test_the_built_in_lists_have_no_menu(win):
    assert win.build_sidebar_menu("smart:never_played") is None
    assert win.build_sidebar_menu("all") is None
    assert win.build_sidebar_menu(None) is None


def test_the_edit_entry_opens_the_dialog(win):
    with_lists(win, BIG)
    win.build_sidebar_menu("smart:user:big-ones").actions()[0].trigger()
    assert win._list_dialog.isVisible() and win._list_dialog.name_edit.text() == "Big Ones"
    win._list_dialog.reject()


# ---------------------------------------------------------------- deleting
def test_delete_is_at_once_with_an_undo(win, tmp_path):
    with_lists(win, BIG, PS2)
    win.build_sidebar_menu("smart:user:big-ones").actions()[1].trigger()
    assert win.controller.session.cfg.user_lists == [PS2]                  # no confirmation
    assert "smart:user:big-ones" not in ids(win)
    assert win.toast.label.text() == "Deleted “Big Ones”" and win.toast.action_button.text() == "Undo"
    assert [u.id for u in config.load(tmp_path / "config.toml").user_lists] == ["user:ps2-games"]
    win.toast.action_button.click()
    assert win.controller.session.cfg.user_lists == [BIG, PS2]               # back where it was
    assert ids(win)[5:7] == ["smart:user:big-ones", "smart:user:ps2-games"]
    assert [u.id for u in config.load(tmp_path / "config.toml").user_lists] == ["user:big-ones", "user:ps2-games"]


def test_the_view_of_a_deleted_list_falls_back_to_all_games_and_undo_brings_it_back(win):
    with_lists(win, BIG)
    win.select_scope("smart:user:big-ones")
    win.delete_smart_list("user:big-ones")
    assert win._scope == "all" and win.windowTitle() == "All Games — Jewelcase"
    assert win.games_filter.rowCount() == len(win.controller.rows)
    win.toast.action_button.click()
    assert win._scope == "smart:user:big-ones"


def test_deleting_a_list_that_is_not_shown_leaves_the_view_alone(win):
    with_lists(win, BIG)
    win.select_scope("system:gba")
    win.delete_smart_list("user:big-ones")
    assert win._scope == "system:gba"
    win.toast.action_button.click()
    assert win._scope == "system:gba"


# ---------------------------------------------------------------- the remembered view
def test_a_shown_list_is_remembered_across_launches(win, tmp_path, qapp):
    with_lists(win, BIG)
    win.select_scope("smart:user:big-ones")
    store = shell.WindowState(tmp_path / "window.ini")
    store.save(win)
    other = make_window(tmp_path / "second")
    try:
        other.controller.session.cfg.user_lists = [BIG]
        other.controller.reload()
        store.restore(other)
        assert other._scope == "smart:user:big-ones"
    finally:
        other.close()


def test_a_remembered_list_that_no_longer_exists_opens_all_games(win, tmp_path):
    ini = tmp_path / "window.ini"
    ini.write_text("[window]\nscope=smart:user:long-gone\n")
    shell.WindowState(ini).restore(win)
    assert win._scope == "all"


def test_a_remembered_list_that_no_longer_exists_is_ignored_at_launch(win, tmp_path):
    win._restore_scope = "smart:user:long-gone"
    win._apply_restored_view()
    assert win._scope == "all"


def test_choosing_a_list_that_is_gone_shows_all_games(win):
    win.select_scope("smart:user:long-gone")
    assert win._scope == "all" and win.games_filter.rowCount() == len(win.controller.rows)


# ---------------------------------------------------------------- undo with a name taken in between, and the sort
def test_undo_of_a_delete_when_a_list_with_that_name_was_made_since_restores_it_under_a_free_name(win):
    with_lists(win, BIG)
    win.delete_smart_list("user:big-ones")
    undo = win.toast.action_button
    win._save_smart_list(UserList("", "Big Ones", title_has="zelda"), show=False)      # same name, same slug
    win.toast.hide()
    undo.click()
    lists = win.controller.session.cfg.user_lists
    assert [u.name for u in lists] == ["Big Ones 2", "Big Ones"]       # back at its old place
    assert len({u.id for u in lists}) == 2
    restored = lists[0]
    assert (restored.size, restored.size_mb) == ("at_least", 30)
    assert "Big Ones 2" in win.toast.label.text()


def test_a_list_without_its_own_sort_goes_back_to_the_default_sort(win):
    with_lists(win, BIG, PS2)
    win.select_scope("smart:user:ps2-games")
    head = win.table.horizontalHeader()
    assert head.sortIndicatorSection() == 3
    win.select_scope("smart:user:big-ones")
    assert head.sortIndicatorSection() == 1 and head.sortIndicatorOrder() == Qt.AscendingOrder     # Title


def test_an_unreadable_settings_file_is_said_in_the_window(win):
    win.controller.session.cfg.unreadable = True
    win._update_infobar()
    assert win.infobar.kind == "info" and win.infobar.label.text() == (
        "The settings file could not be read, so the defaults are in use. "
        "The unreadable file was kept as config.toml.broken.")
