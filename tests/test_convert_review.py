"""Review findings in conversion: sheet parsing, files nobody names, placing files safely, cost, disc order."""
import shutil
import subprocess

import pytest

from jewelcase.core import convert
from jewelcase.core.db import Store
from jewelcase.core.games import cue_files
from jewelcase.core.library import Library
from test_convert import ISO, chdman, no_chdman, pack  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")

GAME = "Final Quest (USA)"


def make_lib(tmp_path, system, archive, members, fmt="7z"):
    root = tmp_path / "lib"
    pack(root / system, archive, members, fmt)
    library = Library(root, Store(":memory:"))
    library.scan()
    library.cache = tmp_path / "cache"
    return library


# ---------------------------------------------------------------- 1: reading sheets
def test_a_cue_sheet_with_a_byte_order_mark_still_names_its_first_file():
    text = '﻿FILE "Game (Track 1).bin" BINARY\r\n  TRACK 01 MODE2/2352\r\nFILE "Game (Track 2).bin" BINARY\r\n'
    assert cue_files(text) == ["Game (Track 1).bin", "Game (Track 2).bin"]


def test_a_cue_sheet_with_windows_line_ends_names_unquoted_files_cleanly():
    assert cue_files("FILE track01.bin BINARY\r\nFILE track02.bin BINARY\r\n") == ["track01.bin", "track02.bin"]


def test_a_gdi_line_with_an_apostrophe_or_an_unbalanced_quote_still_names_its_file():
    text = ("3\r\n1 0 4 2352 \"Rock'n Roll (Track 1).bin\" 0\r\n2 450 0 2352 track02.raw 0\r\n"
            "3 45000 4 2352 \"Odd (Track 3).bin 0\r\n")
    assert convert._gdi_refs("﻿" + text) == ["Rock'n Roll (Track 1).bin", "track02.raw", "Odd (Track 3).bin"]


def disc_cue(n, bom=False):
    return ("﻿" if bom else "") + f'FILE "Disc {n}.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n'


def test_a_multi_disc_archive_with_a_cue_that_starts_with_a_byte_order_mark_keeps_every_track(tmp_path, chdman):
    members = {"Disc 1.cue": disc_cue(1, bom=True).encode("utf-8"), "Disc 1.bin": b"one" * 100,
               "Disc 2.cue": disc_cue(2).encode(), "Disc 2.bin": b"two" * 100}
    lib = make_lib(tmp_path, "psx", GAME + ".7z", members)
    conv = convert.look(lib, "psx", GAME)
    assert conv.problem == ""
    assert [[m[0] for m in d.members] for d in conv.discs] == [["Disc 1.cue", "Disc 1.bin"], ["Disc 2.cue", "Disc 2.bin"]]


def test_a_multi_disc_archive_with_a_file_no_disc_names_is_refused(tmp_path, chdman):
    members = {"Disc 1.cue": disc_cue(1).encode(), "Disc 1.bin": b"one" * 100,
               "Disc 2.cue": disc_cue(2).encode(), "Disc 2.bin": b"two" * 100, "Stray.bin": b"x" * 50}
    lib = make_lib(tmp_path, "psx", GAME + ".7z", members)
    conv = convert.look(lib, "psx", GAME)
    assert "holds files that no disc names: Stray.bin" in conv.problem


def test_a_multi_disc_archive_whose_sheet_names_a_track_it_lacks_is_refused(tmp_path, chdman):
    members = {"Disc 1.cue": disc_cue(1).encode(), "Disc 2.cue": disc_cue(2).encode(), "Disc 2.bin": b"two" * 100}
    lib = make_lib(tmp_path, "psx", GAME + ".7z", members)
    assert "Disc 1.bin, which is not in the archive" in convert.look(lib, "psx", GAME).problem


