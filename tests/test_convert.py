"""Making the file the handheld plays out of an archive, and keeping it in the library beside the archive."""
import hashlib
import shutil
import subprocess
import sys

import pytest

from jewelcase.core import convert
from jewelcase.core.db import Store
from jewelcase.core.library import Library

pytestmark = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")

ISO = b"iso-" * 5120               # ten DVD sectors
CUE = 'FILE "Racer (USA) (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\nFILE "Racer (USA) (Track 2).bin" BINARY\n'
TRACKS = {"Racer (USA).cue": CUE.encode(), "Racer (USA) (Track 1).bin": b"t1" * 3000,
          "Racer (USA) (Track 2).bin": b"t2" * 1000}


def pack(folder, name, members: dict, fmt="7z"):
    """An archive `name` in `folder` holding `members` ({name inside: bytes})."""
    src = folder.parent / ("_src_" + name)
    for inner, body in members.items():
        (src / inner).parent.mkdir(parents=True, exist_ok=True)
        (src / inner).write_bytes(body)
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["7z", "a", f"-t{fmt}", str(folder / name), "."], cwd=src, check=True, capture_output=True)
    shutil.rmtree(src)


STUB = """#!{python}
import sys
from pathlib import Path
args = sys.argv[1:]
Path({log!r}).open("a").write(" ".join(args) + "\\n")
opt = dict(zip(args[1::2], args[2::2]))
if args[0] in ("createdvd", "createcd"):
    sys.stderr.write("Compressing, 0.0% complete...\\rCompressing, 50.0% complete... (ratio=40.0%)\\r")
    if {fail_create}:
        Path(opt["-o"]).write_bytes(b"half")
        sys.exit("Error: could not read the input")
    src = Path(opt["-i"])
    body = src.read_bytes()
    if src.suffix == ".cue":
        body = b"".join((src.parent / line.split('"')[1]).read_bytes() for line in body.decode().splitlines()
                        if line.startswith("FILE"))
    Path(opt["-o"]).write_bytes(b"MComprHD" + args[0].encode() + body)
    sys.stderr.write("Compressing, 100.0% complete... (ratio=40.0%)\\nCompression complete ... final ratio = 40.0%\\n")
elif args[0] == "verify":
    sys.stderr.write("Verifying, 50.0% complete...\\r")
    sys.exit(1 if {fail_verify} else 0)
"""


@pytest.fixture
def chdman(tmp_path, monkeypatch):
    """A stand-in for chdman that 'compresses' by copying. Returns a function giving the commands it was run with."""
    log = tmp_path / "chdman.log"

    def install(fail_create=False, fail_verify=False):
        exe = tmp_path / "chdman-stub"
        exe.write_text(STUB.format(python=sys.executable, log=str(log), fail_create=fail_create,
                                   fail_verify=fail_verify))
        exe.chmod(0o755)
        monkeypatch.setattr(convert, "_chdman", lambda: str(exe))

    install()
    calls = lambda: [line.split()[0] for line in log.read_text().splitlines()] if log.exists() else []
    calls.install = install
    return calls


@pytest.fixture
def no_chdman(monkeypatch):
    monkeypatch.setattr(convert, "_chdman", lambda: None)


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    pack(root / "ps3", "Persona (USA).7z", {"Persona (USA).dec.iso": ISO, "Vimm's Lair.txt": b"hello"})
    pack(root / "ps2", "Shadow (Europe) (En,Fr).7z", {"Shadow (Europe) (En,Fr).iso": ISO, "readme.txt": b"x"})
    pack(root / "psp", "Lumines (USA).zip", {"Lumines (USA).iso": ISO}, fmt="zip")
    pack(root / "psx", "Racer (USA).7z", TRACKS)
    library = Library(root, Store(":memory:"))
    library.scan()
    library.cache = tmp_path / "cache"
    return library


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def leftovers(lib) -> list[str]:
    """Everything under the library and the work folder that is not an archive or a finished game file."""
    known = (".7z", ".zip", ".iso", ".chd", ".cue", ".bin")
    found = [p.name for p in lib.root.rglob("*") if p.is_file() and p.suffix not in known]
    return found + [p.name for p in lib.cache.rglob("*") if p.is_file()]


