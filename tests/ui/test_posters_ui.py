"""The poster grid: the same games as the list, shown by their covers."""
import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest

from jewelcase.core import systems
from jewelcase.core.fmt import human_size
from jewelcase.ui import models, posters
from jewelcase.ui.shell import WindowState

from uihelpers import make_window, wait_until

MEDIA = "device/sdcard/ES-DE/downloaded_media"


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.close()


def index_of(win, predicate):
    """The grid's index of the first game matching, scrolled into view."""
    for i in range(win.games_filter.rowCount()):
        idx = win.games_filter.index(i, models.COL_TITLE)
        if predicate(idx.data(models.ROW_ROLE)):
            win.grid.scrollTo(idx)
            return idx
    raise AssertionError("no such game")


def row_at(idx):
    return idx.data(models.ROW_ROLE)


def game(win, key):
    return next(r for r in win.controller.rows if r.key == key)


# ---------------------------------------------------------------- switching views
def test_the_list_is_shown_until_the_grid_is_chosen(win):
    assert win.games_view == "list" and win.stack.currentWidget() is win.table
    assert win.list_button.isChecked() and not win.grid_button.isChecked()
    win.grid_button.click()
    assert win.games_view == "grid" and win.stack.currentWidget() is win.grid
    assert win.grid_button.isChecked() and not win.list_button.isChecked()
    win.list_button.click()
    assert win.games_view == "list" and win.stack.currentWidget() is win.table


def test_the_view_buttons_say_what_they_do(win):
    assert win.list_button.toolTip() == "View as List (Ctrl+1)" and win.grid_button.toolTip() == "View as Grid (Ctrl+2)"
    assert win.list_button.accessibleName() == "View as List" and win.grid_button.accessibleName() == "View as Grid"


def test_the_grid_shows_the_games_the_list_shows(win):
    win.set_games_view("grid")
    assert win.grid.model() is win.games_filter
    assert win.grid.model().rowCount() == len(win.controller.rows)
    win.select_scope("system:ps2")
    assert win.stack.currentWidget() is win.grid
    assert win.grid.model().rowCount() == len([r for r in win.controller.rows if r.system == "ps2"])
    win.search.setText("citadel")
    assert win.grid.model().rowCount() == 1


def test_the_view_buttons_are_only_for_games(win):
    win.select_scope("device:emulators")
    assert not win.list_button.isEnabled() and not win.grid_button.isEnabled()
    win.select_scope("all")
    assert win.list_button.isEnabled() and win.grid_button.isEnabled()


def test_the_grid_is_still_chosen_after_visiting_another_page(win):
    win.set_games_view("grid")
    win.select_scope("device:emulators")
    win.select_scope("smart:never_played")
    assert win.stack.currentWidget() is win.grid and win.search.isEnabled()


def test_the_chosen_view_is_remembered(win, qapp, tmp_path):
    win.set_games_view("grid")
    state = WindowState(tmp_path / "state.ini")
    state.save(win)
    other = make_window(tmp_path / "second")
    try:
        state.restore(other)
        assert other.games_view == "grid" and other.stack.currentWidget() is other.grid
    finally:
        other.close()


# ---------------------------------------------------------------- pictures
def test_covers_from_the_handheld_are_shown(win):
    key = ("ps2", "Clockwork Citadel (USA)")
    assert win.controller.art.cover(*key) is not None
    win.set_games_view("grid")
    assert wait_until(lambda: win.posters.pixmap("cover", *key) is not None, 5)
    pix = win.posters.pixmap("cover", *key)
    assert not pix.isNull() and pix.height() <= 2 * posters.COVER_H and pix.width() <= 2 * posters.COVER_W


def test_a_game_without_a_cover_still_gets_a_tile(win):
    key = ("ps2", "Northwind Saga (USA)")                   # the demo's handheld has no cover for it
    assert win.controller.art.cover(*key) is None
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.key == key)
    assert win.posters.pixmap("cover", *key) is None
    assert win.grid.visualRect(idx).isValid() and not win.grid.grab().isNull()


def test_a_cover_scraped_later_shows_up_when_the_handheld_is_read_again(win, tmp_path):
    key = ("ps2", "Northwind Saga (USA)")
    win.set_games_view("grid")
    told = []
    win.controller.art_changed.connect(lambda: told.append(1))
    cover = tmp_path / "demo" / MEDIA / "ps2/covers/Northwind Saga (USA).png"
    cover.write_bytes((tmp_path / "demo" / MEDIA / "ps2/covers/Clockwork Citadel (USA).png").read_bytes())
    win.controller.refresh_art()
    assert wait_until(lambda: told and win.posters.pixmap("cover", *key) is not None, 10)


