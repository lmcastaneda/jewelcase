"""Archives that hold several discs: each disc is converted and a playlist joins them into one game."""
import shutil

import pytest

from jewelcase.core import convert
from jewelcase.core.db import Store
from jewelcase.core.library import Library
from test_convert import ISO, Run, chdman, no_chdman, pack, plan, tick  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")

GAME = "Final Quest (USA)"


def disc_files(n: int, tracks: int = 2) -> dict:
    """The files of an archive of `n` discs, each a cue sheet with `tracks` track files."""
    out = {}
    for d in range(1, n + 1):
        stem = f"{GAME} (Disc {d})"
        names = [f"{stem} (Track {t}).bin" for t in range(1, tracks + 1)]
        out[stem + ".cue"] = "".join(f'FILE "{name}" BINARY\n  TRACK 01 MODE2/2352\n' for name in names).encode()
        for t, name in enumerate(names, 1):
            out[name] = f"d{d}t{t}".encode() * 500
    return out


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    pack(root / "psx", GAME + ".7z", disc_files(3))
    pack(root / "ps2", "Two Disc (USA).7z", {"A (Disc 1).iso": ISO, "A (Disc 2).iso": ISO})
    library = Library(root, Store(":memory:"))
    library.scan()
    library.cache = tmp_path / "cache"
    return library


def leftovers(lib) -> list[str]:
    """Anything under the library or the work folder that is not an archive or a finished game file."""
    known = (".7z", ".zip", ".iso", ".chd", ".cue", ".bin", ".m3u")
    found = [p.name for p in lib.root.rglob("*") if p.is_file() and p.suffix not in known]
    return found + [p.name for p in lib.cache.rglob("*") if p.is_file()]


def run(lib, system, stem, report=None):
    conv = convert.look(lib, system, stem)
    convert.run(lib, conv, lib.cache, report)
    return conv


def names(lib, system="psx"):
    return sorted(p.name for p in (lib.root / system).iterdir())


def test_a_three_disc_archive_is_looked_at_as_three_chds_and_a_playlist(lib, chdman):
    c = convert.look(lib, "psx", GAME)
    assert c.problem == "" and c.playlist == GAME + ".m3u"
    assert c.files == (GAME + ".m3u", *(f"{GAME} (Disc {d}).chd" for d in (1, 2, 3)))
    assert [d.how for d in c.discs] == ["chd"] * 3 and c.discs[0].disc == "cd"
    assert c.bytes == len(c.playlist_text) + sum(len(b) for b in disc_files(3).values())


def test_each_disc_becomes_a_chd_and_the_library_shows_one_game(lib, chdman):
    seen = []
    c = run(lib, "psx", GAME, lambda phase, done: seen.append((phase, done)))
    assert names(lib) == sorted([GAME + ".7z", GAME + ".m3u", *(f"{GAME} (Disc {d}).chd" for d in (1, 2, 3))])
    assert (lib.root / "psx" / (GAME + ".m3u")).read_text() == "".join(f"{GAME} (Disc {d}).chd\n" for d in (1, 2, 3))
    made = (lib.root / "psx" / f"{GAME} (Disc 2).chd").read_bytes()
    assert made == b"MComprHDcreatecd" + disc_files(3)[f"{GAME} (Disc 2) (Track 1).bin"] \
        + disc_files(3)[f"{GAME} (Disc 2) (Track 2).bin"]                   # each cue's own tracks, nobody else's
    assert chdman() == ["createcd", "verify"] * 3 and leftovers(lib) == []
    units = [u for u in lib.units("psx") if u.stem == GAME and u.ext == ".m3u"]
    assert len(units) == 1 and units[0].head == GAME + ".m3u" and len(units[0].files) == 4 and units[0].missing == ()
    assert all(lib.has("psx", n) for n in c.files)
    assert [d for _, d in seen] == sorted(d for _, d in seen) and seen[-1] == ("verifying", c.work)
    assert list(dict.fromkeys(p for p, _ in seen)) == ["unpacking", "compressing", "verifying"]