# ---------------------------------------------------------------- 8: disc order
def test_discs_in_folders_are_ordered_by_their_whole_path_naturally(tmp_path, chdman):
    members = {}
    for n in (1, 10, 2):
        members[f"Disc {n}/game.cue"] = f'FILE "game.bin" BINARY\n'.encode()
        members[f"Disc {n}/game.bin"] = f"d{n}".encode() * 100
    lib = make_lib(tmp_path, "psx", GAME + ".7z", members)
    conv = convert.look(lib, "psx", GAME)
    assert conv.problem == ""
    assert [d.members[0][0] for d in conv.discs] == ["Disc 1/game.cue", "Disc 2/game.cue", "Disc 10/game.cue"]


# ---------------------------------------------------------------- 3: never replace
def test_a_file_that_appears_while_converting_is_not_replaced(tmp_path, chdman, monkeypatch):
    lib = make_lib(tmp_path, "ps2", "Shadow (Europe).7z", {"Shadow (Europe).iso": ISO})
    conv = convert.look(lib, "ps2", "Shadow (Europe)")
    target = lib.root / "ps2" / "Shadow (Europe).chd"
    target.write_bytes(b"made by somebody else")
    real = type(target).exists
    # the second conversion's check has already passed when the other file lands: make the check blind to it
    monkeypatch.setattr(type(target), "exists", lambda self: False if self == target else real(self))
    with pytest.raises(convert.ConvertError, match="already has a file named Shadow \\(Europe\\).chd"):
        convert.run(lib, conv, lib.cache)
    monkeypatch.undo()
    assert target.read_bytes() == b"made by somebody else"
    assert sorted(p.name for p in target.parent.iterdir()) == ["Shadow (Europe).7z", "Shadow (Europe).chd"]


# ---------------------------------------------------------------- 2: a running conversion's folder is safe
def test_starting_a_conversion_does_not_delete_the_work_folder_of_one_that_runs(tmp_path, chdman):
    cache = tmp_path / "cache"
    with convert._work_folder(cache) as mine:
        (mine / "half.bin").write_bytes(b"in use")
        convert._clear_stale(cache)
        assert (mine / "half.bin").exists()
    assert not mine.exists()


def test_a_work_folder_left_by_a_killed_run_is_cleared(tmp_path, chdman):
    cache = tmp_path / "cache" / "convert"
    old = cache / "tmpold"
    old.mkdir(parents=True)
    (old / "x").write_bytes(b"left over")
    (cache / "loose-file").write_bytes(b"x")
    convert._clear_stale(tmp_path / "cache")
    assert list(cache.iterdir()) == []


def test_a_conversion_running_while_another_starts_keeps_its_files(tmp_path, chdman):
    lib = make_lib(tmp_path, "ps2", "Shadow (Europe).7z", {"Shadow (Europe).iso": ISO})
    seen = []
    with convert._work_folder(lib.cache) as busy:
        (busy / "unpacked.iso").write_bytes(b"mine")
        convert.run(lib, convert.look(lib, "ps2", "Shadow (Europe)"), lib.cache,
                    lambda phase, done: seen.append((busy / "unpacked.iso").exists()))
        assert (busy / "unpacked.iso").read_bytes() == b"mine" and seen and all(seen)


# ---------------------------------------------------------------- 5: cost
def count_7z(monkeypatch):
    calls = []
    real = subprocess.Popen

    def spy(cmd, *a, **k):
        calls.append(cmd)
        return real(cmd, *a, **k)
    monkeypatch.setattr(convert.subprocess, "Popen", spy)
    return calls


def reads(calls):
    return [c for c in calls if c[1] == "x"]


def two_discs():
    return {"Disc 1.cue": disc_cue(1).encode(), "Disc 1.bin": b"one" * 100,
            "Disc 2.cue": disc_cue(2).encode(), "Disc 2.bin": b"two" * 100}


