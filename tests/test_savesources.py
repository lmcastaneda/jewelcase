"""Finding saves on the device."""
import struct
from dataclasses import replace

import pytest

from jewelcase.core import savesources
from jewelcase.core.device import DeviceError
from jewelcase.core.fakedevice import FakeDevice
from jewelcase.core.savesources import (Source, group_items, load_sources, resolve_root, scan_all, scan_source,
                                     sfo_title)

RA = "/sdcard/RetroArch/saves"
PSP = "/sdcard/Android/data/org.ppsspp.ppsspp/files/PSP/SAVEDATA"
CFG = "/sdcard/Android/data/com.retroarch.aarch64/files/retroarch.cfg"

SRC_SRM = Source(id="ra-saves", package="com.retroarch.aarch64", label="RetroArch", kind="save", root=RA,
                 include=("*.srm", "*/*.srm"), items="name", suffix=r"\.srm$")
SRC_STATE_SAME_ROOT = Source(id="ra-states", package="com.retroarch.aarch64", label="RetroArch", kind="state",
                             root=RA, include=("*.state*", "*.srm"), exclude=("*.png",), items="name",
                             suffix=r"\.state.*$")
SRC_RA = replace(SRC_SRM, root_from=(CFG, "savefile_directory"))
SRC_RA_STATES = Source(id="ra-states", package="com.retroarch.aarch64", label="RetroArch", kind="state",
                       root="/sdcard/RetroArch/states", include=("**/*.state*",), exclude=("**/*.png",),
                       items="name", suffix=r"\.state[^/]*$")
SRC_PSP = Source(id="ppsspp-saves", package="org.ppsspp.ppsspp", label="PPSSPP", kind="save", root=PSP,
                 include=("*/**",), items="folder", advice="Move PPSSPP's memory stick to /sdcard/PSP in its settings.")


@pytest.fixture
def dev(tmp_path, tree):
    d = FakeDevice(tmp_path / "dev")
    tree(d.root, {
        "sdcard/RetroArch/saves": {"x.srm": b"save-x", "aes_keys.txt": b"secret", "bios/b.bin": b"bios",
                                   "core/y.srm": b"save-y", "x.state": b"state-x", "x.state.png": b"thumb",
                                   ".nomedia": b""},
        PSP.lstrip("/"): {"ULUS1/DATA.BIN": b"1", "ULUS2/DATA.BIN": b"2", "ULUS3/DATA.BIN": b"3"},
    })
    return d


def test_bundled_sources_load():
    by = {s.id: s for s in load_sources()}
    assert by["retroarch-saves"].kind == "save" and by["retroarch-saves"].items == "name"
    assert by["retroarch-states"].kind == "state"
    assert by["ppsspp-saves"].items == "folder" and "memory stick" in by["ppsspp-saves"].advice
    assert all(s.package and s.include and s.kind in ("save", "state", "settings") for s in by.values())
    assert all(s.items in ("name", "folder", "file") for s in by.values())


def test_user_sources_extend_and_override(tmp_path):
    extra = tmp_path / "saves.toml"
    extra.write_text('[[source]]\nid = "retroarch-saves"\npackage = "p"\nlabel = "Mine"\nkind = "save"\n'
                     'root = "/x"\ninclude = ["*.sav"]\nitems = "file"\n'
                     '[[source]]\nid = "broken"\n', encoding="utf-8")
    by = {s.id: s for s in load_sources(extra)}
    assert by["retroarch-saves"].label == "Mine" and "broken" not in by and "drastic-saves" in by


def test_only_matching_files_are_saves(dev):
    scan = scan_source(dev, SRC_SRM)
    assert scan.exists and sorted(f.rel for f in scan.files) == ["core/y.srm", "x.srm"]
    assert all(f.source == "ra-saves" and f.size == 6 and f.mtime > 0 for f in scan.files)


def test_excludes_and_nomedia(dev):
    scan = scan_source(dev, replace(SRC_STATE_SAME_ROOT, include=("*",)))
    assert sorted(f.rel for f in scan.files) == ["aes_keys.txt", "x.srm", "x.state"]