# ---------------------------------------------------------------- what an archive turns into
def test_an_archive_of_one_disc_image_unpacks_under_the_games_own_name(lib, chdman):
    c = convert.look(lib, "ps3", "Persona (USA)")
    assert (c.archive, c.how, c.problem) == ("Persona (USA).7z", "extract", "")
    assert c.files == ("Persona (USA).iso",)                 # ".dec.iso" inside, and the text file is left out
    assert c.sizes == {"Persona (USA).iso": len(ISO)} and c.bytes == len(ISO)


def test_a_ps2_iso_becomes_a_dvd_chd(lib, chdman):
    c = convert.look(lib, "ps2", "Shadow (Europe) (En,Fr)")
    assert (c.how, c.disc, c.files, c.problem) == ("chd", "dvd", ("Shadow (Europe) (En,Fr).chd",), "")
    assert c.bytes == len(ISO)                               # not known before it is made: never more than the ISO


def test_without_chdman_a_ps2_iso_is_unpacked_as_it_is(lib, no_chdman):
    c = convert.look(lib, "ps2", "Shadow (Europe) (En,Fr)")
    assert (c.how, c.files, c.problem) == ("extract", ("Shadow (Europe) (En,Fr).iso",), "")


def test_a_psp_iso_stays_an_iso_because_that_is_what_psp_games_are_kept_as(lib, chdman):
    c = convert.look(lib, "psp", "Lumines (USA)")
    assert (c.archive, c.how, c.files) == ("Lumines (USA).zip", "extract", ("Lumines (USA).iso",))


def test_a_cue_with_its_tracks_becomes_a_cd_chd(lib, chdman):
    c = convert.look(lib, "psx", "Racer (USA)")
    assert (c.how, c.disc, c.files) == ("chd", "cd", ("Racer (USA).chd",))
    assert c.bytes == sum(len(b) for b in TRACKS.values())


def test_without_chdman_a_cue_is_unpacked_with_its_tracks(lib, no_chdman):
    c = convert.look(lib, "psx", "Racer (USA)")
    assert c.how == "extract" and c.files == tuple(TRACKS) and c.sizes == {n: len(b) for n, b in TRACKS.items()}


def test_a_game_without_an_archive_is_not_something_to_convert(lib, tmp_path, chdman):
    (lib.root / "ps2" / "Odd (USA).nrg").write_bytes(b"x")
    lib.scan()
    assert convert.look(lib, "ps2", "Odd (USA)") is None
    assert convert.look(lib, "ps2", "Not There (USA)") is None


NO_PLAYLIST = ("The archive holds more than one disc, and the handheld's PlayStation 2 emulators take no "
               "playlist to put them together as one game.")


@pytest.mark.parametrize("members, problem", [
    ({"notes.txt": b"x"}, "The archive holds no game file."),
    ({"Disc 1.iso": b"1", "Disc 2.iso": b"2"}, NO_PLAYLIST),
    ({"A.cue": b"", "B.cue": b"", "a.bin": b"1"}, NO_PLAYLIST),
    ({"Game.iso": b"1", "Other.nrg": b"2"}, "The archive holds more than one game, which Jewelcase cannot convert yet."),
    ({"Game.nrg": b"x"}, "The archive holds a .nrg file, which is not a format the handheld's PlayStation 2 "
                         "emulators take."),
])
def test_an_archive_that_cannot_be_converted_says_why(lib, chdman, members, problem):
    pack(lib.root / "ps2", "Odd (USA).7z", members)
    lib.scan()
    assert convert.look(lib, "ps2", "Odd (USA)").problem == problem


def test_an_archive_that_cannot_be_read_says_so(lib, chdman):
    (lib.root / "ps2" / "Broken (USA).7z").write_bytes(b"not an archive at all")
    lib.scan()
    assert convert.look(lib, "ps2", "Broken (USA)").problem == "The archive could not be read."


def test_a_track_that_would_replace_another_games_file_stops_the_conversion(lib, no_chdman):
    (lib.root / "psx" / "Racer (USA) (Track 2).bin").write_bytes(b"someone else's")
    lib.scan()
    assert convert.look(lib, "psx", "Racer (USA)").problem == \
        "The library already has a file named Racer (USA) (Track 2).bin."


def test_a_missing_7z_is_named(lib, chdman, monkeypatch):
    from jewelcase.core import library as libmod
    monkeypatch.setattr(libmod.shutil, "which", lambda name: None)
    assert convert.look(lib, "ps3", "Persona (USA)").problem == "7z is not installed."


