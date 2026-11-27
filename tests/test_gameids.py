"""Game IDs read out of disc images: PlayStation 2, PSP and GameCube, and remembered once per file."""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from jewelcase.core import gameids
from gameimages import SECTOR, ps2_disc, psp_disc, tiny_iso

NEEDS_CHDMAN = pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")


@pytest.fixture(autouse=True)
def scratch_in_the_test_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))     # /tmp is kept in memory on this machine


def gc_head(game_id=b"GM8E01", wii=False) -> bytes:
    head = bytearray(0x440)
    head[:6] = game_id
    head[0x18:0x1C] = b"\x5D\x1C\x9E\xA3" if wii else b"\0\0\0\0"
    head[0x1C:0x20] = b"\0\0\0\0" if wii else b"\xC2\x33\x9F\x3D"
    return bytes(head)


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- reading one file
def test_a_ps2_iso_gives_the_id_from_its_system_cnf(tmp_path):
    assert gameids.read_id(put(tmp_path / "a.iso", ps2_disc()), "ps2") == "SLUS-20312"


def test_a_ps2_id_is_found_whatever_the_boot_line_looks_like(tmp_path):
    for boot in (b"BOOT2 = cdrom0:\\SLES_523.38;1", b"BOOT2=cdrom0:\\SCPS_150.17;1\r\n", b"boot2 = cdrom0:\\SLUS_212.03;1"):
        got = gameids.read_id(put(tmp_path / "a.iso", ps2_disc(boot)), "ps2")
        assert got in ("SLES-52338", "SCPS-15017", "SLUS-21203")


def test_a_psp_iso_gives_the_id_from_umd_data(tmp_path):
    assert gameids.read_id(put(tmp_path / "a.iso", psp_disc()), "psp") == "ULUS10509"


def test_a_gamecube_iso_gives_the_first_six_bytes(tmp_path):
    assert gameids.read_id(put(tmp_path / "a.iso", gc_head()), "gamecube") == "GM8E01"
    assert gameids.read_id(put(tmp_path / "b.iso", gc_head(b"RSPE01", wii=True)), "wii") == "RSPE01"


def test_a_file_that_is_not_a_disc_with_an_id_gives_none(tmp_path):
    assert gameids.read_id(put(tmp_path / "a.iso", b"x" * 5000), "ps2") is None
    assert gameids.read_id(put(tmp_path / "b.iso", tiny_iso({"README.TXT": b"hi"})), "ps2") is None
    assert gameids.read_id(put(tmp_path / "c.iso", ps2_disc(b"VER = 1.00\n")), "ps2") is None
    assert gameids.read_id(put(tmp_path / "d.iso", b"GM8E01" + bytes(100)), "gamecube") is None   # no disc magic
    assert gameids.read_id(put(tmp_path / "e.cso", ps2_disc()), "ps2") is None                    # not a format read here


def test_only_a_few_sectors_of_an_iso_are_read(tmp_path, monkeypatch):
    path = put(tmp_path / "a.iso", ps2_disc() + bytes(8 * 1024 * 1024))
    seen = []
    real = gameids._file_reader(path)
    monkeypatch.setattr(gameids, "_file_reader", lambda p: lambda off, n: seen.append(n) or real(off, n))
    assert gameids.read_id(path, "ps2") == "SLUS-20312"
    assert sum(seen) <= 16 * SECTOR


def test_a_missing_file_is_an_error_so_that_it_is_not_remembered_as_without_an_id(tmp_path):
    with pytest.raises(OSError):
        gameids.read_id(tmp_path / "gone.iso", "ps2")