def test_patterns_ignore_case_and_span_folders(dev, tree):
    tree(dev.root, {"sdcard/RetroArch/saves": {"deep/er/Z.SRM": b"z"}})
    scan = scan_source(dev, replace(SRC_SRM, include=("**/*.srm",)))
    assert sorted(f.rel for f in scan.files) == ["core/y.srm", "deep/er/Z.SRM", "x.srm"]


def test_missing_folder_is_not_an_error(dev):
    scan = scan_source(dev, replace(SRC_SRM, root="/sdcard/nowhere"))
    assert not scan.exists and scan.files == () and scan.unreadable == ()


def test_unreadable_folders_are_counted(dev):
    dev.unreadable = {PSP + "/ULUS1", PSP + "/ULUS2"}
    scan = scan_source(dev, SRC_PSP)
    assert len(scan.unreadable) == 2 and [f.rel for f in scan.files] == ["ULUS3/DATA.BIN"]
    with pytest.raises(DeviceError):
        dev.read_bytes(PSP + "/ULUS1/DATA.BIN")


def test_unreadable_folder_that_cannot_hold_saves_is_not_counted(dev):
    dev.unreadable = {RA + "/bios"}
    assert scan_source(dev, replace(SRC_SRM, include=("*.srm", "core/*.srm"))).unreadable == ()
    assert scan_source(dev, SRC_SRM).unreadable == (RA + "/bios",)          # "*/*.srm" could be in there


def test_too_large_is_skipped_and_listed(dev, tree, monkeypatch):
    tree(dev.root, {"sdcard/RetroArch/saves": {"big.srm": b"0123456789ABC"}})
    monkeypatch.setattr(savesources, "MAX_FILE", 10)
    scan = scan_source(dev, SRC_SRM)
    assert scan.too_large == ("big.srm",) and "big.srm" not in [f.rel for f in scan.files]


def test_root_from_config_file(dev, tree):
    tree(dev.root, {CFG.rsplit("/", 1)[0].lstrip("/"): {
        "retroarch.cfg": 'video_driver = "vulkan"\nsavefile_directory = "/sdcard/Elsewhere"\n'}})
    assert resolve_root(dev, SRC_RA) == "/sdcard/Elsewhere"
    assert resolve_root(dev, replace(SRC_RA, root_from=("/sdcard/none.cfg", "x"))) == SRC_RA.root
    assert resolve_root(dev, replace(SRC_RA, root_from=(CFG, "missing_key"))) == SRC_RA.root


def test_root_from_default_value_falls_back(dev, tree):
    tree(dev.root, {CFG.rsplit("/", 1)[0].lstrip("/"): {"retroarch.cfg": 'savefile_directory = "default"\n'}})
    assert resolve_root(dev, SRC_RA) == SRC_RA.root


def test_root_from_first_line_with_below(dev, tree):
    tree(dev.root, {"sdcard/app": {"memstick_dir.txt": "/storage/emulated/0/stick/\n"}})
    src = replace(SRC_PSP, root_from=("/sdcard/app/memstick_dir.txt", ""), below="PSP/SAVEDATA")
    assert resolve_root(dev, src) == "/storage/emulated/0/stick/PSP/SAVEDATA"


def test_two_sources_one_folder_claim_each_file_once(dev):
    scans = scan_all(dev, [SRC_SRM, SRC_STATE_SAME_ROOT])
    rels = [f.rel for s in scans for f in s.files]
    assert len(rels) == len(set(rels)) == 3
    assert [f.rel for f in scans[1].files] == ["x.state"]


def test_states_can_be_left_out(dev):
    scans = scan_all(dev, [SRC_SRM, SRC_STATE_SAME_ROOT], include_states=False)
    assert [s.source.kind for s in scans] == ["save"]


def test_name_items_match_games():
    stems = {"snes": {"EarthBound (USA)"}}
    items = group_items(SRC_RA_STATES, ["EarthBound (USA).state", "EarthBound (USA).state1",
                                        "EarthBound (USA).state.auto", "Unknown.state"], stems, ("snes",))
    eb = next(i for i in items if i.game)
    assert eb.game == ("snes", "EarthBound (USA)") and len(eb.files) == 3 and eb.shape == "game"
    assert eb.kind == "state" and eb.key == "ra-states/EarthBound (USA)" and eb.title == "EarthBound (USA)"
    other = next(i for i in items if not i.game)
    assert other.shape == "shared" and other.name == "Unknown"