def test_without_chdman_the_cues_and_their_tracks_arrive_with_the_playlist(lib, no_chdman):
    c = convert.look(lib, "psx", GAME)
    assert [d.how for d in c.discs] == ["extract"] * 3
    run(lib, "psx", GAME)
    assert (lib.root / "psx" / (GAME + ".m3u")).read_text() == "".join(f"{GAME} (Disc {d}).cue\n" for d in (1, 2, 3))
    for name, body in disc_files(3).items():
        assert (lib.root / "psx" / name).read_bytes() == body
    unit = next(u for u in lib.units("psx") if u.stem == GAME and u.ext == ".m3u")
    assert unit.head == GAME + ".m3u" and set(unit.files) == {GAME + ".m3u", *disc_files(3)} and unit.missing == ()
    assert leftovers(lib) == []


def test_discs_are_listed_in_natural_order(lib, chdman):
    pack(lib.root / "psx", "Big (USA).7z", {f"Big (Disc {d}).iso": ISO for d in (10, 2, 1)})
    lib.scan()
    c = convert.look(lib, "psx", "Big (USA)")
    assert c.playlist_text == "Big (Disc 1).chd\nBig (Disc 2).chd\nBig (Disc 10).chd\n"
    assert [d.disc for d in c.discs] == ["cd"] * 3


def test_disc_images_with_no_cue_are_discs_too(lib, no_chdman):
    pack(lib.root / "psx", "Big (USA).7z", {"Big (Disc 2).iso": ISO, "Big (Disc 1).iso": ISO})
    lib.scan()
    run(lib, "psx", "Big (USA)")
    assert (lib.root / "psx" / "Big (USA).m3u").read_text() == "Big (Disc 1).iso\nBig (Disc 2).iso\n"


def test_a_failure_half_way_leaves_nothing_behind(lib, chdman):
    chdman.install(fail_create="'Disc 2' in opt['-i']")
    with pytest.raises(convert.ConvertError, match="chdman could not make the CHD"):
        run(lib, "psx", GAME)
    assert names(lib) == [GAME + ".7z"] and leftovers(lib) == []
    assert not any(lib.has("psx", n) for n in convert.look(lib, "psx", GAME).files)


def test_a_stop_after_the_first_disc_leaves_nothing_behind(lib, chdman):
    class Stop(Exception):
        pass

    def report(phase, done):
        if phase == "unpacking" and done > convert.look(lib, "psx", GAME).discs[0].work:
            raise Stop

    with pytest.raises(Stop):
        run(lib, "psx", GAME, report)
    assert names(lib) == [GAME + ".7z"] and leftovers(lib) == []


def test_the_playlist_goes_in_last(lib, no_chdman, monkeypatch):
    import os
    real, calls = os.link, []

    def link(src, dst):
        calls.append(os.path.basename(src))             # files are put in place by linking the part to its name
        return real(src, dst)

    monkeypatch.setattr(convert.os, "link", link)
    run(lib, "psx", GAME)
    assert calls[-1] == GAME + ".m3u.part"


def test_a_system_with_no_playlists_still_refuses_and_says_why(lib, chdman):
    c = convert.look(lib, "ps2", "Two Disc (USA)")
    assert "more than one disc" in c.problem and "take no playlist" in c.problem


def test_a_cue_that_names_a_track_the_archive_lacks_is_refused(lib, chdman):
    pack(lib.root / "psx", "Short (USA).7z", {"A.cue": b'FILE "gone.bin" BINARY\n', "B.cue": b'FILE "b.bin" BINARY\n',
                                               "b.bin": b"1"})
    lib.scan()
    assert convert.look(lib, "psx", "Short (USA)").problem == "A.cue names gone.bin, which is not in the archive."


def test_a_playlist_name_the_library_already_has_stops_the_conversion(lib, chdman):
    (lib.root / "psx" / (GAME + ".m3u")).write_text("someone else's\n")
    lib.scan()
    assert not convert.for_import(lib, "psx", lib.root / "psx" / (GAME + ".7z"))        # it is playable already
    assert convert.look(lib, "psx", GAME).problem == f"The library already has a file named {GAME}.m3u."


