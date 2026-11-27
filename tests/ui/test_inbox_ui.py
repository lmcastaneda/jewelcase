import pytest

from jewelcase.ui.views import InboxView

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    wait_until(lambda: not w.controller.inbox_busy, 5)
    w.close()


def test_inbox_view_lists_and_imports(win, tmp_path):
    page = win.pages["inbox"]
    assert isinstance(page, InboxView)
    assert page.item_count() == 3 and win.sidebar_count("inbox") == 3
    assert page.systems() == {"Violet Horizon (USA).gba": "gba", "Rally Kings (USA).rvz": "gamecube",
                              "Unknown Disc (USA).chd": None}
    page.import_ready()
    c = win.controller
    assert wait_until(lambda: not c.inbox_busy and page.item_count() == 1, 20)
    lib = tmp_path / "demo" / "library"
    assert (lib / "gba" / "Violet Horizon (USA).gba").exists()
    assert (lib / "gamecube" / "Rally Kings (USA).rvz").exists()
    assert [p.name for p in (tmp_path / "demo" / "inbox").iterdir()] == ["Unknown Disc (USA).chd"]
    row = next(r for r in c.rows if r.stem == "Violet Horizon (USA)")
    assert row.state == "library_only" and row.want is True
    assert win.sidebar_count("inbox") == 1
    assert "+ 2 games" in win.pending_label.text()


def test_inbox_pick_system_then_import(win, tmp_path):
    page = win.pages["inbox"]
    name = "Unknown Disc (USA).chd"
    page.import_one(name)                         # no system chosen yet: nothing happens, and it says why
    assert wait_until(lambda: "choose a system" in page.status.text().lower(), 5)
    page.set_system(name, "ps2")
    page.import_one(name)
    assert wait_until(lambda: (tmp_path / "demo" / "library" / "ps2" / name).exists(), 20)
    assert wait_until(lambda: page.item_count() == 2, 5)


def test_inbox_page_shows_folder(win, tmp_path):
    win.select_scope("inbox")
    assert win.stack.currentWidget() is win.pages["inbox"]
    assert str(tmp_path / "demo" / "inbox") in win.pages["inbox"].folder_label.text()


# ------------------------------------------------------------ progress
GB = 1024 ** 3


def _progress(*args):
    from jewelcase.core.inbox import ImportProgress
    return ImportProgress(*args)


def test_import_progress_line_and_bar(win):
    from jewelcase.core.fmt import human_size
    page, c = win.pages["inbox"], win.controller
    assert page.bar.isHidden()
    c.import_progress.emit(_progress("copying", "God of War II (USA).iso", 2, 3, 5 * GB, 20 * GB))
    assert not page.bar.isHidden() and (page.bar.maximum(), page.bar.value()) == (1000, 250)
    assert page.status.text() == f"Copying God of War II (USA).iso · file 2 of 3 · 25% of {human_size(10 * GB)}"
    c.import_progress.emit(_progress("verifying", "One.gba", 1, 1, 3000, 4000))
    assert page.status.text() == f"Verifying One.gba · 75% of {human_size(2000)}"      # one file: no "file 1 of 1"
    c.import_progress.emit(_progress("checking", "Pack.7z", 1, 2, 0, 4000))
    assert page.bar.maximum() == 0                                  # busy: checking an archive has no byte count
    assert page.status.text() == "Checking Pack.7z · file 1 of 2"
    c.import_progress.emit(None)
    assert page.bar.isHidden()


def test_header_shows_an_import(win):
    c = win.controller
    c.import_progress.emit(_progress("copying", "A.iso", 1, 2, 50, 200))
    assert not win.capsule.bar.isHidden() and (win.capsule.bar.maximum(), win.capsule.bar.value()) == (1000, 250)
    assert win.capsule.sub.text().startswith("Importing ")
    c.import_progress.emit(None)
    assert win.capsule.bar.isHidden() and "Importing" not in win.capsule.sub.text()


def test_header_keeps_showing_a_sync_over_an_import(win):
    from jewelcase.core.sync import Progress
    c = win.controller
    c.job_progress.emit(Progress(1, 0, 2, "push", "Some Game", 10, 100, "running"))
    shown = win.capsule.sub.text()
    c.import_progress.emit(_progress("copying", "A.iso", 1, 2, 50, 200))
    assert win.capsule.sub.text() == shown and win.capsule.bar.value() == 100
    win._on_finished("done")


def test_importing_reports_progress_and_clears_it(win):
    from PySide6.QtCore import QObject

    class Spy(QObject):
        def __init__(self, page):
            super().__init__()
            self.page, self.seen = page, []

        def on(self, p):
            self.seen.append((p, self.page.bar.isHidden(), self.page.status.text()))

    page, c = win.pages["inbox"], win.controller
    spy = Spy(page)
    c.import_progress.connect(spy.on)
    page.import_ready()
    assert wait_until(lambda: not c.inbox_busy and spy.seen and spy.seen[-1][0] is None, 20)
    shown = [s for s in spy.seen if s[0] is not None]
    assert shown and all(not hidden for _, hidden, _ in shown)
    assert any(text.startswith("Copying ") for _, _, text in shown)
    assert shown[-1][0].done == shown[-1][0].total                 # the last report is not lost to throttling
    assert page.bar.isHidden()


def test_scrolling_over_a_system_chooser_does_not_change_the_system(win):
    from uihelpers import wheel
    page = win.pages["inbox"]
    before = page.systems()
    for combo in page._combos.values():
        wheel(combo)
        wheel(combo, steps=1)
        wheel(combo, steps=1)
    assert page.systems() == before