def test_name_items_keep_their_folder_apart():
    items = group_items(SRC_SRM, ["Sonic.srm", "Genesis Plus GX/Sonic.srm"], {"megadrive": {"Sonic"}},
                        ("megadrive",))
    assert sorted(i.name for i in items) == ["Genesis Plus GX/Sonic", "Sonic"]
    assert all(i.game == ("megadrive", "Sonic") for i in items)


def test_name_match_is_limited_to_the_emulators_systems():
    items = group_items(SRC_SRM, ["Sonic.srm"], {"megadrive": {"Sonic"}}, ("snes",))
    assert items[0].game is None


def test_folder_items_group_by_directory():
    items = group_items(SRC_PSP, ["ULUS1/PARAM.SFO", "ULUS1/DATA.BIN", "ULUS2/a/b.bin", "loose.bin"], {}, ("psp",))
    assert [(i.name, len(i.files), i.shape) for i in items] == [
        ("ULUS1", 2, "folder"), ("ULUS2", 1, "folder"), ("loose.bin", 1, "shared")]


def test_folder_items_at_depth():
    src = replace(SRC_PSP, item_depth=3)
    items = group_items(src, ["00/savedata/PCSE1/sce_sys/param.sfo", "00/savedata/PCSE1/data.bin",
                              "00/savedata/PCSE2/data.bin"], {}, ())
    assert [i.name for i in items] == ["00/savedata/PCSE1", "00/savedata/PCSE2"]


def test_file_items_are_one_per_file():
    src = replace(SRC_SRM, items="file")
    items = group_items(src, ["mcd001.ps2", "mcd002.ps2"], {"ps2": {"mcd001"}}, ("ps2",))
    assert [(i.name, i.shape, i.game) for i in items] == [("mcd001.ps2", "shared", None), ("mcd002.ps2", "shared", None)]


def make_sfo(entries: dict) -> bytes:
    keys, data, index = b"", b"", b""
    for k, v in entries.items():
        raw = v.encode() + b"\0"
        index += struct.pack("<HHIII", len(keys), 0x0204, len(raw), len(raw), len(data))
        keys += k.encode() + b"\0"
        data += raw
    key_start = 20 + len(index)
    return b"\0PSF" + struct.pack("<IIII", 0x101, key_start, key_start + len(keys), len(entries)) + index + keys + data


def test_sfo_title():
    assert sfo_title(make_sfo({"CATEGORY": "MS", "TITLE": "Patapon 3"})) == "Patapon 3"
    assert sfo_title(make_sfo({"CATEGORY": "MS"})) is None
    assert sfo_title(b"junk") is None and sfo_title(b"") is None
    assert sfo_title(make_sfo({"TITLE": "Patapon 3"})[:30]) is None


def test_unreadable_files_are_counted_not_listed(dev):
    dev.unreadable_files = {RA + "/x.srm", RA + "/aes_keys.txt"}
    scan = scan_source(dev, SRC_SRM)
    assert [f.rel for f in scan.files] == ["core/y.srm"]
    assert scan.blocked == ("x.srm",)                      # a save that cannot be opened; the key file is no save
    assert scan.unreadable == ()
    with pytest.raises(DeviceError):
        dev.md5(RA + "/x.srm")


def test_unreadable_file_is_still_claimed(dev):
    dev.unreadable_files = {RA + "/x.srm"}
    scans = scan_all(dev, [SRC_SRM, SRC_STATE_SAME_ROOT])
    assert scans[0].blocked == ("x.srm",) and scans[1].blocked == ()


def test_a_source_folder_that_cannot_be_entered_is_counted(dev):
    dev.unreadable = {PSP}
    scan = scan_source(dev, SRC_PSP)
    assert scan.exists and scan.files == () and scan.unreadable == (PSP,)


