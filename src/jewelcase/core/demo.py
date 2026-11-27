"""A made-up library and handheld, for `--demo`, screenshots and UI tests. No real games involved."""
from __future__ import annotations

import html
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .db import Store
from .device import PackageInfo
from .fakedevice import FakeDevice

CARD = "/storage/1A2B-3C4D"
MB = 1024 * 1024

# (system, esde, ext, [(title, size in MB, where, hours played, days since last play)])
# where: B = both, L = library only, D = device only, A = only as an archive (a sync converts it first)
_GAMES = [
    ("ps2", "ps2", ".chd", [
        ("Clockwork Citadel (USA)", 58, "B", 14.5, 3), ("Baldur's Echo - Dark Path (USA)", 44, "B", 0.0, 0),
        ("Harbor Run 3 (USA, Canada)", 37, "B", 2.1, 40), ("Iron Meridian (USA) (En,Ja)", 52, "B", 0.004, 20),
        ("Skyline Drift (Europe)", 30, "B", 0.0, 0), ("Tidewater Tactics (USA)", 41, "L", 6.0, 200),
        ("Ember & Ash (USA)", 47, "L", 0.0, 0), ("Northwind Saga (USA)", 61, "A", 0.0, 0),
        ("Glass Garden (USA)", 28, "D", 0.5, 12)]),
    ("psx", "psx", ".chd", [
        ("Lantern Quest VII (USA)", 21, "B", 31.0, 1), ("Pocket Racers (USA)", 9, "B", 0.0, 0),
        ("Moonlit Keep (USA)", 12, "B", 4.2, 130), ("Signal Lost (USA)", 14, "L", 0.0, 0),
        ("Paper Kingdoms (USA)", 11, "L", 9.5, 400)]),
    ("gamecube", "gc", ".rvz", [
        ("Prism Hunter (USA) (Rev 2)", 26, "B", 0.0, 0), ("Kart Carnival - Double Lap (USA)", 24, "B", 7.7, 6),
        ("Wind & Wave (USA)", 22, "B", 1.1, 95), ("Garden Sprouts 2 (USA)", 18, "L", 0.0, 0),
        ("Thousand Page Door (USA)", 25, "B", 0.0, 0)]),
    ("psp", "psp", ".iso", [
        ("Chains of Dusk (USA)", 19, "B", 3.3, 15), ("Liberty Streets (USA) (v3.00)", 23, "B", 0.0, 0),
        ("Pocket Tactics - War of Lions (USA)", 8, "B", 22.0, 2), ("Crisis Line (USA)", 20, "L", 0.0, 0)]),
    ("gba", "gba", ".zip", [
        ("Mother Lode 3 (Tr)", 3, "B", 0.0, 0), ("Minish Hat (USA)", 2, "B", 5.5, 60),
        ("Advance Skirmish (USA)", 1, "B", 12.0, 9), ("Pokémon Verdigris (USA)", 2, "B", 0.01, 30),
        ("Golden Dawn (USA, Europe)", 2, "L", 0.0, 0), ("Metro Fusion (USA)", 1, "L", 3.0, 300)]),
    ("snes", "snes", ".zip", [
        ("Chrono Compass (USA)", 1, "B", 18.0, 4), ("Super Plumber Land (USA)", 1, "B", 0.0, 0),
        ("Secret of Tides (USA)", 1, "B", 0.0, 0), ("Star Vixen (USA) (Rev 2)", 1, "L", 0.0, 0)]),
    ("dreamcast", "dreamcast", ".chd", [
        ("Soul Blade Calibre (USA)", 16, "B", 0.0, 0), ("Crazy Cab (USA)", 13, "B", 2.4, 22),
        ("Jet Grind Stereo (USA)", 15, "L", 0.0, 0), ("Shen Harbor (USA)", 17, "L", 0.0, 0)]),
    ("n64", "n64", ".zip", [
        ("Ocarina of Hours (USA)", 2, "B", 9.9, 50), ("Golden Agent 64 (USA)", 1, "B", 0.0, 0),
        ("Smash Siblings (USA)", 1, "L", 0.0, 0)]),
]

_EMULATORS = {
    "com.retroarch.aarch64": ("1.19.1", True, False, True), "org.ppsspp.ppsspp": ("v1.17.1", True, True, True),
    "org.dolphinemu.dolphinemu": ("2409", True, False, True), "com.flycast.emulator": ("2.3", True, False, False),
    "com.armsx2": ("1.0.4", False, False, True), "org.es_de.frontend": ("3.2.0", False, False, True),
    "com.github.stenzek.duckstation": ("0.1-6759", True, False, True),
    "org.mupen64plusae.v3.fzurita.pro": ("3.0.294", True, False, True),
}

