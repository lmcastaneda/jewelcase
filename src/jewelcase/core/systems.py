"""Built-in table of game systems: folder names, ES-DE names, device-ready formats."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

ARCHIVE_EXTS = {".7z", ".rar"}
NOT_ROM_EXTS = {".txt", ".png", ".jpg", ".jpeg", ".xml", ".part", ".tsv", ".md", ".json",
                ".log", ".bak", ".cfg", ".ini", ".db", ".nfo", ".jewelcase-tmp",
                # save files some emulators keep beside the ROM
                ".srm", ".sav", ".state", ".eep", ".sra", ".fla", ".mpk", ".dsv", ".mcr", ".mcd", ".nv",
                ".rtc", ".ss0", ".ss1", ".st0", ".st1"}


@dataclass(frozen=True)
class System:
    folder: str
    name: str
    esde: str
    device_exts: tuple[str, ...]   # preferred first; () = any non-archive ROM extension
    color: str


_DISC = (".m3u", ".chd", ".cue", ".gdi")


def _cart(*native: str) -> tuple[str, ...]:
    return (".zip", *native)


_TABLE = [
    System("nes", "NES", "nes", _cart(".nes", ".fds"), "#c0392b"),
    System("snes", "Super Nintendo", "snes", _cart(".sfc", ".smc"), "#8e44ad"),
    System("gb", "Game Boy", "gb", _cart(".gb"), "#7f8c5a"),
    System("gbc", "Game Boy Color", "gbc", _cart(".gbc", ".gb"), "#16a085"),
    System("gba", "Game Boy Advance", "gba", _cart(".gba"), "#5b5fc7"),
    System("n64", "Nintendo 64", "n64", _cart(".z64", ".n64", ".v64"), "#27ae60"),
    System("nds", "Nintendo DS", "nds", _cart(".nds"), "#95a5a6"),
    System("n3ds", "Nintendo 3DS", "n3ds", (".3ds", ".cci", ".cxi", ".zip"), "#d35400"),
    System("gamecube", "GameCube", "gc", (".rvz", ".iso", ".gcz", ".ciso"), "#6c5ce7"),
    System("wii", "Wii", "wii", (".rvz", ".wbfs", ".iso"), "#00a8cc"),
    System("wiiu", "Wii U", "wiiu", (".wua", ".wux", ".rpx"), "#0984e3"),
    System("switch", "Switch", "switch", (".nsp", ".xci"), "#e84393"),
    System("mastersystem", "Master System", "mastersystem", _cart(".sms"), "#2d3436"),
    # Sega CD discs too: Genesis Plus GX plays them from this folder, and ES-DE lists them there
    System("megadrive", "Mega Drive / Genesis", "megadrive", _cart(".md", ".gen", ".bin", ".m3u", ".chd", ".cue"),
           "#34495e"),
    System("segacd", "Sega CD", "segacd", _DISC, "#3d6b8e"),
    System("saturn", "Saturn", "saturn", _DISC, "#576574"),
    System("dreamcast", "Dreamcast", "dreamcast", _DISC, "#e67e22"),
    System("psx", "PlayStation", "psx", (".m3u", ".chd", ".cue", ".pbp", ".iso"), "#7f8fa6"),
    System("ps2", "PlayStation 2", "ps2", (".chd", ".iso", ".cso"), "#2980b9"),
    System("ps3", "PlayStation 3", "ps3", (".iso", ".ps3"), "#0c2461"),
    System("psp", "PSP", "psp", (".iso", ".cso", ".chd"), "#1f6f8b"),
    System("psvita", "PS Vita", "psvita", (".vpk", ".zip"), "#1e3799"),
    System("neogeocd", "Neo Geo CD", "neogeocd", _DISC, "#b8860b"),
    System("pcengine", "PC Engine", "pcengine", _cart(".pce"), "#e17055"),
    System("arcade", "Arcade", "arcade", (".zip",), "#f1c40f"),
    System("mame", "MAME", "mame", (".zip",), "#d4ac0d"),
    System("cps1", "Capcom CPS-1", "cps1", (".zip",), "#f39c12"),
    System("cps2", "Capcom CPS-2", "cps2", (".zip",), "#e59866"),
    System("atari2600", "Atari 2600", "atari2600", _cart(".a26"), "#a04000"),
    System("atari7800", "Atari 7800", "atari7800", _cart(".a78"), "#935116"),
    System("atarijaguar", "Atari Jaguar", "atarijaguar", _cart(".j64", ".jag"), "#922b21"),
]
_BY_FOLDER = {s.folder: s for s in _TABLE}
_BY_ESDE = {s.esde: s for s in _TABLE}
_GENERIC_COLOR = "#7d8590"
_EXTRA: set[str] = set()


def all_systems() -> list[System]:
    return list(_BY_FOLDER.values())


def known_folders() -> set[str]:
    return set(_BY_FOLDER)


def get(folder: str) -> System:
    return _BY_FOLDER.get(folder) or System(folder, folder, folder, (), _GENERIC_COLOR)


def by_esde(esde_name: str) -> System:
    return _BY_ESDE.get(esde_name) or get(esde_name)


def ext_of(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def is_rom_file(system: System, filename: str) -> bool:
    if not filename or filename.startswith("."):
        return False
    ext = ext_of(filename)
    if ext in system.device_exts:
        return True                     # e.g. ".md" is a Mega Drive ROM, not a markdown note
    return bool(ext) and ext not in NOT_ROM_EXTS


def device_ready(system: System, ext: str) -> bool:
    ext = ext.lower()
    if system.device_exts:
        return ext in system.device_exts
    return ext not in ARCHIVE_EXTS


def register(folder: str, name: str | None = None, esde: str | None = None,
             device_exts=(), color: str | None = None) -> System:
    """Add or replace a system (used for [systems.<folder>] entries in the config)."""
    base = _BY_FOLDER.get(folder)
    s = System(folder, name or (base.name if base else folder),
               esde or (base.esde if base else folder),
               tuple(e.lower() for e in device_exts) or (base.device_exts if base else ()),
               color or (base.color if base else _GENERIC_COLOR))
    if base is None:
        _EXTRA.add(folder)
    _BY_FOLDER[folder] = s
    _BY_ESDE[s.esde] = s
    return s


def unregister(folder: str) -> None:
    if folder in _EXTRA:
        s = _BY_FOLDER.pop(folder)
        _BY_ESDE.pop(s.esde, None)
        _EXTRA.discard(folder)