# ---------------------------------------------------------------- doing it
def run(lib, system, stem, report=None):
    c = convert.look(lib, system, stem)
    convert.run(lib, c, lib.cache, report)
    return c


def test_unpacking_puts_the_game_beside_its_archive_and_into_the_index(lib, chdman):
    seen = []
    c = run(lib, "ps3", "Persona (USA)", lambda phase, done: seen.append((phase, done)))
    made = lib.root / "ps3" / "Persona (USA).iso"
    assert made.read_bytes() == ISO and (lib.root / "ps3" / "Persona (USA).7z").exists()
    assert lib.has("ps3", "Persona (USA).iso") and lib.cached_md5("ps3", "Persona (USA).iso") == md5(ISO)
    assert leftovers(lib) == []
    assert [p for p, _ in seen if p != "verifying"][0] == "unpacking" and seen[-1] == ("verifying", c.work)
    assert [d for _, d in seen] == sorted(d for _, d in seen)       # it only ever moves forward
    assert chdman() == []


def test_a_cue_and_its_tracks_all_arrive_or_none_does(lib, no_chdman):
    run(lib, "psx", "Racer (USA)")
    for name, body in TRACKS.items():
        assert (lib.root / "psx" / name).read_bytes() == body and lib.cached_md5("psx", name) == md5(body)
    assert [u.files for u in lib.units("psx") if u.ext == ".cue"] == [tuple(TRACKS)]
    assert leftovers(lib) == []


def test_a_ps2_iso_is_made_into_a_dvd_chd_and_checked(lib, chdman):
    seen = []
    c = run(lib, "ps2", "Shadow (Europe) (En,Fr)", lambda phase, done: seen.append((phase, done)))
    made = lib.root / "ps2" / "Shadow (Europe) (En,Fr).chd"
    assert made.read_bytes() == b"MComprHDcreatedvd" + ISO
    assert chdman() == ["createdvd", "verify"]
    assert lib.has("ps2", made.name) and not (lib.root / "ps2" / "Shadow (Europe) (En,Fr).iso").exists()
    assert leftovers(lib) == []                                      # the unpacked ISO is gone again
    assert list(dict.fromkeys(p for p, _ in seen)) == ["unpacking", "compressing", "verifying"]
    assert seen[-1] == ("verifying", c.work)


def test_a_cue_is_made_into_a_cd_chd(lib, chdman):
    run(lib, "psx", "Racer (USA)")
    made = (lib.root / "psx" / "Racer (USA).chd").read_bytes()
    assert made == b"MComprHDcreatecd" + TRACKS["Racer (USA) (Track 1).bin"] + TRACKS["Racer (USA) (Track 2).bin"]
    assert chdman() == ["createcd", "verify"] and leftovers(lib) == []


def nothing_was_made(lib, system) -> bool:
    return leftovers(lib) == [] and all(p.suffix in (".7z", ".zip") for p in (lib.root / system).iterdir())


def test_chdman_failing_leaves_nothing_behind(lib, chdman):
    chdman.install(fail_create=True)
    with pytest.raises(convert.ConvertError, match="chdman could not make the CHD: Error: could not read the input"):
        run(lib, "ps2", "Shadow (Europe) (En,Fr)")
    assert nothing_was_made(lib, "ps2") and not lib.has("ps2", "Shadow (Europe) (En,Fr).chd")


def test_a_chd_that_does_not_verify_is_thrown_away(lib, chdman):
    chdman.install(fail_verify=True)
    with pytest.raises(convert.ConvertError, match="The CHD did not pass chdman's check"):
        run(lib, "ps2", "Shadow (Europe) (En,Fr)")
    assert nothing_was_made(lib, "ps2")


def test_an_unpacked_file_that_does_not_read_back_the_same_is_thrown_away(lib, chdman, monkeypatch):
    monkeypatch.setattr(convert, "md5_file", lambda path, progress=None: "0" * 32)
    with pytest.raises(convert.ConvertError, match="did not read back the same"):
        run(lib, "ps3", "Persona (USA)")
    assert nothing_was_made(lib, "ps3")


