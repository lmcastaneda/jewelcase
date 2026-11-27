"""Converting in the window: the preference, the progress words and what the user is told."""
import shutil
import zipfile

import pytest

from jewelcase.core.inbox import ImportProgress
from jewelcase.ui import dialogs

from uihelpers import make_window, wait_until


@pytest.fixture
def win(qapp, tmp_path):
    w = make_window(tmp_path)
    yield w
    wait_until(lambda: not w.controller.inbox_busy, 5)
    w.close()


def put_archive(win, tmp_path, name):
    """A zip with one file nothing can play, aged so the downloads folder takes it as finished."""
    import os
    import time
    path = tmp_path / "demo" / "inbox" / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Odd Disc (USA).bin", b"x" * 4096)
    os.utime(path, (time.time() - 3600,) * 2)
    win.controller.inbox_items = win.controller.inbox.scan()
    win.controller.inbox_changed.emit()


def _progress(*args):
    return ImportProgress(*args)


def test_the_preference_to_convert_on_import_is_on_and_applies_at_once(win):
    c = win.controller
    dlg = dialogs.open_preferences(win)
    assert c.session.cfg.convert_on_import is True
    assert dlg.convert_check.isChecked() and dlg.convert_check.text() == "Convert archives when they are imported"
    dlg.convert_check.setChecked(False)
    assert c.session.cfg.convert_on_import is False
    dlg.convert_check.setChecked(True)
    assert c.session.cfg.convert_on_import is True
    dlg.close()


def test_the_inbox_page_and_the_header_say_what_is_being_made(win):
    page, c = win.pages["inbox"], win.controller
    for phase, word in (("unpacking", "Unpacking"), ("compressing", "Compressing"), ("verifying_game", "Verifying")):
        c.import_progress.emit(_progress(phase, "Shadow (Europe).7z", 1, 1, 500, 1000))
        assert page.status.text() == f"{word} Shadow (Europe).7z · 50%"
        assert win.capsule.sub.text() == f"{word} Shadow (Europe).7z"
        assert not win.capsule.bar.isHidden() and win.capsule.bar.value() == 500
    c.import_progress.emit(_progress("copying", "Shadow (Europe).7z", 1, 1, 500, 1000))
    assert win.capsule.sub.text() == "Importing Shadow (Europe).7z"
    c.import_progress.emit(None)
    assert win.capsule.bar.isHidden()


@pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")
def test_an_import_whose_conversion_fails_is_kept_and_the_user_is_told_once(win, tmp_path):
    name = "Odd Disc (USA).zip"
    put_archive(win, tmp_path, name)
    c, page = win.controller, win.pages["inbox"]
    told = []
    c.toast.connect(lambda text, action, callback: told.append((text, action)))
    page.set_system(name, "ps2")
    page.import_one(name)
    assert wait_until(lambda: not c.inbox_busy and told, 20)
    assert told == [("Odd Disc (USA) was imported, but could not be converted: The archive holds a .bin file, "
                     "which is not a format the handheld's PlayStation 2 emulators take.", "Undo")]
    assert (tmp_path / "demo" / "library" / "ps2" / name).exists()


@pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")
def test_with_the_preference_off_an_archive_is_only_imported(win, tmp_path):
    name = "Odd Disc (USA).zip"
    put_archive(win, tmp_path, name)
    c, page = win.controller, win.pages["inbox"]
    c.session.cfg.convert_on_import = False
    told = []
    c.toast.connect(lambda text, action, callback: told.append(text))
    page.set_system(name, "ps2")
    page.import_one(name)
    assert wait_until(lambda: not c.inbox_busy and told, 20)
    assert told == ["Odd Disc (USA) was imported"]


# ---------------------------------------------------------------- a disc image the library already has
LOOSE = ("ps2", "Loose Disc (USA)")


@pytest.fixture
def loose(win, tmp_path):
    (tmp_path / "demo" / "library" / "ps2" / "Loose Disc (USA).iso").write_bytes(b"iso-" * 5120)
    win.controller.session.library.scan()
    win.controller._reload()
    return win


def labels(win, *keys):
    return [a.text() for a in win.build_table_menu(list(keys)).actions() if a.text()]


@pytest.fixture
def chdman_here(monkeypatch):
    """Menu entries only need to know the tool is there."""
    from jewelcase.core import convert
    if not shutil.which("chdman"):
        monkeypatch.setattr(convert, "_chdman", lambda: "/bin/true")


