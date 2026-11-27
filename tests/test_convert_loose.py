"""A disc image the library already has, made into the smaller form the handheld's games are kept in."""
import shutil

import pytest

from jewelcase.core import convert, systems
from jewelcase.core.db import Store
from jewelcase.core.library import Library, pick_device_form
from test_convert import CARD, ISO, TRACKS, chdman, no_chdman  # noqa: F401  (the stub chdman)

LOOSE = "Loose Disc (USA)"


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    for system in ("ps2", "psx", "psp", "ps3"):
        (root / system).mkdir(parents=True)
    (root / "ps2" / (LOOSE + ".iso")).write_bytes(ISO)
    (root / "ps2" / "Packed (USA).chd").write_bytes(b"x")
    (root / "ps2" / "Packed (USA).iso").write_bytes(ISO)
    (root / "psx" / "Racer (USA).cue").write_bytes(TRACKS["Racer (USA).cue"])
    for name in ("Racer (USA) (Track 1).bin", "Racer (USA) (Track 2).bin"):
        (root / "psx" / name).write_bytes(TRACKS[name])
    (root / "psp" / "Lumines (USA).iso").write_bytes(ISO)
    (root / "ps3" / "Persona (USA).iso").write_bytes(ISO)
    library = Library(root, Store(":memory:"))
    library.scan()
    library.cache = tmp_path / "cache"
    return library


def made(lib, system="ps2"):
    return sorted(p.name for p in (lib.root / system).iterdir())


def test_a_loose_iso_on_a_system_that_keeps_chd_can_become_a_dvd_chd(lib, chdman):
    c = convert.look_loose(lib, "ps2", LOOSE)
    assert (c.archive, c.how, c.disc, c.files, c.problem) == ("", "chd", "dvd", (LOOSE + ".chd",), "")
    assert c.discs[0].loose and c.work == 2 * len(ISO) and c.bytes == len(ISO)      # compress and check, no unpacking


def test_a_cue_with_its_tracks_becomes_a_cd_chd(lib, chdman):
    c = convert.look_loose(lib, "psx", "Racer (USA)")
    assert (c.how, c.disc, c.files) == ("chd", "cd", ("Racer (USA).chd",))
    assert c.bytes == sum(len(b) for b in TRACKS.values())


def test_making_it_keeps_the_original_and_never_unpacks_anything(lib, chdman):
    seen = []
    c = convert.look_loose(lib, "ps2", LOOSE)
    assert convert.run(lib, c, lib.cache, lambda phase, done: seen.append((phase, done))) == (LOOSE + ".chd",)
    assert (lib.root / "ps2" / (LOOSE + ".iso")).read_bytes() == ISO                    # the original stays
    assert (lib.root / "ps2" / (LOOSE + ".chd")).read_bytes() == b"MComprHDcreatedvd" + ISO
    assert lib.has("ps2", LOOSE + ".chd") and lib.has("ps2", LOOSE + ".iso")
    assert chdman() == ["createdvd", "verify"] and not lib.cache.exists()
    assert list(dict.fromkeys(p for p, _ in seen)) == ["compressing", "verifying"]
    assert [d for _, d in seen] == sorted(d for _, d in seen) and seen[-1] == ("verifying", c.work)
    assert not [p for p in (lib.root / "ps2").iterdir() if p.suffix == ".part"]


def test_a_cue_is_made_from_the_files_in_the_library_and_all_of_them_stay(lib, chdman):
    convert.run(lib, convert.look_loose(lib, "psx", "Racer (USA)"), lib.cache)
    assert made(lib, "psx") == sorted([*TRACKS, "Racer (USA).chd"])


def test_the_form_picker_then_prefers_the_chd(lib, chdman):
    info = systems.get("ps2")
    forms = lambda: lib.forms("ps2")[LOOSE]
    assert pick_device_form(info, forms(), set()).ext == ".iso"
    convert.run(lib, convert.look_loose(lib, "ps2", LOOSE), lib.cache)
    assert pick_device_form(info, forms(), set()).ext == ".chd"
    assert pick_device_form(info, forms(), {".iso"}).ext == ".iso"          # a game already on the handheld stays


def test_a_game_that_already_has_a_chd_has_nothing_better_to_make(lib, chdman):
    assert convert.look_loose(lib, "ps2", "Packed (USA)") is None


@pytest.mark.parametrize("system, stem", [("psp", "Lumines (USA)"), ("ps3", "Persona (USA)"), ("ps2", "No Such (USA)")])
def test_where_the_iso_is_what_the_system_keeps_there_is_nothing_to_make(lib, chdman, system, stem):
    assert convert.look_loose(lib, system, stem) is None


def test_without_chdman_there_is_nothing_to_make(lib, no_chdman):
    assert convert.look_loose(lib, "ps2", LOOSE) is None


def test_a_failure_leaves_the_original_alone_and_nothing_else(lib, chdman):
    chdman.install(fail_verify=True)
    with pytest.raises(convert.ConvertError, match="did not pass chdman's check"):
        convert.run(lib, convert.look_loose(lib, "ps2", LOOSE), lib.cache)
    assert made(lib) == sorted(["Packed (USA).chd", "Packed (USA).iso", LOOSE + ".iso"])
    assert not lib.has("ps2", LOOSE + ".chd")


def test_it_does_not_start_without_room(lib, chdman, monkeypatch):
    monkeypatch.setattr(Library, "free_bytes", lambda self: 1000)
    with pytest.raises(convert.ConvertError, match="not enough room in the library"):
        convert.run(lib, convert.look_loose(lib, "ps2", LOOSE), lib.cache)


@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")
def test_the_real_chdman_makes_a_chd_of_a_loose_iso(lib):
    import subprocess
    convert.run(lib, convert.look_loose(lib, "ps2", LOOSE), lib.cache)
    info = subprocess.run(["chdman", "info", "-i", str(lib.root / "ps2" / (LOOSE + ".chd"))],
                          capture_output=True, text=True).stdout
    assert "Tag='DVD '" in info and (lib.root / "ps2" / (LOOSE + ".iso")).read_bytes() == ISO


# ---------------------------------------------------------------- not part of a sync
def test_a_sync_copies_the_iso_as_it_is_and_does_not_convert(world, chdman):
    from jewelcase.core.plan import build_plan
    (world.lib_root / "ps2" / (LOOSE + ".iso")).write_bytes(ISO)
    w = world.refresh()
    w.store.set_wanted(w.serial, "ps2", LOOSE, True)
    p = build_plan(w.rows(), w.dev.storage(CARD), w.library, w.dev.info())
    assert [(i.row.stem, i.files, i.convert) for i in p.adds] == [(LOOSE, (LOOSE + ".iso",), None)]