def test_an_archive_that_breaks_half_way_is_reported(lib, chdman):
    arc = lib.root / "ps3" / "Persona (USA).7z"
    c = convert.look(lib, "ps3", "Persona (USA)")
    body = bytearray(arc.read_bytes())
    body[40:80] = b"\xff" * 40                      # damage the packed data, not the listing at the end
    arc.write_bytes(bytes(body))
    with pytest.raises(convert.ConvertError, match="could not be unpacked"):
        convert.run(lib, c, lib.cache)
    assert nothing_was_made(lib, "ps3")


def test_it_does_not_start_without_room_in_the_library(lib, chdman, monkeypatch):
    monkeypatch.setattr(Library, "free_bytes", lambda self: 1000)
    with pytest.raises(convert.ConvertError, match="not enough room in the library"):
        run(lib, "ps3", "Persona (USA)")
    assert nothing_was_made(lib, "ps3")


def test_stopping_half_way_leaves_nothing_behind(lib, chdman):
    class Stop(Exception):
        pass

    def report(phase, done):
        if phase == "compressing":
            raise Stop

    with pytest.raises(Stop):
        run(lib, "ps2", "Shadow (Europe) (En,Fr)", report)
    assert nothing_was_made(lib, "ps2")


def test_converting_again_never_replaces_what_is_there(lib, chdman):
    c = run(lib, "ps3", "Persona (USA)")
    with pytest.raises(convert.ConvertError, match="already has a file named Persona \\(USA\\).iso"):
        convert.run(lib, c, lib.cache)
    assert (lib.root / "ps3" / "Persona (USA).iso").read_bytes() == ISO


@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")
def test_the_real_chdman_makes_a_dvd_chd(lib):
    run(lib, "ps2", "Shadow (Europe) (En,Fr)")
    made = lib.root / "ps2" / "Shadow (Europe) (En,Fr).chd"
    info = subprocess.run(["chdman", "info", "-i", str(made)], capture_output=True, text=True).stdout
    assert "Tag='DVD '" in info and f"{len(ISO):,} bytes" in info and leftovers(lib) == []


# ---------------------------------------------------------------- in the game list, the plan and a sync
from conftest import CARD                                   # noqa: E402
from jewelcase.core import plan as planmod                     # noqa: E402
from jewelcase.core.plan import build_plan                     # noqa: E402
from jewelcase.core.sync import Runner                         # noqa: E402

PERSONA, SHADOW = "Persona (USA)", "Shadow (Europe)"


@pytest.fixture
def w(world, chdman):
    pack(world.lib_root / "ps3", PERSONA + ".7z", {PERSONA + ".dec.iso": ISO, "notes.txt": b"x"})
    pack(world.lib_root / "ps2", SHADOW + ".7z", {SHADOW + ".iso": ISO})
    return world.refresh()


def tick(w, *keys, want=True):
    for system, stem in keys:
        w.store.set_wanted(w.serial, system, stem, want)


def plan(w):
    return build_plan(w.rows(), w.dev.storage(CARD), w.library, w.dev.info())


class Run:
    def __init__(self, w, tmp_path):
        self.w, self.events = w, []
        self.runner = Runner(lambda: w.dev, w.library, w.store, w.profile, w.serial, log_path=tmp_path / "sync.log",
                             data_dir=tmp_path / "data", on_progress=self.on_progress, sleep=lambda s: None,
                             cache_dir=tmp_path / "cache")
        self.hook = None

    def on_progress(self, p):
        self.events.append(p)
        if self.hook:
            self.hook(p)

    def start(self):
        self.job = self.runner.start(plan(self.w))
        return self

    def go(self, **kw):
        return self.runner.run(self.job, **kw)

    def steps(self):
        return [(s.kind, s.payload.get("name") or s.payload.get("stem") or "", s.state, s.message)
                for s in self.w.store.job_steps(self.job)]


@pytest.fixture
def sync(w, tmp_path):
    return Run(w, tmp_path / "run")


def test_an_archive_only_game_is_looked_into_once_it_is_ticked(w):
    row = w.row("ps3", PERSONA)
    assert (row.state, row.archive, row.convert) == ("needs_conversion", PERSONA + ".7z", None)
    tick(w, ("ps3", PERSONA))
    row = w.row("ps3", PERSONA)
    assert row.convert.files == (PERSONA + ".iso",) and row.size == len(ISO)
    assert row.sizes == {PERSONA + ".iso": len(ISO)}


def test_a_game_the_library_has_in_a_playable_form_has_nothing_to_convert(w):
    assert (w.row("ps2", "Champions (USA)").archive, w.row("ps2", "Champions (USA)").convert) == ("", None)