def test_the_menu_offers_convert_to_chd_only_for_a_loose_disc_image(loose, chdman_here):
    assert "Convert to CHD" in labels(loose, LOOSE)
    assert "Convert to CHD" not in labels(loose, ("ps2", "Ember & Ash (USA)"))       # already a CHD
    assert "Convert to CHD" not in labels(loose, ("gba", "Metro Fusion (USA)"))
    assert "Convert to CHD" in labels(loose, LOOSE, ("gba", "Metro Fusion (USA)"))   # for the ones it applies to


def test_the_menu_does_not_offer_it_without_chdman(loose, monkeypatch):
    from jewelcase.core import convert
    monkeypatch.setattr(convert, "_chdman", lambda: None)
    assert "Convert to CHD" not in labels(loose, LOOSE)


def test_the_entry_is_disabled_while_a_sync_or_an_import_runs(loose, chdman_here):
    c = loose.controller
    entry = lambda: next(a for a in loose.build_table_menu([LOOSE]).actions() if a.text() == "Convert to CHD")
    assert entry().isEnabled()
    c.inbox_busy = True
    assert not entry().isEnabled()
    c.inbox_busy, c.job_running = False, True
    assert not entry().isEnabled()
    c.job_running = False


@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")
def test_converting_runs_in_the_background_shows_progress_and_ends_with_a_toast(loose, tmp_path):
    c = loose.controller
    seen, told = [], []
    c.import_progress.connect(lambda p: seen.append(p))
    c.toast.connect(lambda text, action, callback: told.append((text, action)))
    c.convert_games([LOOSE])
    assert c.inbox_busy                                            # a sync or an import cannot start meanwhile
    assert wait_until(lambda: told, 20)
    shown = [p for p in seen if p is not None]
    assert seen[-1] is None and shown and {p.name for p in shown} == {"Loose Disc"}
    assert "compressing" in {p.phase for p in shown} and [p.done for p in shown] == sorted(p.done for p in shown)
    assert told == [("Loose Disc was converted to CHD", None)] and not c.inbox_busy
    folder = tmp_path / "demo" / "library" / "ps2"
    assert (folder / "Loose Disc (USA).chd").is_file() and (folder / "Loose Disc (USA).iso").is_file()
    row = next(r for r in c.rows if r.key == LOOSE)
    assert row.lib_unit.ext == ".chd"
    assert "Convert to CHD" not in labels(loose, LOOSE)


def test_converting_is_refused_while_a_sync_runs(loose, chdman_here):
    c = loose.controller
    told = []
    c.toast.connect(lambda text, action, callback: told.append(text))
    c.job_running = True
    c.convert_games([LOOSE])
    c.job_running = False
    assert told == ["Wait for the sync or import to finish first"] and not c.inbox_busy


def test_a_gamecube_iso_is_offered_as_rvz_when_dolphin_tool_is_there(win, tmp_path, monkeypatch):
    from jewelcase.core import convert
    (tmp_path / "demo" / "library" / "gamecube" / "Loose Cube (USA).iso").write_bytes(b"iso-" * 5120)
    win.controller.session.library.scan()
    win.controller._reload()
    key = ("gamecube", "Loose Cube (USA)")
    monkeypatch.setattr(convert, "_dolphin", lambda: None)
    assert not [t for t in labels(win, key) if t.startswith("Convert to")]
    monkeypatch.setattr(convert, "_dolphin", lambda: "/bin/true")
    assert "Convert to RVZ" in labels(win, key) and "Convert to CHD" not in labels(win, key)


def test_the_copy_line_names_the_size_of_the_copy_while_the_bar_counts_the_whole_job(win):
    from jewelcase.core.fmt import human_size
    page, c = win.pages["inbox"], win.controller
    mb = 1 << 20
    # a 100 MB archive: 200 MB of copy work out of a job (with its conversion) of 1.2 GB
    c.import_progress.emit(ImportProgress("copying", "Big.7z", 1, 1, 6 * mb, 1200 * mb, 6 * mb, 200 * mb))
    assert page.status.text() == f"Copying Big.7z · 3% of {human_size(100 * mb)}"
    assert win.pages["inbox"].bar.value() == int(1000 * 6 / 1200)