def test_looking_at_the_same_multi_disc_archive_again_does_not_read_its_cues_again(tmp_path, chdman, monkeypatch):
    lib = make_lib(tmp_path, "psx", GAME + ".7z", two_discs())
    calls = count_7z(monkeypatch)
    convert.look(lib, "psx", GAME)
    first = len(reads(calls))
    assert first == 2                                   # one read for each cue sheet
    convert.look(lib, "psx", GAME)
    assert len(reads(calls)) == first


def test_a_changed_archive_is_read_again(tmp_path, chdman, monkeypatch):
    lib = make_lib(tmp_path, "psx", GAME + ".7z", two_discs())
    convert.look(lib, "psx", GAME)
    path = lib.root / "psx" / (GAME + ".7z")
    path.unlink()
    pack(path.parent, path.name, {**two_discs(), "Disc 1.cue": (disc_cue(1) + "REM changed\r\n").encode()})
    calls = count_7z(monkeypatch)
    convert.look(lib, "psx", GAME)
    assert len(reads(calls)) == 2


# ---------------------------------------------------------------- 9: a missing or broken 7z is one line
def test_7z_that_cannot_be_started_is_a_problem_not_a_crash(tmp_path, chdman, monkeypatch):
    lib = make_lib(tmp_path, "psx", GAME + ".7z", two_discs())
    convert.look(lib, "psx", GAME)              # lists the archive while 7z works
    monkeypatch.setattr(convert, "_seven_zip", lambda: str(tmp_path / "no-such-7z"))
    convert._texts.clear()
    conv = convert.look(lib, "psx", GAME)
    assert conv.problem and "7z" in conv.problem


def test_running_without_7z_says_so_instead_of_crashing(tmp_path, chdman, monkeypatch):
    lib = make_lib(tmp_path, "ps2", "Shadow (Europe).7z", {"Shadow (Europe).iso": ISO})
    conv = convert.look(lib, "ps2", "Shadow (Europe)")

    def gone():
        raise convert.ToolMissing("7z is not installed")
    monkeypatch.setattr(convert, "_seven_zip", gone)
    with pytest.raises(convert.ConvertError, match="7z"):
        convert.run(lib, conv, lib.cache)


# ---------------------------------------------------------------- 5 and 6: importing
def _import_setup(tmp_path):
    from jewelcase.core.inbox import InboxItem
    box = tmp_path / "Downloads"
    box.mkdir()
    pack(box, "Shadow (Europe).7z", {"Shadow (Europe).iso": ISO})
    path = box / "Shadow (Europe).7z"
    (tmp_path / "lib" / "ps2").mkdir(parents=True)
    library = Library(tmp_path / "lib", Store(":memory:"))
    library.scan()
    return library, InboxItem(path, (path,), path.stat().st_size, "ps2", "inside the archive", "ready")


def test_importing_lists_the_archive_once(tmp_path, chdman, monkeypatch):
    from jewelcase.core.inbox import import_items
    library, item = _import_setup(tmp_path)
    calls = count_7z(monkeypatch)
    records, problems = import_items([(item, "ps2")], library, library.store, None, None, tmp_path / "cache")
    assert problems == [] and [n for n, _, _ in records[0].made] == ["Shadow (Europe).chd"]
    assert len([c for c in calls if c[1] == "l"]) == 1


def test_progress_while_copying_says_how_big_the_copy_is_not_the_whole_job(tmp_path, chdman):
    from jewelcase.core.inbox import import_items
    library, item = _import_setup(tmp_path)
    seen = []
    import_items([(item, "ps2")], library, library.store, None, seen.append, tmp_path / "cache")
    copying = [p for p in seen if p.phase == "copying"]
    assert copying and all(p.copy_total == 2 * item.size and p.copy_done <= p.copy_total for p in copying)
    assert all(p.total > p.copy_total for p in seen)    # the whole job counts the conversion too
    making = [p for p in seen if p.converting]
    assert making and all(p.copy_done == p.copy_total for p in making)