def test_a_ticked_archive_is_an_addition_that_is_converted_first(w):
    tick(w, ("ps3", PERSONA), ("ps2", SHADOW))
    p = plan(w)
    assert [(i.row.stem, i.files, i.bytes, i.convert.how) for i in p.adds] == [
        (SHADOW, (SHADOW + ".chd",), len(ISO), "chd"), (PERSONA, (PERSONA + ".iso",), len(ISO), "extract")]
    assert p.add_bytes == 2 * len(ISO) and p.fits and p.skipped == []


def test_an_archive_that_cannot_be_converted_is_left_out_with_the_reason(w):
    tick(w, ("ps2", "Only Archive (USA)"))
    p = plan(w)
    assert p.adds == [] and [(r.stem, why) for r, why in p.skipped] == [
        ("Only Archive (USA)", "The archive could not be read.")]


def test_the_time_a_sync_takes_counts_the_converting(w, monkeypatch):
    tick(w, ("ps2", SHADOW))
    monkeypatch.setattr(planmod, "CONVERT_RATE", 1024)
    assert plan(w).seconds == 4 * len(ISO) // 1024           # unpack, read back, compress, check; the copy is instant


def test_a_sync_converts_then_copies_and_the_library_keeps_the_converted_file(w, sync):
    tick(w, ("ps3", PERSONA))
    assert sync.start().go() == "done"
    assert [s[:3] for s in sync.steps()] == [("convert", PERSONA, "done"), ("push", PERSONA + ".iso", "done"),
                                             ("refresh", "", "done")]
    assert (w.lib_root / "ps3" / (PERSONA + ".iso")).read_bytes() == ISO
    assert (w.lib_root / "ps3" / (PERSONA + ".7z")).exists()
    assert w.dev_path("ps3", PERSONA + ".iso").read_bytes() == ISO
    row = w.refresh().row("ps3", PERSONA)
    assert (row.state, row.lib_unit.files, row.convert) == ("on_device", (PERSONA + ".iso",), None)
    converting = [e for e in sync.events if e.kind == "convert" and e.state == "running"]
    assert {e.message for e in converting} >= {"Unpacking", "Checking"}
    assert converting[0].label == PERSONA and converting[0].bytes_total == 3 * len(ISO)     # 2 passes + the copy
    assert [e.bytes_done for e in sync.events] == sorted(e.bytes_done for e in sync.events)


def test_plain_additions_go_first_then_each_converted_game_is_made_and_copied(w, sync):
    tick(w, ("ps3", PERSONA), ("ps2", SHADOW), ("ps2", "Champions (USA)"))
    assert sync.start().go() == "done"
    assert [s[:2] for s in sync.steps()] == [
        ("push", "Champions (USA).chd"), ("convert", SHADOW), ("push", SHADOW + ".chd"),
        ("convert", PERSONA), ("push", PERSONA + ".iso"), ("refresh", "")]
    assert w.dev_path("ps2", SHADOW + ".chd").read_bytes() == b"MComprHDcreatedvd" + ISO


def test_a_game_that_fails_to_convert_is_not_copied_and_the_rest_goes_on(w, sync, chdman):
    tick(w, ("ps3", PERSONA), ("ps2", SHADOW))
    sync.start()
    chdman.install(fail_create=True)
    assert sync.go() == "done"
    steps = {s[:2]: s[2:] for s in sync.steps()}
    assert steps[("convert", SHADOW)] == ("failed", "chdman could not make the CHD: Error: could not read the input")
    assert steps[("push", SHADOW + ".chd")] == ("skipped", "it could not be converted")
    assert steps[("push", PERSONA + ".iso")] == ("done", "")
    assert not w.dev_path("ps2", SHADOW + ".chd").exists()


def test_a_game_converted_in_the_meantime_is_just_copied(w, sync, tmp_path):
    tick(w, ("ps3", PERSONA))
    sync.start()
    convert.run(w.library, convert.look(w.library, "ps3", PERSONA), tmp_path / "elsewhere")
    assert sync.go() == "done"
    assert sync.steps()[0] == ("convert", PERSONA, "done", "already converted")
    assert w.dev_path("ps3", PERSONA + ".iso").read_bytes() == ISO


