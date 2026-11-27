"""GameCube and Wii .iso made into .rvz with dolphin-tool.

dolphin-tool is not installed on the machine these tests were written on, so every test here runs a
stand-in program that records how it was called and writes a file. The real tool has never been run.
"""
import shutil
import sys

import pytest

from jewelcase.core import convert, systems
from jewelcase.core.db import Store
from jewelcase.core.library import Library, pick_device_form
from test_convert import ISO, Run, chdman, no_chdman, pack, plan, tick  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")

WIND = "Wind Tales (USA)"
STUB = """#!{python}
import sys
from pathlib import Path
args = sys.argv[1:]
Path({log!r}).open("a").write(" ".join(args) + "\\n")
opt = dict(zip(args[1::2], args[2::2]))
if {mode!r} == "fail":
    sys.stderr.write("Error: could not read the disc\\n")
    sys.exit(1)
sys.stderr.write("Converting, 50.0% complete...\\r")
if {mode!r} == "empty":
    Path(opt["-o"]).write_bytes(b"")
elif {mode!r} != "nothing":
    Path(opt["-o"]).write_bytes(b"RVZ\\x01" + Path(opt["-i"]).read_bytes()[4:])
"""


@pytest.fixture
def dolphin(tmp_path, monkeypatch):
    """A stand-in dolphin-tool. Returns a function giving the argument lines it was called with."""
    log = tmp_path / "dolphin.log"

    def install(mode="ok"):
        exe = tmp_path / "dolphin-tool-stub"
        exe.write_text(STUB.format(python=sys.executable, log=str(log), mode=mode))
        exe.chmod(0o755)
        monkeypatch.setattr(convert, "_dolphin", lambda: str(exe))

    install()
    calls = lambda: log.read_text().splitlines() if log.exists() else []
    calls.install = install
    return calls


@pytest.fixture
def no_dolphin(monkeypatch):
    monkeypatch.setattr(convert, "_dolphin", lambda: None)


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    pack(root / "gamecube", WIND + ".7z", {WIND + ".iso": ISO, "readme.txt": b"x"})
    pack(root / "wii", "Sports Pack (USA).zip", {"Sports Pack (USA).iso": ISO}, fmt="zip")
    pack(root / "psp", "Lumines (USA).7z", {"Lumines (USA).iso": ISO})
    pack(root / "gamecube", "Compressed (USA).7z", {"Compressed (USA).gcz": b"gcz-" * 100})
    (root / "gamecube" / "Loose (USA).iso").write_bytes(ISO)
    (root / "wii" / "Loose Wii (USA).iso").write_bytes(ISO)
    library = Library(root, Store(":memory:"))
    library.scan()
    library.cache = tmp_path / "cache"
    return library


def leftovers(lib) -> list[str]:
    known = (".7z", ".zip", ".iso", ".rvz", ".gcz")
    found = [p.name for p in lib.root.rglob("*") if p.is_file() and p.suffix not in known]
    return found + [p.name for p in lib.cache.rglob("*") if p.is_file()]


def run(lib, system, stem, report=None):
    conv = convert.look(lib, system, stem)
    convert.run(lib, conv, lib.cache, report)
    return conv


def test_the_target_is_worked_out_from_the_systems_preference():
    assert [convert.wanted_form(systems.get(s), ".iso") for s in ("gamecube", "wii", "ps2", "psx", "psp", "ps3")] == \
        [".rvz", ".rvz", ".chd", ".chd", "", ""]
    assert convert.wanted_form(systems.get("gamecube"), ".gcz") == ""
    assert convert.wanted_form(systems.get("ps2"), ".cue") == ".chd"


# ---------------------------------------------------------------- from an archive
@pytest.mark.parametrize("system, stem", [("gamecube", WIND), ("wii", "Sports Pack (USA)")])
def test_an_iso_in_an_archive_becomes_an_rvz_when_dolphin_tool_is_there(lib, dolphin, system, stem):
    c = convert.look(lib, system, stem)
    assert (c.how, c.disc, c.files, c.problem) == ("rvz", "", (stem + ".rvz",), "")
    assert c.work == 3 * len(ISO) and c.bytes == len(ISO)               # unpack, read back, convert


def test_without_dolphin_tool_the_iso_is_unpacked_as_it_is(lib, no_dolphin):
    c = convert.look(lib, "gamecube", WIND)
    assert (c.how, c.files, c.problem) == ("extract", (WIND + ".iso",), "")


def test_the_rvz_is_made_with_the_given_settings_and_the_work_folder_is_cleared(lib, dolphin):
    seen = []
    c = run(lib, "gamecube", WIND, lambda phase, done: seen.append((phase, done)))
    folder = lib.root / "gamecube"
    assert (folder / (WIND + ".rvz")).read_bytes() == b"RVZ\x01" + ISO[4:]
    assert (folder / (WIND + ".7z")).exists() and lib.has("gamecube", WIND + ".rvz")
    (line,) = dolphin()
    import re
    assert re.fullmatch(r"convert -i \S.*/" + re.escape(WIND) + r"\.iso -o .*/" + re.escape(WIND)
                        + r"\.rvz\.part -f rvz -b 131072 -c zstd -l 5", line)
    assert leftovers(lib) == []
    assert list(dict.fromkeys(p for p, _ in seen)) == ["unpacking", "compressing", "verifying"]
    assert [d for _, d in seen] == sorted(d for _, d in seen) and seen[-1] == ("verifying", c.work)