ESDE_APK = "/data/app/org.es_de.frontend/base.apk"
# (ES-DE's system name, full name, [(command label, ES-DE's emulator name)]): the first command is the default
_ESDE_SYSTEMS = [
    ("ps2", "Sony PlayStation 2", [("AetherSX2 (Standalone)", "NETHERSX2"), ("ARMSX2 (Standalone)", "ARMSX2")]),
    ("ps3", "Sony PlayStation 3", [("ARMSX3 Directory or ISO (Standalone)", "ARMSX3"),
                                   ("aPS3e Game Serial (Standalone)", "APS3E"), ("aPS3e ISO (Standalone)", "APS3E")]),
    ("psx", "Sony PlayStation", [("SwanStation", "RETROARCH"), ("PCSX ReARMed", "RETROARCH"),
                                 ("DuckStation (Standalone)", "DUCKSTATION")]),
    ("gc", "Nintendo GameCube", [("Dolphin (Standalone)", "DOLPHIN"), ("Dolphin", "RETROARCH")]),
    ("psp", "Sony PlayStation Portable", [("PPSSPP (Standalone)", "PPSSPP"), ("PPSSPP", "RETROARCH")]),
    ("wii", "Nintendo Wii", [("Dolphin MMJR2 (Standalone)", "DOLPHIN-MMJR2"), ("Dolphin", "RETROARCH")]),
    ("gba", "Nintendo Game Boy Advance", [("mGBA", "RETROARCH"), ("VBA-M", "RETROARCH")]),
    ("snes", "Nintendo SNES", [("Snes9x - Current", "RETROARCH"), ("bsnes", "RETROARCH")]),
    ("dreamcast", "Sega Dreamcast", [("Flycast", "RETROARCH"), ("Flycast (Standalone)", "FLYCAST")]),
    ("n64", "Nintendo 64", [("Mupen64Plus-Next", "RETROARCH"), ("M64Plus FZ (Standalone)", "M64PLUS-FZ")]),
]
_SAF = {"PPSSPP (Standalone)", "ARMSX2 (Standalone)"}      # commands that hand the game over as a document link, as ES-DE's do
_ESDE_RULES = {
    "RETROARCH": "com.retroarch.aarch64", "NETHERSX2": "xyz.aethersx2.android", "ARMSX2": "com.armsx2",
    "APS3E": "aenu.aps3e", "ARMSX3": "com.armsx3", "DUCKSTATION": "com.github.stenzek.duckstation",
    "DOLPHIN": "org.dolphinemu.dolphinemu", "PPSSPP": "org.ppsspp.ppsspp", "FLYCAST": "com.flycast.emulator",
    "M64PLUS-FZ": "org.mupen64plusae.v3.fzurita.pro", "DOLPHIN-MMJR2": "org.dolphinemu.mmjr",
}
PSP_SAVES = "/sdcard/Android/data/org.ppsspp.ppsspp/files/PSP/SAVEDATA"
# (device path, megabytes or 0 for a few bytes)
_SAVES = [
    ("/sdcard/RetroArch/saves/Chrono Compass (USA).srm", 0), ("/sdcard/RetroArch/saves/Minish Hat (USA).srm", 0),
    ("/sdcard/RetroArch/saves/Advance Skirmish (USA).srm", 0),
    ("/sdcard/RetroArch/states/Chrono Compass (USA).state", 1),
    ("/sdcard/Android/data/com.armsx2/files/memcards/mcd001.ps2", 8),
    ("/sdcard/Android/data/com.armsx2/files/memcards/mcd002.ps2", 8),
    ("/sdcard/Android/data/com.armsx2/files/sstates/SLUS-20312 (D224D348).00.p2s", 14),
    (PSP_SAVES + "/ULUS10509001/DATA.BIN", 0), (PSP_SAVES + "/ULUS100720000/DATA.BIN", 0),
    # things that sit beside saves and are not saves
    ("/sdcard/RetroArch/saves/mame2003-plus/cfg/default.cfg", 0),
    ("/sdcard/Android/data/com.armsx2/files/bios/scph39001.bin", 4),
]


NO_COVER = {"Skyline Drift (Europe)", "Jet Grind Stereo (USA)"}      # games ES-DE has no cover for
NO_SYSTEM_IMAGE = {"n64"}