def test_an_archive_that_changed_since_the_review_is_not_converted(w, sync):
    tick(w, ("ps3", PERSONA))
    sync.start()
    (w.lib_root / "ps3" / (PERSONA + ".7z")).unlink()
    pack(w.lib_root / "ps3", PERSONA + ".7z", {PERSONA + ".cue": b"", "track.bin": b"x"})
    assert sync.go() == "done"
    assert sync.steps()[0][2:] == ("failed", "The archive is not what it was when the sync was reviewed.")
    assert sync.steps()[1][2:] == ("skipped", "it could not be converted")
    assert sorted(p.name for p in (w.lib_root / "ps3").iterdir()) == [PERSONA + ".7z"]


def test_unticking_before_resuming_skips_the_conversion(w, sync):
    tick(w, ("ps3", PERSONA))
    sync.start()
    w.store.set_wanted(w.serial, "ps3", PERSONA, None)
    assert sync.go(resumed=True) == "done"
    assert [s[2] for s in sync.steps()[:2]] == ["skipped", "skipped"]
    assert not (w.lib_root / "ps3" / (PERSONA + ".iso")).exists()


def test_pausing_while_converting_leaves_nothing_half_made_and_resuming_finishes(w, sync):
    tick(w, ("ps2", SHADOW))
    sync.start()

    def pause_once(p):
        if p.kind == "convert" and p.message == "Compressing":
            sync.hook = None
            sync.runner.stop("pause")

    sync.hook = pause_once
    assert sync.go() == "paused"
    assert sorted(p.name for p in (w.lib_root / "ps2").iterdir() if SHADOW in p.name) == [SHADOW + ".7z"]
    assert sync.go() == "done"
    assert w.dev_path("ps2", SHADOW + ".chd").read_bytes() == b"MComprHDcreatedvd" + ISO


# ---------------------------------------------------------------- findings of the branch review
def test_a_system_with_no_preferred_form_gets_the_unpacked_game_not_a_chd(lib, chdman):
    pack(lib.root / "xbox", "Halo (USA).7z", {"Halo (USA).iso": ISO})
    lib.scan()
    c = convert.look(lib, "xbox", "Halo (USA)")
    assert (c.how, c.files, c.problem) == ("extract", ("Halo (USA).iso",), "")


@pytest.mark.parametrize("members", [
    {"Twin (USA).cue": b"", "a/t.bin": b"1", "b/t.bin": b"2"},
    {"Twin (USA).cue": b"", "track.bin": b"1", "TRACK.BIN": b"2"},
])
def test_two_files_with_the_same_name_are_refused_even_for_a_chd(lib, chdman, members):
    pack(lib.root / "psx", "Twin (USA).7z", members)
    lib.scan()
    assert convert.look(lib, "psx", "Twin (USA)").problem == "The archive holds two files with the same name."


def test_a_cue_whose_tracks_cannot_all_be_put_in_place_is_taken_back_out(lib, no_chdman, monkeypatch):
    import os
    real, calls = os.link, []

    def link(src, dst):
        calls.append(os.path.basename(src))
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(convert.os, "link", link)
    with pytest.raises(convert.ConvertError, match="could not be written: No space left on device"):
        run(lib, "psx", "Racer (USA)")
    monkeypatch.undo()
    assert nothing_was_made(lib, "psx") and not any(lib.has("psx", n) for n in TRACKS)
    assert calls[0] != "Racer (USA).cue.part"           # the file that makes it a game goes in last
    assert convert.look(lib, "psx", "Racer (USA)").problem == ""        # and it can be tried again


def test_the_disc_image_a_chd_is_made_from_is_read_back_first(lib, chdman, monkeypatch):
    monkeypatch.setattr(convert, "md5_file", lambda path, progress=None: "0" * 32)
    with pytest.raises(convert.ConvertError, match="did not read back the same"):
        run(lib, "ps2", "Shadow (Europe) (En,Fr)")
    assert nothing_was_made(lib, "ps2") and chdman() == []


def test_stopping_at_the_very_end_still_leaves_nothing_behind(lib, chdman):
    c = convert.look(lib, "ps2", "Shadow (Europe) (En,Fr)")

    class Stop(Exception):
        pass

    def report(phase, done):
        if done == c.work:
            raise Stop

    with pytest.raises(Stop):
        convert.run(lib, c, lib.cache, report)
    assert nothing_was_made(lib, "ps2") and not lib.has("ps2", "Shadow (Europe) (En,Fr).chd")