def test_a_failing_dolphin_tool_leaves_nothing_behind(lib, dolphin):
    dolphin.install("fail")
    with pytest.raises(convert.ConvertError, match="dolphin-tool could not make the RVZ: Error: could not read the disc"):
        run(lib, "gamecube", WIND)
    assert sorted(p.name for p in (lib.root / "gamecube").iterdir() if WIND in p.name) == [WIND + ".7z"]
    assert leftovers(lib) == [] and not lib.has("gamecube", WIND + ".rvz")


@pytest.mark.parametrize("mode", ["empty", "nothing"])
def test_an_empty_or_missing_output_counts_as_a_failure(lib, dolphin, mode):
    dolphin.install(mode)
    with pytest.raises(convert.ConvertError, match="could not make the RVZ: it gave no reason"):
        run(lib, "gamecube", WIND)
    assert sorted(p.name for p in (lib.root / "gamecube").iterdir() if WIND in p.name) == [WIND + ".7z"]
    assert leftovers(lib) == []


def test_stopping_while_it_compresses_leaves_nothing_behind(lib, dolphin):
    class Stop(Exception):
        pass

    def report(phase, done):
        if phase == "compressing":
            raise Stop

    with pytest.raises(Stop):
        run(lib, "gamecube", WIND, report)
    assert sorted(p.name for p in (lib.root / "gamecube").iterdir() if WIND in p.name) == [WIND + ".7z"]
    assert leftovers(lib) == []


def test_only_an_iso_is_made_into_an_rvz(lib, dolphin):
    c = convert.look(lib, "gamecube", "Compressed (USA)")
    assert (c.how, c.files) == ("extract", ("Compressed (USA).gcz",))
    assert convert.look(lib, "psp", "Lumines (USA)").how == "extract"
    assert dolphin() == []


def test_a_system_that_keeps_chd_still_makes_chd(lib, dolphin, chdman):
    pack(lib.root / "ps2", "Shadow (USA).7z", {"Shadow (USA).iso": ISO})
    lib.scan()
    assert convert.look(lib, "ps2", "Shadow (USA)").how == "chd"


# ---------------------------------------------------------------- an iso the library has
def test_a_loose_gamecube_iso_can_become_an_rvz_and_stays(lib, dolphin):
    c = convert.look_loose(lib, "gamecube", "Loose (USA)")
    assert (c.how, c.files, c.work) == ("rvz", ("Loose (USA).rvz",), len(ISO))
    convert.run(lib, c, lib.cache, None)
    assert (lib.root / "gamecube" / "Loose (USA).iso").read_bytes() == ISO
    assert (lib.root / "gamecube" / "Loose (USA).rvz").read_bytes() == b"RVZ\x01" + ISO[4:]
    assert f"-i {lib.root}/gamecube/Loose (USA).iso " in dolphin()[0] and not lib.cache.exists()
    forms = lib.forms("gamecube")["Loose (USA)"]
    assert pick_device_form(systems.get("gamecube"), forms, set()).ext == ".rvz"


def test_a_loose_wii_iso_too(lib, dolphin):
    assert convert.look_loose(lib, "wii", "Loose Wii (USA)").files == ("Loose Wii (USA).rvz",)


def test_without_dolphin_tool_a_loose_iso_has_nothing_to_make(lib, no_dolphin):
    assert convert.look_loose(lib, "gamecube", "Loose (USA)") is None


# ---------------------------------------------------------------- in a sync
@pytest.fixture
def w(world, dolphin):
    pack(world.lib_root / "gamecube", WIND + ".7z", {WIND + ".iso": ISO})
    return world.refresh()


def test_a_sync_makes_the_rvz_and_copies_it(w, tmp_path):
    tick(w, ("gamecube", WIND))
    p = plan(w)
    assert [(i.files, i.convert.how, i.convert.work) for i in p.adds] == [((WIND + ".rvz",), "rvz", 3 * len(ISO))]
    sync = Run(w, tmp_path / "run")
    assert sync.start().go() == "done"
    assert [s[:3] for s in sync.steps()] == [("convert", WIND, "done"), ("push", WIND + ".rvz", "done"),
                                             ("refresh", "", "done")]
    assert w.dev_path("gamecube", WIND + ".rvz").read_bytes() == b"RVZ\x01" + ISO[4:]
    assert (w.lib_root / "gamecube" / (WIND + ".rvz")).exists()
    assert [e.bytes_done for e in sync.events] == sorted(e.bytes_done for e in sync.events)


def test_a_sync_without_dolphin_tool_copies_the_unpacked_iso(w, tmp_path, no_dolphin):
    tick(w, ("gamecube", WIND))
    sync = Run(w, tmp_path / "run")
    assert sync.start().go() == "done"
    assert w.dev_path("gamecube", WIND + ".iso").read_bytes() == ISO