# ---------------------------------------------------------------- emulator settings
AD = "sdcard/Android/data/"
SETTINGS_ON_DEVICE = {
    # settings files that are meant to be backed up
    AD + "com.retroarch.aarch64/files/retroarch.cfg": "ra",
    "sdcard/RetroArch/config/Snes9x/Snes9x.opt": "opt",
    "sdcard/RetroArch/config/esde/atari2600.cfg": "cfg",
    AD + "com.armsx2/files/PCSX2-Android.ini": "i",
    AD + "com.armsx2/files/armsx2-settings.json": "j",
    AD + "com.armsx2/files/inputprofiles/pad.json": "j",
    AD + "com.armsx2/files/gamesettings/SLUS-20001.ini": "i",
    AD + "xyz.aethersx2.android/files/gamesettings/SLUS-20002.ini": "i",
    AD + "xyz.aethersx2.android/files/PCSX2.ini": "i",
    AD + "org.ppsspp.ppsspp/files/PSP/SYSTEM/ppsspp.ini": "i",
    "sdcard/PSP/SYSTEM/controls.ini": "i",
    AD + "org.dolphinemu.dolphinemu/files/Config/GFX.ini": "i",
    AD + "org.dolphinemu.mmjr/files/Config/Dolphin.ini": "i",
    AD + "com.flycast.emulator/files/emu.cfg": "c",
    AD + "com.flycast.emulator/files/mappings/pad.cfg": "c",
    AD + "io.recompiled.redream/files/redream.cfg": "c",
    AD + "org.devmiyax.yabasanshioro2.pro/files/yabause/keymap_v2.json": "j",
    AD + "org.citra.emu/files/citra-emu/config/config.ini": "i",
    AD + "org.vita3k.emulator/files/config.yml": "y",
    AD + "aenu.aps3e/files/aps3e/config/default.yml": "y",
}
NOT_SETTINGS_ON_DEVICE = {
    "sdcard/RetroArch/overlays/gamepad.cfg": "overlay",
    "sdcard/RetroArch/config/readme.txt": "txt",
    AD + "com.armsx2/files/resources/GameIndex.yaml": "database",
    AD + "com.armsx2/files/recent_games.json": "recent",
    AD + "com.armsx2/files/bios/scph.bin": "bios",
    AD + "org.vita3k.emulator/files/cache/shaders.bin": "cache",
    AD + "org.vita3k.emulator/files/vita/os0/firmware.pup": "firmware",
    AD + "aenu.aps3e/files/aps3e/config/game_list.json": "list",
    AD + "aenu.aps3e/files/aps3e/config/dev_hdd0/keys.yml": "deeper than the settings",
    AD + "org.dolphinemu.dolphinemu/files/Config/Profiles/x.ini": "profile folder",
    AD + "org.dolphinemu.dolphinemu/files/Sys/GameSettings/GALE01.ini": "shipped database",
    AD + "com.flycast.emulator/files/data/keys.bin": "keys",
    AD + "org.ppsspp.ppsspp/files/PSP/SYSTEM/CACHE/x.ini": "cache folder",
}


def test_the_bundled_settings_sources_pick_only_settings_files(tmp_path):
    d = FakeDevice(tmp_path / "dev")
    for rel, body in {**SETTINGS_ON_DEVICE, **NOT_SETTINGS_ON_DEVICE}.items():
        p = d.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    found = set()
    for scan in scan_all(d, [s for s in load_sources() if s.kind == "settings"]):
        found |= {f"{scan.root}/{f.rel}".lstrip("/") for f in scan.files}
    assert found == set(SETTINGS_ON_DEVICE)


def test_every_settings_source_is_one_item_per_file():
    settings = [s for s in load_sources() if s.kind == "settings"]
    assert len(settings) == 14 and all(s.items == "file" and s.restore for s in settings)
    items = group_items(settings[0], ["a.cfg", "sub/b.cfg"], {}, ())
    assert sorted(i.name for i in items) == ["a.cfg", "sub/b.cfg"] and {i.kind for i in items} == {"settings"}


def test_scan_all_can_leave_settings_out(tmp_path):
    d = FakeDevice(tmp_path / "dev")
    (d.root / (AD + "com.flycast.emulator/files")).mkdir(parents=True)
    (d.root / (AD + "com.flycast.emulator/files/emu.cfg")).write_text("c")
    srcs = [s for s in load_sources() if s.package == "com.flycast.emulator"]
    assert [f.rel for s in scan_all(d, srcs) for f in s.files] == ["emu.cfg"]
    assert [f.rel for s in scan_all(d, srcs, include_settings=False) for f in s.files] == []