def test_what_a_killed_conversion_left_in_the_work_folder_is_cleared(lib, chdman):
    stale = lib.cache / "convert" / "tmpkilled"
    stale.mkdir(parents=True)
    (stale / "Huge (USA).iso").write_bytes(b"x" * 100)
    run(lib, "ps2", "Shadow (Europe) (En,Fr)")
    assert not stale.exists() and leftovers(lib) == []


def test_7z_is_never_left_waiting_for_a_password_on_the_terminal(lib, chdman, monkeypatch):
    seen = []
    real_popen, real_run = subprocess.Popen, subprocess.run

    class Popen(real_popen):
        def __init__(self, cmd, *a, **kw):
            seen.append((cmd[0], kw.get("stdin")))
            super().__init__(cmd, *a, **kw)

    def run_(cmd, *a, **kw):
        seen.append((cmd[0], kw.get("stdin")))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Popen)
    monkeypatch.setattr(subprocess, "run", run_)
    convert._listings.clear()
    run(lib, "ps3", "Persona (USA)")
    sevens = [stdin for exe, stdin in seen if "7z" in exe]
    assert sevens and all(stdin == subprocess.DEVNULL for stdin in sevens)


def test_the_archives_of_a_system_are_worked_out_once_not_once_per_game(world, monkeypatch):
    for i in range(30):
        (world.lib_root / "ps2" / f"Packed {i:02d} (USA).7z").write_bytes(b"x")
    world.refresh()
    real, calls = convert.archives, []
    monkeypatch.setattr(convert, "archives", lambda *a: calls.append(a[0].folder) or real(*a))
    rows = world.rows()
    assert sum(1 for r in rows if r.archive) == 31 and len(calls) == len(set(calls))


@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")
def test_the_real_chdman_makes_a_cd_chd_from_a_cue(lib):
    cue = 'FILE "Disc (USA).bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
    pack(lib.root / "psx", "Disc (USA).7z", {"Disc (USA).cue": cue.encode(), "Disc (USA).bin": bytes(2352 * 32)})
    lib.scan()
    run(lib, "psx", "Disc (USA)")
    info = subprocess.run(["chdman", "info", "-i", str(lib.root / "psx" / "Disc (USA).chd")],
                          capture_output=True, text=True).stdout
    assert "CHT2" in info and leftovers(lib) == []
    assert sorted(p.name for p in (lib.root / "psx").iterdir() if "Disc" in p.name) == ["Disc (USA).7z",
                                                                                       "Disc (USA).chd"]


def test_no_file_of_a_game_is_copied_when_its_conversion_failed(w, sync, no_chdman):
    cue = 'FILE "Track 01.bin" BINARY\n  TRACK 01 MODE1/2352\n'
    for game, body in (("Alpha CD (USA)", b"a" * 64), ("Beta CD (USA)", b"b" * 64)):
        pack(w.lib_root / "megadrive", game + ".7z", {game + ".cue": cue.encode(), "Track 01.bin": body})
    w.refresh()
    tick(w, ("megadrive", "Alpha CD (USA)"), ("megadrive", "Beta CD (USA)"))
    assert sync.start().go() == "done"
    steps = sync.steps()
    beta = [s for s in steps if s[0] == "push"][2:]
    assert [s[1] for s in beta] == ["Beta CD (USA).cue", "Track 01.bin"]
    assert [s[2:] for s in beta] == [("skipped", "it could not be converted")] * 2
    assert next(s for s in steps if s[:2] == ("convert", "Beta CD (USA)"))[2:] == (
        "failed", "The library already has a file named Track 01.bin.")
    assert w.dev_path("megadrive", "Track 01.bin").read_bytes() == b"a" * 64
    assert not w.dev_path("megadrive", "Beta CD (USA).cue").exists()


def test_cancelling_while_converting_closes_the_sync_and_leaves_nothing_half_made(w, sync):
    tick(w, ("ps2", SHADOW))
    sync.start()
    sync.hook = lambda p: sync.runner.stop("cancel") if p.message == "Compressing" else None
    assert sync.go() == "cancelled"
    assert sorted(p.name for p in (w.lib_root / "ps2").iterdir() if SHADOW in p.name) == [SHADOW + ".7z"]
    assert w.store.open_job(w.serial) is None and not w.dev_path("ps2", SHADOW + ".chd").exists()