def _png(width: int, rows: list[bytes], alpha: bool = False) -> bytes:
    """A PNG from ready-made rows of RGB (or RGBA) bytes. Enough for made-up pictures; no image library."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    head = struct.pack(">IIBBBBB", width, len(rows), 8, 6 if alpha else 2, 0, 0, 0)
    body = zlib.compress(b"".join(b"\x00" + row for row in rows), 1)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", body) + chunk(b"IEND", b"")


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _cover(title: str, colour: str) -> bytes:
    """A made-up box cover: the system's colour running darker downwards, with a plate where a title would be."""
    import zlib
    seed = zlib.crc32(title.encode())
    tint = (seed % 41 - 20, (seed >> 8) % 41 - 20, (seed >> 16) % 41 - 20)
    plate_at = 44 + seed % 90
    rows = []
    for y in range(336):
        shade = int(46 - 110 * y / 336)
        base = bytes(max(0, min(255, c + d + shade)) for c, d in zip(_rgb(colour), tint))
        if y >= 304:
            rows.append(bytes(c // 3 for c in base) * 240)
        elif plate_at <= y < plate_at + 50:
            rows.append(base * 22 + bytes(min(255, c + 80) for c in base) * 196 + base * 22)
        else:
            rows.append(base * 240)
    return _png(240, rows)


def _system_image(colour: str) -> bytes:
    """A made-up console on a clear background, in the system's colour."""
    r, g, b = _rgb(colour)
    clear, body, light = bytes(4), bytes((r, g, b, 255)), bytes((min(255, r + 70), min(255, g + 70), min(255, b + 70), 255))
    rows = []
    for y in range(200):
        if 70 <= y < 80:
            rows.append(clear * 24 + light * 152 + clear * 24)
        elif 80 <= y < 132:
            rows.append(clear * 24 + body * 152 + clear * 24)
        elif 150 <= y < 170:
            rows.append(clear * 40 + body * 44 + clear * 32 + body * 44 + clear * 40)
        else:
            rows.append(clear * 200)
    return _png(200, rows, alpha=True)


def _esde_app(path: Path) -> None:
    """A stand-in for ES-DE's APK: the two definition files Jewelcase reads from it, and its system images."""
    import zipfile
    blocks = []
    for name, full, commands in _ESDE_SYSTEMS:
        lines = "".join(f'        <command label="{label}">%EMULATOR_{emu}% '
                        f'{"%DATA%=%ROMSAF%" if label in _SAF else "%ROM%"}</command>\n' for label, emu in commands)
        blocks.append(f"    <system>\n        <name>{name}</name>\n        <fullname>{full}</fullname>\n"
                      f"        <path>%ROMPATH%/{name}</path>\n{lines}    </system>\n")
    rules = "".join(f'    <emulator name="{emu}"><rule type="androidpackage"><entry>{package}/.Main</entry></rule>'
                    "</emulator>\n" for emu, package in _ESDE_RULES.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("assets/systems/android/es_systems.xml",
                   '<?xml version="1.0"?>\n<systemList>\n' + "".join(blocks) + "</systemList>\n")
        z.writestr("assets/systems/android/es_find_rules.xml",
                   '<?xml version="1.0"?>\n<ruleList>\n' + rules + "</ruleList>\n")
        from . import systems
        for name, _full, _commands in _ESDE_SYSTEMS:
            if name not in NO_SYSTEM_IMAGE:
                z.writestr(f"assets/linear-es-de/system/systemart/{name}.png",
                           _system_image(systems.by_esde(name).color))


def _sparse(path: Path, size: int, salt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(salt.encode())          # different first bytes, so different hashes
        f.truncate(max(size, len(salt.encode())))


ARCHIVED_MB = 8

# Made-up BIOS files for the BIOS page. Their bytes and checksums are invented here: they are not real BIOS
# data and the checksums are not real ones. They are added to the list of known files only in demo mode.
DEMO_PSX_BIOS = b"JEWELCASE DEMO - not a real PlayStation BIOS\n" * 64
DEMO_PS2_BIOS = "demo-ps2-bios-not-in-the-list"            # a 4 MB file whose checksum no list knows
DEMO_PSX_NAME = "demo-psx-bios.bin"


def bios_entries() -> dict:
    """The one made-up file the demo list knows (in the shape of data/bios.toml), kept apart from the shipped data."""
    import hashlib
    return {"system": [{"folder": "psx", "file": [{
        "name": DEMO_PSX_NAME, "size": len(DEMO_PSX_BIOS), "md5": hashlib.md5(DEMO_PSX_BIOS).hexdigest()}]}]}


def _archive(path: Path, inner: str, rng: random.Random) -> None:
    """A real archive holding one disc image, so the demo has something a sync can convert."""
    import zipfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr(inner, rng.randbytes(ARCHIVED_MB * MB))


def open_demo(tmp: Path) -> tuple[Config, Store, FakeDevice]:
    """The demo world in `tmp`, created on first use and reused afterwards."""
    tmp = Path(tmp)
    if not (tmp / "library").exists():
        return make_demo(tmp)
    return _objects(tmp)


def _objects(tmp: Path) -> tuple[Config, Store, FakeDevice]:
    dev = FakeDevice(tmp / "device", serial="DEMO0001")
    dev.model = "RG Demo"
    dev.storage_total = 2048 * MB
    dev.battery, dev.power = 81, "usb"
    dev.fake_packages = {p: PackageInfo(p, *v) for p, v in _EMULATORS.items()}
    dev.fake_apks = {"org.es_de.frontend": ESDE_APK}
    # folders given to apps through Android's folder picker: ARMSX2 has its games folder, PPSSPP has none yet
    dev.fake_grants = {"com.armsx2": [CARD + "/ps2"], "org.es_de.frontend": [CARD]}
    # Android lets adb list PPSSPP's save folder but not enter the per-game folders inside it
    dev.unreadable = {PSP_SAVES + "/ULUS10509001", PSP_SAVES + "/ULUS100720000"}
    return Config(library=tmp / "library", inbox=tmp / "inbox"), Store(tmp / "jewelcase.db"), dev


def make_demo(tmp: Path, now: datetime | None = None) -> tuple[Config, Store, FakeDevice]:
    tmp = Path(tmp)
    now = now or datetime.now()
    lib = tmp / "library"
    card = tmp / "device" / CARD.lstrip("/")
    dev_root = tmp / "device"
    rng = random.Random(7)
    from . import systems as colours
    for system, esde, ext, games in _GAMES:
        entries = []
        for title, mb, where, hours, days in games:
            name = title + (".zip" if where == "A" else ext)
            size = mb * MB + rng.randrange(0, MB)
            if where == "A":
                _archive(lib / system / name, title + ".iso", rng)
            elif where in "BL":
                _sparse(lib / system / name, size, title)
            if where != "A" and title not in NO_COVER:      # what ES-DE's scraper would have left
                cover = dev_root / "sdcard/ES-DE/downloaded_media" / esde / "covers" / (title + ".png")
                cover.parent.mkdir(parents=True, exist_ok=True)
                cover.write_bytes(_cover(title, colours.get(system).color))
            if where in "BD":
                _sparse(card / system / name, size, title)
                secs = int(hours * 3600)
                last = (now - timedelta(days=days, hours=3)).strftime("%Y%m%dT%H%M%S") if secs else ""
                entry = f"\t<game>\n\t\t<path>./{html.escape(name, quote=False)}</path>\n" \
                        f"\t\t<name>{html.escape(title.split(' (')[0], quote=False)}</name>\n"
                if secs:
                    entry += f"\t\t<playcount>{max(1, int(hours * 2))}</playcount>\n" \
                             f"\t\t<lastplayed>{last}</lastplayed>\n\t\t<playtime>{secs}</playtime>\n"
                entries.append(entry + "\t</game>\n")
        gl = dev_root / "sdcard/ES-DE/gamelists" / esde / "gamelist.xml"
        gl.parent.mkdir(parents=True, exist_ok=True)
        gl.write_text('<?xml version="1.0"?>\n<gameList>\n' + "".join(entries) + "</gameList>\n", encoding="utf-8")
    # one multi-disc game with a playlist, on both sides
    discs = [f"Lantern Quest IX (USA) (Disc {i}).chd" for i in (1, 2, 3)]
    for root in (lib / "psx", card / "psx"):
        for d in discs:
            _sparse(root / d, 15 * MB, d)
        (root / "Lantern Quest IX (USA).m3u").write_text("\n".join(discs) + "\n", encoding="utf-8")
    (dev_root / "sdcard/ES-DE/downloaded_media/psx/covers/Lantern Quest IX (USA).png").write_bytes(
        _cover("Lantern Quest IX (USA)", colours.get("psx").color))
    for path, mb in _SAVES:
        _sparse(dev_root / path.lstrip("/"), mb * MB, path)
    _esde_app(dev_root / ESDE_APK.lstrip("/"))
    # BIOS page: a verified file under another name (DuckStation names files by checksum), one nobody
    # recognises, and nothing for Dreamcast
    import hashlib
    (dev_root / "sdcard/duckstation/bios").mkdir(parents=True, exist_ok=True)
    (dev_root / "sdcard/duckstation/bios" / hashlib.md5(DEMO_PSX_BIOS).hexdigest()).write_bytes(DEMO_PSX_BIOS)
    _sparse(card / "bios-extras/armsx2-bios/SCPH-30004R.rom0", 4 * MB, DEMO_PS2_BIOS)
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    _sparse(inbox / "Violet Horizon (USA).gba", 2 * MB, "vh")
    _sparse(inbox / "Unknown Disc (USA).chd", 30 * MB, "ud")
    with open(inbox / "Rally Kings (USA).rvz", "wb") as f:      # a header that says "GameCube disc"
        f.write(b"RVZ\x01" + bytes(0x44) + (1).to_bytes(4, "big"))
        f.truncate(20 * MB)
    an_hour_ago = now.timestamp() - 3600
    for f in inbox.iterdir():
        os.utime(f, (an_hour_ago, an_hour_ago))
    return _objects(tmp)
