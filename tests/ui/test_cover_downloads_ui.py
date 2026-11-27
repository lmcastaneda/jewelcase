"""Downloading covers is opt-in: the preference, the menu entry, and nothing asked for unless chosen."""
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QColor, QImage

from jewelcase.core import config, covers

from uihelpers import make_window, wait_until

KEY = ("ps2", "Northwind Saga (USA)")           # the demo's handheld has no cover for it


def png() -> bytes:
    image = QImage(8, 12, QImage.Format_RGB32)
    image.fill(QColor("#cc2288"))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(data)


class Net:
    def __init__(self):
        self.asked = []

    def __call__(self, url):
        self.asked.append(url)
        return png() if "Northwind" in url else None


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    w.controller.cover_get = Net()
    yield w
    w.close()


def test_nothing_is_requested_by_default(win):
    c = win.controller
    assert c.session.cfg.download_covers is False
    c.refresh_art()
    wait_until(lambda: not c._art_busy, 5)
    assert c.cover_get.asked == [] and c.art.cover(*KEY) is None


def test_the_preference_is_off_at_first_and_applies_at_once(win):
    from jewelcase.ui import dialogs
    dlg = dialogs.open_preferences(win)
    assert not dlg.covers_check.isChecked()
    assert dlg.covers_check.text() == "Download covers for games that have none"
    hints = [l.text() for l in dlg.findChildren(QLabel) if "libretro" in l.text()]
    assert hints == ["From the libretro thumbnail collection on GitHub.\n"
                     "Each request names the game and its system, nothing else."]
    told = []
    win.controller.art_changed.connect(lambda: told.append(1))
    dlg.covers_check.setChecked(True)
    assert win.controller.session.cfg.download_covers is True
    assert wait_until(lambda: win.controller.art.cover(*KEY) is not None and told, 10)
    assert win.controller.art.cover(*KEY).parent.parent.name == "downloaded"


def test_with_the_preference_on_covers_are_fetched_after_the_handheld_is_read(win):
    c = win.controller
    c.session.cfg.download_covers = True
    c.refresh()
    assert wait_until(lambda: c.art.cover(*KEY) is not None, 10)
    assert any("Northwind" in u for u in c.cover_get.asked)


def test_the_menu_entry_downloads_once_and_says_what_came(win):
    c = win.controller
    said = []
    c.toast.connect(lambda text, *_: said.append(text))
    action = next(a for a in win.menu_button.menu().actions() if a.text() == "Download Missing Covers")
    action.trigger()
    assert wait_until(lambda: said and "downloaded" in said[-1], 10)
    assert said[-1].startswith("1 cover downloaded, ") and said[-1].endswith("without one")
    assert c.art.cover(*KEY) is not None and c.session.cfg.download_covers is False


def test_a_cover_that_arrives_is_shown_in_the_list_without_scrolling(win):
    from PySide6.QtWidgets import QApplication
    from jewelcase.ui import models
    idx = next(win.games_filter.index(i, models.COL_TITLE) for i in range(win.games_filter.rowCount())
               if win.games_filter.index(i, models.COL_TITLE).data(models.ROW_ROLE).key == KEY)
    win.table.scrollTo(idx)
    win.table.viewport().grab()
    win.controller.download_missing_covers()
    assert wait_until(lambda: win.controller.art.cover(*KEY) is not None, 10)
    # no scrolling and no update() of our own: the window repaints the list, which asks for the picture
    assert wait_until(lambda: win.posters.pixmap("cover", *KEY) is not None, 10)


def test_the_preference_is_saved_and_read_back(tmp_path):
    cfg = config.Config(download_covers=True)
    config.save(cfg, tmp_path / "c.toml")
    assert config.load(tmp_path / "c.toml").download_covers is True
    config.save(config.Config(), tmp_path / "d.toml")
    assert config.load(tmp_path / "d.toml").download_covers is False


def test_the_review_says_which_added_games_get_their_cover_in_es_de(win):
    from jewelcase.ui.dialogs import ReviewDialog
    c = win.controller
    row = next(r for r in c.rows if r.state == "library_only")
    c.set_want([row.key], True)
    assert c.art.store_download(row.system, row.stem, png(), ".png")
    c.reload()
    dlg = ReviewDialog(c.plan)
    notes = [dlg._sections["add"].child(i).text(3) for i in range(dlg.section_count("add"))]
    assert notes == ["its cover is copied to ES-DE if it has none"]


def test_after_a_failed_download_the_automatic_run_waits_but_the_menu_entry_still_tries(win):
    c = win.controller
    asked = []

    def broken(url):
        asked.append(url)
        raise OSError("http 503")
    c.cover_get = broken
    c.session.cfg.download_covers = True
    c.refresh_art()
    assert wait_until(lambda: not c._art_busy and not c._covers_busy, 10)
    assert len(asked) == 1
    c.refresh_art()
    wait_until(lambda: not c._art_busy and not c._covers_busy, 10)
    assert len(asked) == 1                                      # not again at once
    c.download_missing_covers()
    assert wait_until(lambda: len(asked) == 2 and not c._covers_busy, 10)


def test_closing_the_window_stops_a_long_download_run(win):
    import threading
    c = win.controller
    c.session.cfg.download_covers = False
    first, release, asked = threading.Event(), threading.Event(), []

    def slow(url):
        asked.append(url)
        first.set()
        release.wait(10)
        return None
    c.cover_get = slow
    c.download_missing_covers()
    assert wait_until(first.is_set, 10)
    win.close()
    release.set()
    assert wait_until(lambda: not c._covers_busy, 10)
    assert len(asked) == 1