# ---------------------------------------------------------------- compressed discs
@NEEDS_CHDMAN
def test_a_dvd_chd_is_read_by_extracting_only_the_few_ranges_needed(tmp_path, monkeypatch):
    iso = put(tmp_path / "game.iso", ps2_disc())
    chd = tmp_path / "game.chd"
    subprocess.run(["chdman", "createdvd", "-i", str(iso), "-o", str(chd)], check=True, capture_output=True)
    log = tmp_path / "chdman.log"
    wrapper = tmp_path / "chdman-spy"
    wrapper.write_text(f"#!{sys.executable}\nimport os, sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                       f"os.execv({shutil.which('chdman')!r}, ['chdman'] + sys.argv[1:])\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(gameids, "_chdman", lambda: str(wrapper))
    assert gameids.read_id(chd, "ps2") == "SLUS-20312"
    lengths = [int(line.split("-ib ")[1].split()[0]) for line in log.read_text().splitlines()]
    assert lengths and sum(lengths) <= 16 * SECTOR and all(line.startswith("extractdvd") for line in log.read_text().splitlines())
    with pytest.raises(OSError):                            # chdman cannot read it: not the same as "no ID"
        gameids.read_id(put(tmp_path / "p.chd", b"not a chd"), "ps2")


@NEEDS_CHDMAN
def test_a_psp_dvd_chd_gives_its_id(tmp_path):
    iso = put(tmp_path / "game.iso", psp_disc())
    subprocess.run(["chdman", "createdvd", "-i", str(iso), "-o", str(tmp_path / "game.chd")], check=True,
                   capture_output=True)
    assert gameids.read_id(tmp_path / "game.chd", "psp") == "ULUS10509"


def test_a_chd_without_chdman_cannot_be_read(tmp_path, monkeypatch):
    monkeypatch.setattr(gameids, "_chdman", lambda: None)
    with pytest.raises(gameids.Unreadable):
        gameids.read_id(put(tmp_path / "a.chd", b"MComprHD" + bytes(100)), "ps2")


def test_an_rvz_is_asked_of_dolphin_tool(tmp_path, monkeypatch):
    tool = tmp_path / "dolphin-tool"
    tool.write_text(f"#!{sys.executable}\nimport sys\nassert sys.argv[1:3] == ['header', '-i']\n"
                    "print('Internal Name: Some Game')\nprint('Game ID: GALE01')\nprint('Region: NTSC-U')\n")
    tool.chmod(0o755)
    monkeypatch.setattr(gameids, "_dolphin_tool", lambda: str(tool))
    assert gameids.read_id(put(tmp_path / "a.rvz", b"RVZ\x01" + bytes(100)), "gamecube") == "GALE01"
    monkeypatch.setattr(gameids, "_dolphin_tool", lambda: None)
    with pytest.raises(gameids.Unreadable):
        gameids.read_id(tmp_path / "a.rvz", "gamecube")


# ---------------------------------------------------------------- reading once, remembering
@pytest.fixture
def spy(monkeypatch):
    calls = []
    real = gameids.read_id
    monkeypatch.setattr(gameids, "read_id", lambda path, system: calls.append(path.name) or real(path, system))
    return calls


def lib_file(world, system, name, data):
    put(world.lib_root / system / name, data)


def test_each_file_is_read_once_and_a_second_ask_does_not_read_it_again(world, spy):
    lib_file(world, "ps2", "Wrath (USA).iso", ps2_disc())
    world.refresh()
    rows = world.rows()
    assert gameids.read_missing(world.library, world.store, rows, {"ps2"}) >= 1
    assert spy.count("Wrath (USA).iso") == 1
    spy.clear()
    assert gameids.read_missing(world.library, world.store, rows, {"ps2"}) == 0 and spy == []
    found = gameids.known(world.library, world.store, rows)
    assert [(r.system, r.stem, r.display) for r in found["SLUS20312"]] == [("ps2", "Wrath (USA)", "SLUS-20312")]


def test_a_file_that_changed_is_read_again(world, spy):
    lib_file(world, "ps2", "Wrath (USA).iso", ps2_disc())
    world.refresh()
    gameids.read_missing(world.library, world.store, world.rows(), {"ps2"})
    spy.clear()
    lib_file(world, "ps2", "Wrath (USA).iso", ps2_disc(b"BOOT2 = cdrom0:\\SLUS_999.99;1\r\n"))
    st = os.stat(world.lib_root / "ps2" / "Wrath (USA).iso")
    os.utime(world.lib_root / "ps2" / "Wrath (USA).iso", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    world.refresh()
    assert gameids.read_missing(world.library, world.store, world.rows(), {"ps2"}) == 1
    assert spy == ["Wrath (USA).iso"]      # and only that file
    assert "SLUS99999" in gameids.known(world.library, world.store, world.rows())


def test_a_game_without_an_id_is_remembered_as_that_and_not_read_again(world, spy):
    lib_file(world, "ps2", "Plain (USA).iso", b"x" * 5000)
    world.refresh()
    gameids.read_missing(world.library, world.store, world.rows(), {"ps2"})
    assert spy.count("Plain (USA).iso") == 1
    assert world.store.lib_game_id("ps2", "Plain (USA).iso", 5000, (world.lib_root / "ps2" / "Plain (USA).iso").stat().st_mtime_ns) == ""
    spy.clear()
    assert gameids.read_missing(world.library, world.store, world.rows(), {"ps2"}) == 0 and spy == []
    assert all(r.stem != "Plain (USA)" for refs in gameids.known(world.library, world.store, world.rows()).values() for r in refs)


def test_archives_and_games_only_on_the_handheld_are_not_opened(world, spy):
    world.refresh()
    gameids.read_missing(world.library, world.store, world.rows(), {"ps2", "psp", "gamecube", "wii"})
    assert not [n for n in spy if n.endswith(".7z") or n.startswith("Device Only")]


def test_nothing_is_read_while_the_library_is_offline(world, spy):
    lib_file(world, "ps2", "Wrath (USA).iso", ps2_disc())
    world.refresh()
    rows = world.rows()
    shutil.rmtree(world.lib_root)
    assert gameids.read_missing(world.library, world.store, rows, {"ps2"}) == 0 and spy == []


# ---------------------------------------------------------------- what a save's name says
@pytest.mark.parametrize("name,system,code", [
    ("SLUS-20312 (D224D348).00.p2s", "ps2", "SLUS20312"),
    ("SLUS_203.12.ps2", "ps2", "SLUS20312"),
    ("ULUS10509001", "psp", "ULUS10509"),
    ("ULUS100720000", "psp", "ULUS10072"),
    ("mcd001.ps2", "ps2", None),
    ("01-GM8E-Super Game.gci", "gamecube", "GM8E"),
    ("GM8E01.gci", "gamecube", "GM8E01"),
    ("Super Game.gci", "gamecube", None),
])
def test_the_id_a_save_is_named_by(name, system, code):
    assert gameids.save_code(name, system) == code


def test_a_four_letter_gamecube_code_matches_every_game_that_starts_with_it():
    ids = {"GM8E01": [gameids.GameRef("gamecube", "Sunny (USA)", "Sunny", "GM8E01")],
           "GALE01": [gameids.GameRef("gamecube", "Other (USA)", "Other", "GALE01")]}
    assert [r.stem for r in gameids.match_save("01-GM8E-Sunny.gci", ["gamecube", "wii"], ids)] == ["Sunny (USA)"]
    assert gameids.match_save("01-ZZZZ-Nothing.gci", ["gamecube"], ids) == []
