from jewelcase.core import systems


def test_known_system():
    gc = systems.get("gamecube")
    assert gc.esde == "gc"
    assert gc.device_exts[0] == ".rvz"
    assert systems.get("ps2").device_exts[0] == ".chd"
    assert systems.get("psp").device_exts == (".iso", ".cso", ".chd")
    assert systems.get("psx").device_exts[:3] == (".m3u", ".chd", ".cue")
    assert systems.get("nes").device_exts[0] == ".zip"
    assert systems.get("ps2").name == "PlayStation 2"


def test_by_esde():
    assert systems.by_esde("gc").folder == "gamecube"
    assert systems.by_esde("psx").folder == "psx"
    assert systems.by_esde("somethingnew").folder == "somethingnew"


def test_unknown_system_is_generic():
    s = systems.get("vectrex")
    assert s.name == "vectrex"
    assert s.esde == "vectrex"
    assert s.device_exts == ()


def test_is_rom_file():
    ps2 = systems.get("ps2")
    assert systems.is_rom_file(ps2, "Game (USA).chd")
    assert systems.is_rom_file(ps2, "Game (USA).7z")
    for bad in ("gamelist.xml", "x.part", ".hidden", "notes.txt", "cover.png", "m.tsv"):
        assert not systems.is_rom_file(ps2, bad), bad


def test_device_ready():
    ps2, nes, odd = systems.get("ps2"), systems.get("nes"), systems.get("vectrex")
    assert not systems.device_ready(ps2, ".7z")
    assert systems.device_ready(ps2, ".chd")
    assert systems.device_ready(ps2, ".CHD")
    assert systems.device_ready(nes, ".zip")
    assert not systems.device_ready(nes, ".7z")
    # generic systems take anything that is not a 7z/rar archive
    assert systems.device_ready(odd, ".vec")
    assert not systems.device_ready(odd, ".7z")


def test_every_system_has_a_colour_and_name():
    for s in systems.all_systems():
        assert s.color.startswith("#") and len(s.color) == 7
        assert s.name and s.folder


def test_ps3_system():
    ps3 = systems.get("ps3")
    assert ps3.name == "PlayStation 3" and ps3.esde == "ps3"
    assert ps3.device_exts == (".iso", ".ps3")          # what ES-DE on Android launches with aPS3e
    assert systems.by_esde("ps3").folder == "ps3"
    assert systems.is_rom_file(ps3, "Some Game (USA).iso")
    assert systems.is_rom_file(ps3, "BLUS30000.ps3")
    assert systems.device_ready(ps3, ".ISO") and not systems.device_ready(ps3, ".pkg")
    assert ps3.color.startswith("#")


def test_mega_drive_also_takes_sega_cd_discs():
    """Genesis Plus GX plays Sega CD games from the Mega Drive folder, and ES-DE lists them there."""
    megadrive = systems.get("megadrive")
    assert systems.device_ready(megadrive, ".chd") and systems.device_ready(megadrive, ".cue")
    assert systems.device_ready(megadrive, ".m3u") and not systems.device_ready(megadrive, ".7z")
    assert megadrive.device_exts[0] == ".zip"               # cartridges stay the first choice