def test_a_cover_that_is_not_a_picture_is_treated_as_missing(win, tmp_path):
    key = ("ps2", "Clockwork Citadel (USA)")
    win.controller.art.cover(*key).write_bytes(b"not a picture at all")
    win.set_games_view("grid")
    index_of(win, lambda r: r.key == key)
    assert wait_until(lambda: win.posters.settled("cover", *key), 5)     # it was read, and found wanting
    assert win.posters.pixmap("cover", *key) is None and not win.grid.grab().isNull()
    reads = win.posters.reads
    win.grid.grab()
    wait_until(lambda: False, 0.1)
    assert win.posters.reads == reads                                    # and it is not read over and over


# ---------------------------------------------------------------- ticking and selecting
def click(win, point):
    QTest.mouseClick(win.grid.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(int(point.x()), int(point.y())))


def test_clicking_the_tick_box_on_a_poster_ticks_the_game(win):
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.state == "library_only")
    key = row_at(idx).key
    click(win, win.grid.itemDelegate().check_rect(win.grid.visualRect(idx)).center())
    assert game(win, key).want and "+ 1 game" in win.pending_label.text()
    idx = index_of(win, lambda r: r.key == key)
    click(win, win.grid.itemDelegate().check_rect(win.grid.visualRect(idx)).center())
    assert not game(win, key).want


def test_clicking_the_cover_selects_the_game_without_ticking_it(win):
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.state == "library_only")
    key = row_at(idx).key
    click(win, win.grid.visualRect(idx).center())
    assert win.selected_keys() == [key] and not game(win, key).want


def test_a_game_that_cannot_be_ticked_cannot_be_ticked_from_its_poster(win):
    c = win.controller
    (c.session.library.root / "ps2" / "Odd Format (USA).nrg").write_bytes(b"nrg")
    c.refresh_blocking()
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.stem == "Odd Format (USA)")
    click(win, win.grid.itemDelegate().check_rect(win.grid.visualRect(idx)).center())
    assert not game(win, ("ps2", "Odd Format (USA)")).want


def test_space_ticks_the_selected_posters(win):
    win.set_games_view("grid")
    picked = [r.key for r in win.controller.rows if r.state == "library_only"][:2]
    for key in picked:
        idx = index_of(win, lambda r, key=key: r.key == key)
        win.grid.selectionModel().select(idx, QItemSelectionModel.Select)
    win.grid.setFocus()
    QTest.keyClick(win.grid, Qt.Key_Space)
    assert all(game(win, key).want for key in picked)
    assert sorted(win.selected_keys()) == sorted(picked)     # and they stay selected


def test_typing_in_the_grid_starts_a_search(win):
    win.set_games_view("grid")
    win.grid.setFocus()
    QTest.keyClicks(win.grid, "lan")
    assert win.search.text() == "lan" and win.search.hasFocus()


def test_the_selection_goes_along_when_the_view_changes(win):
    win.table.selectRow(0)
    key = win.selected_keys()
    win.set_games_view("grid")
    assert win.selected_keys() == key and win.grid.selectionModel().hasSelection()
    idx = index_of(win, lambda r: r.key != key[0])
    win.grid.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect)
    win.set_games_view("list")
    rows = win.table.selectionModel().selectedRows()         # whole rows again, as the list selects them
    assert [row_at(win.games_filter.index(i.row(), models.COL_TITLE)).key for i in rows] == [row_at(idx).key]


def test_the_grid_has_the_same_menu_as_the_list(win):
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.state == "library_only")
    win.grid.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect)
    menu = win.build_table_menu(win.selected_keys())
    assert [a.text() for a in menu.actions() if a.text()][:2] == ["Tick 1 Game", "Untick 1 Game"]


def test_a_poster_says_what_the_list_says_about_the_game(win):
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.key == ("ps2", "Clockwork Citadel (USA)"))
    delegate = win.grid.itemDelegate()
    assert delegate.lines(row_at(idx)) == ("Clockwork Citadel", "On device")
    win.controller.set_want([("ps2", "Clockwork Citadel (USA)")], False)
    idx = index_of(win, lambda r: r.key == ("ps2", "Clockwork Citadel (USA)"))
    assert delegate.lines(row_at(idx)) == ("Clockwork Citadel", "Will be removed")
    tip = idx.data(Qt.ToolTipRole)
    assert "Clockwork Citadel (USA)" in tip


# ---------------------------------------------------------------- a system's page
def test_a_systems_page_is_headed_by_its_image_name_and_counts(win):
    assert win.banner.isHidden()
    win.select_scope("system:ps2")
    rows = [r for r in win.controller.rows if r.system == "ps2"]
    on = [r for r in rows if r.on_device]
    assert not win.banner.isHidden() and win.banner.title.text() == "PlayStation 2"
    assert win.banner.detail.text() == (f"{len(on)} games on the handheld  ·  {len(rows)} in all  ·  "
                                        f"{human_size(sum(r.size for r in on))} on the handheld")
    assert win.controller.art.system("ps2") is not None
    assert wait_until(lambda: win.banner.image.pixmap() is not None and not win.banner.image.pixmap().isNull(), 5)
    win.select_scope("all")
    assert win.banner.isHidden()
    win.select_scope("system:ps2")
    win.select_scope("device:emulators")
    assert win.banner.isHidden()