def test_two_dreamcast_discs_with_tracks_of_the_same_names_become_chds(lib, chdman):
    gdi = "3\n1 0 4 2352 track01.bin 0\n2 450 0 2352 track02.raw 0\n3 45000 4 2352 track03.bin 0\n"
    members = {}
    for d in (1, 2):
        members[f"Disc {d}/disc.gdi"] = gdi.encode()
        for t in ("track01.bin", "track02.raw", "track03.bin"):
            members[f"Disc {d}/{t}"] = f"disc{d}{t}".encode() * 100
    pack(lib.root / "dreamcast", "Skies (USA).7z", members)
    lib.scan()
    c = convert.look(lib, "dreamcast", "Skies (USA)")
    assert c.problem == "" and c.files == ("Skies (USA).m3u", "Skies (USA) (Disc 1).chd", "Skies (USA) (Disc 2).chd")
    run(lib, "dreamcast", "Skies (USA)")
    assert chdman() == ["createcd", "verify"] * 2


def test_two_dreamcast_discs_with_tracks_of_the_same_names_cannot_be_unpacked_side_by_side(lib, no_chdman):
    gdi = "1\n1 0 4 2352 track01.bin 0\n"
    members = {f"Disc {d}/{n}": gdi.encode() if n.endswith("gdi") else b"x" for d in (1, 2) for n in ("disc.gdi", "track01.bin")}
    pack(lib.root / "dreamcast", "Skies (USA).7z", members)
    lib.scan()
    assert convert.look(lib, "dreamcast", "Skies (USA)").problem == "The archive holds two files with the same name."


# ---------------------------------------------------------------- in a sync
@pytest.fixture
def w(world, chdman):
    pack(world.lib_root / "psx", GAME + ".7z", disc_files(3))
    return world.refresh()


@pytest.fixture
def sync(w, tmp_path):
    return Run(w, tmp_path / "run")


def test_a_ticked_multi_disc_archive_is_planned_as_one_addition(w):
    tick(w, ("psx", GAME))
    p = plan(w)
    assert [(i.row.stem, i.files[0], len(i.files), i.convert.how) for i in p.adds] == [
        (GAME, GAME + ".m3u", 4, "chd")]
    assert p.skipped == [] and p.add_bytes == w.row("psx", GAME).size


def test_a_sync_converts_the_discs_and_copies_the_playlist_and_every_disc(w, sync):
    tick(w, ("psx", GAME))
    assert sync.start().go() == "done"
    assert [s[:3] for s in sync.steps()] == [
        ("convert", GAME, "done"), ("push", GAME + ".m3u", "done"), ("push", GAME + " (Disc 1).chd", "done"),
        ("push", GAME + " (Disc 2).chd", "done"), ("push", GAME + " (Disc 3).chd", "done"), ("refresh", "", "done")]
    assert w.dev_path("psx", GAME + ".m3u").read_text() == "".join(f"{GAME} (Disc {d}).chd\n" for d in (1, 2, 3))
    assert w.dev_path("psx", GAME + " (Disc 3).chd").read_bytes().startswith(b"MComprHDcreatecd")
    row = w.refresh().row("psx", GAME)
    assert (row.state, row.convert) == ("on_device", None) and row.lib_unit.head == GAME + ".m3u"
    assert [e.bytes_done for e in sync.events] == sorted(e.bytes_done for e in sync.events)
    assert (w.lib_root / "psx" / (GAME + ".7z")).exists()


@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")
def test_the_real_chdman_makes_a_chd_of_each_disc(lib):
    members = {}
    for d in (1, 2):
        cue = f'FILE "Disc {d}.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
        members[f"Disc {d}.cue"] = cue.encode()
        members[f"Disc {d}.bin"] = bytes(2352 * 32)
    pack(lib.root / "psx", "Pair (USA).7z", members)
    lib.scan()
    run(lib, "psx", "Pair (USA)")
    assert {"Disc 1.chd", "Disc 2.chd", "Pair (USA).m3u"} <= set(names(lib)) and leftovers(lib) == []
