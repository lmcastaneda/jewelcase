"""Small covers at the start of each Title cell in the list."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from jewelcase.core import systems
from jewelcase.ui import models, posters

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    w.close()


def index_of(win, key):
    for i in range(win.games_filter.rowCount()):
        idx = win.games_filter.index(i, models.COL_TITLE)
        if idx.data(models.ROW_ROLE).key == key:
            win.table.scrollTo(idx)
            return idx
    raise AssertionError("no such game")


def cover_pixel(win, idx):
    """The colour at the middle of the small cover box of a row, as painted."""
    rect = win.table.visualRect(idx)
    box = posters.ListCoverDelegate.cover_rect(rect)
    image = win.table.viewport().grab().toImage()
    return image.pixelColor(box.center())


def test_list_rows_are_tall_enough_for_a_small_cover(win):
    assert win.table.verticalHeader().defaultSectionSize() == 34
    assert isinstance(win.table.itemDelegateForColumn(models.COL_TITLE), posters.ListCoverDelegate)
    assert (posters.LIST_W, posters.LIST_H) == (22, 30)


def test_a_game_without_a_cover_gets_a_card_in_its_systems_colour(win):
    row = next(r for r in win.controller.rows if r.on_device and win.controller.art.cover(r.system, r.stem) is None)
    idx = index_of(win, row.key)
    colour = QColor(systems.get(row.system).color)
    got = cover_pixel(win, idx)
    assert abs(got.red() - colour.red()) < 6 and abs(got.green() - colour.green()) < 6


def test_a_cover_is_painted_once_it_has_been_read(win):
    key = ("ps2", "Clockwork Citadel (USA)")
    idx = index_of(win, key)
    win.table.viewport().grab()
    assert wait_until(lambda: win.posters.settled("cover", *key), 5)
    colour = QColor(systems.get("ps2").color)
    got = cover_pixel(win, idx)
    assert (got.red(), got.green(), got.blue()) != (colour.red(), colour.green(), colour.blue())
    assert win.posters.pixmap("cover", *key) is not None


def test_the_list_repaints_when_pictures_arrive(win):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication

    class Count(QObject):
        paints = 0

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Paint:
                Count.paints += 1
            return False

    spy = Count()
    viewport = win.table.viewport()
    viewport.installEventFilter(spy)
    QApplication.processEvents()
    Count.paints = 0
    win.controller.art_changed.emit()
    QApplication.processEvents()
    viewport.removeEventFilter(spy)
    assert Count.paints > 0


def test_sorting_and_selection_and_ticking_still_work(win):
    win.table.sortByColumn(models.COL_TITLE, Qt.DescendingOrder)
    titles = [win.games_filter.index(i, models.COL_TITLE).data() for i in range(win.games_filter.rowCount())]
    assert titles == sorted(titles, key=str.lower, reverse=True)
    idx = index_of(win, ("ps2", "Clockwork Citadel (USA)"))
    win.table.selectRow(idx.row())
    assert win.selected_keys() == [("ps2", "Clockwork Citadel (USA)")]
    lib = next(r for r in win.controller.rows if r.state == "library_only")
    idx = index_of(win, lib.key)
    check = win.games_filter.index(idx.row(), models.COL_CHECK)
    QTest.mouseClick(win.table.viewport(), Qt.LeftButton, Qt.NoModifier, win.table.visualRect(check).center())
    assert next(r for r in win.controller.rows if r.key == lib.key).want


def test_typing_in_the_list_still_starts_a_search(win):
    win.table.setFocus()
    QTest.keyClicks(win.table, "lan")
    assert win.search.text() == "lan"


def test_a_game_not_on_the_handheld_is_drawn_paler(win):
    row = next(r for r in win.controller.rows if r.state == "library_only" and not r.want
               and win.controller.art.cover(r.system, r.stem) is None)
    colour = QColor(systems.get(row.system).color)
    got = cover_pixel(win, index_of(win, row.key))
    assert (got.red(), got.green(), got.blue()) != (colour.red(), colour.green(), colour.blue())
    assert got.red() > 0 or got.green() > 0 or got.blue() > 0