def test_a_system_without_an_image_is_headed_by_its_name_alone(win):
    assert win.controller.art.system("n64") is None          # the demo's ES-DE has no image for it
    win.select_scope("system:n64")
    assert not win.banner.isHidden() and win.banner.title.text() == systems.get("n64").name
    assert win.banner.image.isHidden()


def test_the_banner_follows_the_games(win):
    win.select_scope("system:ps2")
    total = len([r for r in win.controller.rows if r.system == "ps2"])
    assert f"{total} in all" in win.banner.detail.text()
    (win.controller.session.library.root / "ps2" / "Brand New (USA).chd").write_bytes(b"new")
    win.controller.refresh_blocking()
    assert f"{total + 1} in all" in win.banner.detail.text()


# ---------------------------------------------------------------- how it looks
def test_the_view_button_that_is_down_looks_down(win):
    from jewelcase.ui import style
    sheet = win.window().styleSheet() or __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.instance().styleSheet()
    assert "QToolButton#headerButton:checked" in sheet
    assert style.tokens()["sel"] in sheet.split("QToolButton#headerButton:checked")[1].split("}")[0]


def test_posters_spread_over_the_whole_width(win):
    win.set_games_view("grid")
    win.resize(1180, 760)
    wait_until(lambda: False, 0.05)
    grid = win.grid
    width = grid.viewport().width()
    columns = max(1, width // posters.TILE_W)
    first, last = grid.visualRect(grid.model().index(0, models.COL_TITLE)), \
        grid.visualRect(grid.model().index(columns - 1, models.COL_TITLE))
    assert first.top() == last.top()                                 # they share the first row
    assert last.right() >= width - posters.TILE_W // 2               # and the row reaches the right edge
    assert grid.visualRect(grid.model().index(columns, models.COL_TITLE)).top() > first.top()
    win.resize(900, 760)
    wait_until(lambda: False, 0.05)
    fewer = max(1, grid.viewport().width() // posters.TILE_W)
    assert fewer < columns
    assert grid.visualRect(grid.model().index(fewer, models.COL_TITLE)).top() > \
        grid.visualRect(grid.model().index(0, models.COL_TITLE)).top()


def test_the_tick_box_sits_beside_the_title_not_on_the_cover(win):
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.state == "library_only")
    delegate, rect = win.grid.itemDelegate(), win.grid.visualRect(idx)
    cover, check = delegate.cover_rect(rect), delegate.check_rect(rect)
    assert check.top() >= cover.bottom() and rect.contains(check)
    assert check.left() >= cover.left() - 4 and not cover.intersects(check)


# ---------------------------------------------------------------- findings of the branch review
def test_ctrl_click_takes_a_game_out_of_the_selection_after_ticking(win):
    win.set_games_view("grid")
    picked = [r.key for r in win.controller.rows if r.state == "library_only"][:2]
    for key in picked:
        idx = index_of(win, lambda r, key=key: r.key == key)
        win.grid.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
    win.grid.setFocus()
    QTest.keyClick(win.grid, Qt.Key_Space)                   # ticking selects the games again, as whole rows
    idx = index_of(win, lambda r: r.key == picked[0])
    QTest.mouseClick(win.grid.viewport(), Qt.LeftButton, Qt.ControlModifier, win.grid.visualRect(idx).center())
    assert win.selected_keys() == [picked[1]]


def test_clicking_a_poster_selects_the_whole_row_as_the_list_does(win):
    win.set_games_view("grid")
    idx = index_of(win, lambda r: r.state == "library_only")
    click(win, win.grid.visualRect(idx).center())
    assert [i.row() for i in win.grid.selectionModel().selectedRows()] == [idx.row()]


def test_only_so_many_pictures_are_kept_ready(win):
    cache = win.posters
    cache.limit = 5
    win.set_games_view("grid")
    keys = [r.key for r in win.controller.rows if win.controller.art.cover(*r.key)][:12]
    for key in keys:
        cache.pixmap("cover", *key)
        assert wait_until(lambda key=key: cache.settled("cover", *key), 5)
    assert cache.kept() <= 5
    assert cache.pixmap("cover", *keys[-1]) is not None      # the newest are the ones kept


def test_a_long_title_on_a_placeholder_card_keeps_its_beginning(win):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QFont, QFontMetrics
    font = QFont(win.font())
    font.setBold(True)
    long = " ".join(["Peter Jackson's King Kong - The Official Game of the Movie"] * 4)
    box = QRect(0, 0, posters.COVER_W - 24, posters.COVER_H - 68)
    shown = posters.fit_title(long, font, box.width(), box.height())
    assert shown.startswith("Peter Jackson's King Kong") and shown.endswith("…") and len(shown) < len(long)
    wrapped = QFontMetrics(font).boundingRect(box, Qt.AlignCenter | Qt.TextWordWrap, shown)
    assert wrapped.height() <= box.height()
    assert posters.fit_title("Skyline Drift", font, box.width(), box.height()) == "Skyline Drift"
