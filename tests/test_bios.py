"""The BIOS check: the data file, recognising files on the handheld by checksum, and the command line."""
import hashlib
import json
import re

import pytest

from jewelcase import cli
from jewelcase.core import bios, systems

from conftest import CARD

PSX = b"made-up psx bios bytes"            # invented bytes, not real BIOS data
PSX_MD5 = hashlib.md5(PSX).hexdigest()
ARM = "/sdcard/Android/data/com.armsx2/files/bios/SCPH-30004R.rom0"


def entry(folder, name, data, need="needed", **extra):
    return {"system": [{"folder": folder, "need": need, **extra,
                        "file": [{"name": name, "size": len(data), "md5": hashlib.md5(data).hexdigest()}]}]}


def put(world, path, data):
    p = world.dev._p(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def check(world, *extras, rows=(), packages=()):
    data = bios.load(*extras)
    return {r.folder: r for r in bios.check_device(world.dev, world.store, world.serial, CARD, world.profile.rom_root,
                                                   data, rows, set(packages))}


# ---------------------------------------------------------------- the shipped data
def test_every_checksum_in_the_data_file_is_32_hex_characters_and_every_size_is_positive():
    data = bios.load()
    files = [f for s in data.systems.values() for f in s.files]
    assert files
    assert all(re.fullmatch(r"[0-9a-f]{32}", f.md5) and f.size > 0 and f.name for f in files)


def test_every_system_in_the_data_file_is_one_jewelcase_knows():
    data = bios.load()
    known = systems.known_folders()
    assert set(data.systems) <= known
    assert {fw.folder for fw in data.firmware} <= known
    assert all(s.need in ("needed", "optional") for s in data.systems.values())
    assert {s.folder for s in data.systems.values() if s.files} == set(data.systems)


def test_the_systems_the_owner_asked_for_are_in_the_data_with_the_right_need():
    data = bios.load()
    need = {f: s.need for f, s in data.systems.items()}
    for folder in ("psx", "ps2", "dreamcast", "saturn", "segacd", "pcengine", "neogeocd"):
        assert need[folder] == "needed"
    for folder in ("gba", "nds", "gamecube"):
        assert need[folder] == "optional"


def test_a_user_file_adds_to_a_system_without_dropping_the_shipped_files(tmp_path):
    mine = tmp_path / "bios.toml"
    mine.write_text('[[system]]\nfolder = "psx"\n[[system.file]]\nname = "mine.bin"\nsize = 10\n'
                    'md5 = "' + "a" * 32 + '"\n')
    shipped, merged = bios.load().systems["psx"], bios.load(mine).systems["psx"]
    assert len(merged.files) == len(shipped.files) + 1
    assert merged.need == "needed" and merged.hints == shipped.hints


def test_a_broken_user_file_is_ignored(tmp_path):
    mine = tmp_path / "bios.toml"
    mine.write_text("this is not toml [")
    assert bios.load(mine).systems.keys() == bios.load().systems.keys()


# ---------------------------------------------------------------- recognising
def test_a_file_is_verified_by_its_checksum_whatever_it_is_called(world):
    put(world, "/sdcard/duckstation/bios/" + PSX_MD5, PSX)          # DuckStation names files by their hash
    r = check(world, entry("psx", "scph-made-up.bin", PSX))["psx"]
    assert r.state == "verified" and r.need == "needed"
    assert [f.path for f in r.files] == ["/sdcard/duckstation/bios/" + PSX_MD5]
    assert r.files[0].known_as == "scph-made-up.bin"


def test_a_bios_like_name_with_an_unknown_checksum_is_found_but_not_recognised(world):
    put(world, "/sdcard/RetroArch/system/scph9999.bin", b"x" * 524288)
    r = check(world)["psx"]
    assert r.state == "unrecognised"
    assert r.files[0].state == "unrecognised"


def test_a_four_megabyte_ps2_dump_with_an_unknown_checksum_is_found_but_not_recognised(world):
    put(world, f"{CARD}/bios-extras/armsx2-bios/SCPH-30004R.rom0", b"q" * (4 * 1024 * 1024))
    r = check(world)["ps2"]
    assert r.state == "unrecognised"
    assert r.files[0].path == f"{CARD}/bios-extras/armsx2-bios/SCPH-30004R.rom0"


def test_a_file_with_a_bios_name_but_the_wrong_size_is_not_taken_for_a_bios(world):
    put(world, "/sdcard/RetroArch/system/scph-notes.bin", b"tiny")
    assert check(world)["psx"].state == "missing"


def test_nothing_found_is_missing(world):
    r = check(world)
    assert r["dreamcast"].state == "missing" and r["dreamcast"].files == ()


def test_a_system_that_only_benefits_is_listed_as_optional(world):
    assert check(world)["gba"].need == "optional"
    assert check(world)["psx"].need == "needed"


def test_files_are_found_in_retroarch_system_subfolders_and_bios_named_folders(world):
    put(world, "/sdcard/RetroArch/system/dc/dc_boot.bin", b"d" * 4)
    put(world, f"{CARD}/bios-extras/dolphin-GC-USA/IPL.bin", b"gc-ipl")
    r = check(world, entry("dreamcast", "dc_boot.bin", b"d" * 4), entry("gamecube", "IPL.bin", b"gc-ipl", "optional"))
    assert r["dreamcast"].state == "incomplete" and r["gamecube"].state == "verified"   # the flash file is missing too
    assert r["gamecube"].files[0].path == f"{CARD}/bios-extras/dolphin-GC-USA/IPL.bin"


def test_an_installed_emulators_own_bios_folder_is_looked_in(world):
    put(world, ARM, PSX)
    data = entry("ps2", "x.rom0", PSX)
    assert check(world, data)["ps2"].state == "missing"             # the emulator is not known to be installed
    r = check(world, data, packages={"com.armsx2"})["ps2"]
    assert r.state == "verified" and r.files[0].path == ARM


def test_the_same_file_reached_by_two_folders_is_listed_once(world):
    put(world, "/sdcard/RetroArch/system/bios/b.bin", PSX)
    r = check(world, entry("psx", "b.bin", PSX))["psx"]
    assert len(r.files) == 1


def test_a_folder_that_cannot_be_read_is_skipped(world):
    put(world, "/sdcard/RetroArch/system/a.bin", PSX)
    put(world, "/sdcard/bios-locked/b.bin", PSX)
    world.dev.unreadable = {"/sdcard/bios-locked"}
    r = check(world, entry("psx", "a.bin", PSX))["psx"]
    assert [f.path for f in r.files] == ["/sdcard/RetroArch/system/a.bin"]


def test_a_file_that_cannot_be_opened_is_skipped(world):
    put(world, "/sdcard/RetroArch/system/a.bin", PSX)
    world.dev.unreadable_files = {"/sdcard/RetroArch/system/a.bin"}
    assert check(world, entry("psx", "a.bin", PSX))["psx"].state == "missing"


def test_nothing_bigger_than_sixteen_megabytes_is_read(world):
    big = world.dev._p("/sdcard/RetroArch/system/scph-big.bin")
    big.parent.mkdir(parents=True, exist_ok=True)
    with open(big, "wb") as f:
        f.truncate(17 * 1024 * 1024)
    check(world)
    assert not [c for c in world.dev.calls if c[0] == "md5"]


def test_a_files_hash_is_remembered_until_the_file_changes(world):
    put(world, "/sdcard/RetroArch/system/a.bin", PSX)
    data = entry("psx", "a.bin", PSX)
    md5s = lambda: len([c for c in world.dev.calls if c[0] == "md5"])  # noqa: E731
    check(world, data)
    assert md5s() == 1
    check(world, data)
    assert md5s() == 1
    put(world, "/sdcard/RetroArch/system/a.bin", PSX)               # same size, but written again
    import os
    p = world.dev._p("/sdcard/RetroArch/system/a.bin")
    os.utime(p, (p.stat().st_mtime + 100, p.stat().st_mtime + 100))
    check(world, data)
    assert md5s() == 2


def test_files_that_are_not_candidates_are_never_read(world):
    put(world, "/sdcard/RetroArch/system/readme.txt", b"hello")
    check(world)
    assert not [c for c in world.dev.calls if c[0] == "md5"]


# ---------------------------------------------------------------- games, order and firmware
def test_systems_with_games_on_the_handheld_come_first(world):
    r = bios.check_device(world.dev, world.store, world.serial, CARD, world.profile.rom_root, bios.load(),
                          world.rows(), set())
    first = [x.folder for x in r if x.has_games]
    assert "ps2" in first and "psx" in first
    assert [x.has_games for x in r] == sorted((x.has_games for x in r), reverse=True)
    assert next(x for x in r if x.folder == "dreamcast").has_games is False


def test_a_ticked_game_counts_even_when_it_is_not_on_the_handheld_yet(world):
    row = world.row("gamecube", "Pikmin 2 (USA)")
    assert row.on_device is False
    world.store.set_wanted(world.serial, "gamecube", "Pikmin 2 (USA)", True)
    r = check(world, rows=world.rows())
    assert r["gamecube"].has_games and not r["dreamcast"].has_games


def test_mega_drive_folder_games_that_are_discs_count_for_sega_cd(world, tree):
    tree(world.dev.root / CARD.lstrip("/"), {"megadrive": {"Disc Game (USA).chd": b"d" * 50, "Cart (USA).md": b"c" * 50}})
    world.refresh()
    assert check(world, rows=world.rows())["segacd"].has_games


def test_a_mega_drive_cartridge_does_not_count_for_sega_cd(world, tree):
    tree(world.dev.root / CARD.lstrip("/"), {"megadrive": {"Cart (USA).md": b"c" * 50}})
    world.refresh()
    assert not check(world, rows=world.rows())["segacd"].has_games


def test_ps3_firmware_is_installed_when_its_folder_can_be_seen_and_left_out_otherwise(world):
    assert "ps3" not in check(world)
    (world.dev._p("/sdcard/Android/data/aenu.aps3e/files/aps3e/config/dev_flash")).mkdir(parents=True)
    r = check(world)["ps3"]
    assert r.state == "installed" and r.files[0].known_as == "aPS3e"


# ---------------------------------------------------------------- command line
def run(capsys, demo, *args):
    code = cli.main(["--demo-dir", str(demo), *args])
    out = capsys.readouterr()
    return code, out.out


def test_the_bios_command_shows_a_verified_an_unrecognised_and_a_missing_system(capsys, tmp_path):
    code, out = run(capsys, tmp_path / "demo", "--json", "bios")
    assert code == 0
    states = {s["folder"]: s["state"] for s in json.loads(out)["systems"]}
    assert states["psx"] == "verified"
    assert states["ps2"] == "unrecognised"
    assert states["dreamcast"] == "missing"
    assert "pointed at the folder" in json.loads(out)["note"]


def test_the_bios_command_prints_text_that_says_where_files_are(capsys, tmp_path):
    code, out = run(capsys, tmp_path / "demo", "bios")
    assert code == 0
    assert "PlayStation (needed): verified" in out and "found, not recognised" in out
    assert "/sdcard/duckstation/bios/" in out


def test_the_bios_command_never_copies_anything(capsys, tmp_path):
    run(capsys, tmp_path / "demo", "bios")
    before = sorted(p.name for p in (tmp_path / "demo").rglob("*") if p.is_file())
    run(capsys, tmp_path / "demo", "bios")
    assert sorted(p.name for p in (tmp_path / "demo").rglob("*") if p.is_file()) == before
    assert not any(p.name == "demo-psx-bios.bin" for p in (tmp_path / "demo" / "library").rglob("*"))


def test_the_bios_command_without_a_device_says_so(capsys, tmp_path):
    from argparse import Namespace
    from jewelcase.core.session import open_session
    session = open_session(demo_dir=tmp_path / "demo")
    assert bios.cmd_bios(session, Namespace(json=False), cli._out) == 2
    assert "No device connected" in capsys.readouterr().out


# ---------------------------------------------------------------- files that are wanted together, or instead of each other
def two_roles(a=b"boot-bytes", b=b"flash-bytes"):
    return {"system": [{"folder": "dreamcast", "need": "needed", "file": [
        {"name": "dc_boot.bin", "size": len(a), "md5": hashlib.md5(a).hexdigest(), "role": "boot"},
        {"name": "boot.bin", "size": len(a), "md5": hashlib.md5(a).hexdigest(), "role": "boot"},
        {"name": "flash.bin", "size": len(b), "md5": hashlib.md5(b).hexdigest(), "role": "flash"}]}]}


def test_a_system_that_needs_two_files_is_incomplete_with_only_one_of_them(world):
    put(world, "/sdcard/RetroArch/system/dc/dc_boot.bin", b"boot-bytes")
    r = check(world, two_roles())["dreamcast"]
    assert r.state == "incomplete"
    assert "flash.bin" in r.missing and "boot" not in r.missing


def test_a_system_is_verified_when_every_role_has_a_file(world):
    put(world, "/sdcard/RetroArch/system/dc/dc_boot.bin", b"boot-bytes")
    put(world, "/sdcard/RetroArch/system/dc/flash.bin", b"flash-bytes")
    r = check(world, two_roles())["dreamcast"]
    assert r.state == "verified" and r.missing == ""


def test_alternatives_of_one_role_need_only_one_file(world):
    put(world, "/sdcard/RetroArch/system/a.bin", PSX)
    r = check(world, {"system": [{"folder": "psx", "need": "needed", "file": [
        {"name": "a.bin", "size": len(PSX), "md5": PSX_MD5},
        {"name": "b.bin", "size": 9, "md5": "b" * 32}]}]})["psx"]
    assert r.state == "verified"


def test_the_shipped_data_wants_several_files_where_the_system_does(world):
    data = bios.load()
    roles = lambda f: {k.role for k in data.systems[f].files}  # noqa: E731
    assert len(roles("dreamcast")) == 2 and len(roles("nds")) == 3
    assert len(roles("psx")) == len(roles("saturn")) == len(roles("segacd")) == 1


def test_a_user_file_without_a_role_joins_the_systems_first_role(tmp_path):
    mine = tmp_path / "bios.toml"
    mine.write_text('[[system]]\nfolder = "dreamcast"\n[[system.file]]\nname = "mine.bin"\nsize = 10\n'
                    'md5 = "' + "a" * 32 + '"\n')
    files = bios.load(mine).systems["dreamcast"].files
    assert next(f for f in files if f.name == "mine.bin").role == files[0].role


def test_one_file_known_by_two_names_is_listed_once(world):
    put(world, "/sdcard/RetroArch/system/dc/dc_boot.bin", b"boot-bytes")
    r = check(world, two_roles())["dreamcast"]
    assert len(r.files) == 1
    assert r.files[0].known_as == "dc_boot.bin, boot.bin"


def test_a_name_hint_without_a_size_never_counts(world):
    put(world, "/sdcard/RetroArch/system/firmware-notes.txt", b"hello")
    assert check(world, {"system": [{"folder": "nds", "hints": ["firmware*"]}]})["nds"].state == "missing"
    assert all(s.hint_sizes for s in bios.load().systems.values() if s.hints)


# ---------------------------------------------------------------- the cost of looking
def test_the_search_asks_the_handheld_once_per_root_not_once_per_folder(world):
    put(world, f"{CARD}/bios-extras/dolphin-GC-USA/IPL.bin", b"gc-ipl")
    put(world, "/sdcard/Documents/stuff/more/x.txt", b"x")
    asked = []
    real = world.dev.list_dirs
    world.dev.list_dirs = lambda path: asked.append(path) or real(path)
    folders = bios.locations(world.dev, CARD, world.profile.rom_root, {"com.armsx2"})
    assert f"{CARD}/bios-extras" in folders or any(f.endswith("bios-extras") for f in folders)
    assert asked == []
    assert len([c for c in world.dev.calls if c[0] == "tree"]) <= 3


def test_the_search_skips_games_and_app_folders_and_keeps_folders_named_bios(world):
    put(world, f"{world.profile.rom_root}/psx/bios-fake/a.bin", b"x")      # inside the games folder: never entered
    put(world, "/sdcard/ES-DE/bios-in-esde/a.bin", b"x")
    put(world, "/sdcard/Games/Emu/bios/a.bin", b"x")
    folders = bios.locations(world.dev, CARD, world.profile.rom_root, set())
    assert "/sdcard/Games/Emu/bios" in folders
    assert not any("bios-fake" in f or "bios-in-esde" in f for f in folders)
