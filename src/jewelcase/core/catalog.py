"""One list of games from three sources: the library index, the device snapshot, and play stats."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Iterable

from . import convert, gamelist, smartlists, systems
from .config import Config
from .db import Stats, Store
from .device import Device, DeviceError, RemoteFile
from .games import Unit, group_units
from .library import Library, pick_device_form
from .systems import System

_CARD = re.compile(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}")
_PLAYLIST = (".m3u", ".cue")


@dataclass(frozen=True)
class Profile:
    rom_root: str
    gamelists_dir: str | None
    card_root: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Profile":
        return Profile(d["rom_root"], d.get("gamelists_dir"), d.get("card_root") or d["rom_root"])


class FolderError(Exception):
    """A games folder that cannot be used; the message says why, in a sentence."""


@dataclass(frozen=True)
class Candidate:
    path: str
    card_root: str
    systems: int            # system folders inside it
    games: int = 0          # games in them (counted only when asked for)


def _roots(dev: Device) -> list[tuple[str, str]]:
    """Every (games folder, card it is on) detection looks at."""
    if not dev.has_apps:                    # a card or folder: '/' is the card
        # On FAT/exFAT the three spellings are one folder: only the spelling the card stores is looked at
        real = set(dev.list_dirs("/"))
        return [(c, "/") for c in ("/", "/ROMs", "/roms", "/Roms") if c == "/" or c[1:] in real]
    cards = ["/storage/" + d for d in dev.list_dirs("/storage") if _CARD.fullmatch(d)]
    roots = [(c + sub, c) for c in cards for sub in ("", "/ROMs", "/roms", "/Roms")]
    return roots + [("/sdcard/ROMs", "/sdcard"), ("/sdcard/roms", "/sdcard"), ("/sdcard/Roms", "/sdcard")]


def candidates(dev: Device, library_systems: Iterable[str] = ()) -> list[Candidate]:
    """The folders detection considers that hold system folders, most systems first."""
    known = systems.known_folders() | set(library_systems)
    found = [Candidate(root, card, n) for root, card in _roots(dev)
             if (n := len(known & set(dev.list_dirs(root))))]
    return sorted(found, key=lambda c: -c.systems)      # stable: on a tie the earlier candidate stays first


def count_games(dev: Device, root: str, library_systems: Iterable[str] = ()) -> int:
    """How many game files (a disc and its tracks count each) are in a games folder's system folders."""
    wanted = systems.known_folders() | set(library_systems)
    base = root.rstrip("/")
    n = 0
    for f in dev.list_files(root, maxdepth=2):
        rel = f.path[len(base) + 1:].split("/")
        if len(rel) == 2 and rel[0] in wanted and systems.is_rom_file(systems.get(rel[0]), rel[1]):
            n += 1
    return n


def gamelists_for(dev: Device) -> str | None:
    paths = ("/sdcard/ES-DE/gamelists",) if dev.has_apps else ("/ES-DE/gamelists", "/gamelists")
    return next((p for p in paths if dev.exists(p)), None)


def detect_profile(dev: Device) -> Profile | None:
    found = candidates(dev)
    if found:
        return Profile(found[0].path, gamelists_for(dev), found[0].card_root)
    if not dev.has_apps:                    # a card that has no system folders yet: an existing ROMs folder will do
        for root, card in _roots(dev)[1:]:
            if dev.exists(root):
                return Profile(root, gamelists_for(dev), card)
    return None


def card_root_of(path: str, previous: str | None = None) -> str:
    """Which card a games folder is on: the one it is inside, else the one the device had."""
    if previous and previous != "/" and (path == previous or path.startswith(previous.rstrip("/") + "/")):
        return previous
    m = re.match(r"/storage/[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}", path)
    if m:
        return m.group(0)
    if path.startswith("/sdcard"):
        return "/sdcard"
    return previous or "/"


def check_games_folder(dev: Device, path: str, library_systems: Iterable[str] = ()) -> str:
    """The cleaned-up path when it holds system folders; otherwise FolderError saying why."""
    path = "/" + path.strip().strip("/") if path.strip().strip("/") else "/"
    try:
        if path != "/" and not dev.exists(path):
            raise FolderError(f"{path} does not exist on the device.")
        found = set(dev.list_dirs(path))
    except DeviceError:                     # e.g. "/../x" on a card: the path leaves the card
        raise FolderError(f"{path} is outside the card, so it cannot be the games folder.") from None
    if not (systems.known_folders() | set(library_systems)) & found:
        raise FolderError(f"{path} has no system folders in it (folders like psx, gba or ps2), "
                          "so it cannot be the games folder.")
    return path


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def device_units(store: Store, serial: str) -> dict[str, list[Unit]]:
    """Games on the device according to the last snapshot."""
    texts = store.snapshot_text(serial)
    out = {}
    for system, files in store.snapshot(serial).items():
        by_name = {_name(f.path): f.path for f in files}
        out[system] = group_units(by_name, lambda n, b=by_name: texts[b[n]])
    return out


def refresh_device(dev: Device, profile: Profile, store: Store, library_systems: Iterable[str] = ()) -> None:
    """Re-read the device: files, playlists, battery, storage and ES-DE play stats."""
    serial = dev.serial
    esde_dirs = dev.list_dirs(profile.gamelists_dir) if profile.gamelists_dir else []
    wanted = systems.known_folders() | set(library_systems) | {systems.by_esde(e).folder for e in esde_dirs}
    root = profile.rom_root.rstrip("/")

    files: dict[str, list[RemoteFile]] = {}
    for f in dev.list_files(root, maxdepth=2):
        rel = f.path[len(root) + 1:].split("/")
        if len(rel) != 2 or rel[0] not in wanted:
            continue
        if systems.is_rom_file(systems.get(rel[0]), rel[1]):
            files.setdefault(rel[0], []).append(f)

    old_files = {f.path: f for items in store.snapshot(serial).values() for f in items}
    old_texts = store.snapshot_text(serial)
    texts = {}
    for items in files.values():
        for f in items:
            if f.path.lower().endswith(_PLAYLIST):
                if f.path in old_texts and old_files.get(f.path) == f:
                    texts[f.path] = old_texts[f.path]
                else:
                    try:
                        texts[f.path] = dev.read_bytes(f.path).decode("utf-8", "replace")
                    except DeviceError:
                        pass
    store.save_snapshot(serial, files, texts)

    store.set_meta(serial, "info", asdict(dev.info()))
    store.set_meta(serial, "storage_card", asdict(dev.storage(profile.card_root)))
    try:
        store.set_meta(serial, "storage_internal", asdict(dev.storage("/sdcard")))
    except DeviceError:
        pass
    store.set_meta(serial, "seen", datetime.now().isoformat(timespec="seconds"))

    units = device_units(store, serial)
    for esde in esde_dirs:
        try:
            xml = dev.read_bytes(f"{profile.gamelists_dir}/{esde}/gamelist.xml").decode("utf-8", "replace")
        except DeviceError:
            continue
        folder = systems.by_esde(esde).folder
        owner: dict[str, Unit] = {}
        for u in units.get(folder, []):
            for n in u.files:
                owner.setdefault(n, u)
        totals: dict[str, list] = {}
        for entry in gamelist.parse(xml).values():
            u = owner.get(entry.file)
            stem = u.stem if u else PurePosixPath(entry.file).stem
            t = totals.setdefault(stem, [0, 0, None, ""])
            t[0] += entry.plays
            t[1] += entry.seconds
            if entry.last_played and (t[2] is None or entry.last_played > t[2]):
                t[2] = entry.last_played
            if not t[3] or (u and entry.file == u.head):
                t[3] = entry.name
        for stem, (plays, seconds, last, name) in totals.items():
            store.upsert_stats(serial, folder, stem, Stats(plays, seconds, last, name))


def seed_from_manifest(library: Library, store: Store, serial: str) -> int:
    """Use an existing card-pull-manifest.tsv as already-verified hashes for both sides."""
    library.import_manifest()
    seeded = set(store.meta(serial, "manifest_seeded", []))
    on_device = {(system, _name(f.path)): f for system, files in store.snapshot(serial).items() for f in files}
    n = 0
    for system, name, size, md5 in library.manifest_device_hashes():
        key = f"{system}/{name}"
        f = on_device.get((system, name))
        # once per file: a file that changes afterwards is hashed again, never re-stamped from the manifest
        if key in seeded or f is None or f.size != size:
            continue
        seeded.add(key)
        store.set_dev_hash(serial, system, name, size, f.mtime, md5)
        n += 1
    if n:
        store.set_meta(serial, "manifest_seeded", sorted(seeded))
    return n


def files_missing_from_library(row: "GameRow", library: Library) -> list[str]:
    """Device files of a game for which the library has neither the same file nor an archive of it."""
    if not row.dev_unit:
        return []
    names = library.names(row.system)
    archives = {PurePosixPath(n).stem for n in names if PurePosixPath(n).suffix.lower() in (".7z", ".zip")}
    present = set(names)
    return [n for n in row.dev_unit.files
            if n not in present and PurePosixPath(n).stem not in archives and row.stem not in archives]


@dataclass(frozen=True)
class GameRow:
    system: str
    stem: str
    title: str
    state: str                  # on_device | library_only | device_only | needs_conversion
    lib_unit: Unit | None       # the form that goes (or went) to the device
    dev_unit: Unit | None
    size: int
    plays: int
    seconds: int
    last_played: datetime | None
    on_device: bool
    want: bool
    in_library: bool = False
    sizes: dict = field(default_factory=dict, compare=False)   # file name -> bytes (device side if on device)
    archive: str = ""           # needs_conversion: the archive it could be converted from, if there is one
    convert: object = None      # ... and, once the game is ticked, what converting it makes (convert.Conversion)

    @property
    def key(self) -> tuple[str, str]:
        return (self.system, self.stem)


_TAGS = re.compile(r"(\s*[\(\[][^\)\]]*[\)\]])+\s*$")


def clean_title(stem: str) -> str:
    """'Wind Waker (USA) (Rev 2)' -> 'Wind Waker', for display when ES-DE has no name for the game."""
    return _TAGS.sub("", stem).strip() or stem


def _merge(units: list[Unit]) -> Unit:
    if len(units) == 1:
        return units[0]
    files = tuple(dict.fromkeys(n for u in units for n in u.files))
    return Unit(units[0].stem, units[0].head, files)


def build_rows(library: Library, store: Store, serial: str | None) -> list[GameRow]:
    snap = store.snapshot(serial) if serial else {}
    dev_units = device_units(store, serial) if serial else {}
    stats = store.stats(serial) if serial else {}
    wanted = store.wanted(serial) if serial else {}
    rows = []
    for system in sorted(set(library.systems()) | set(dev_units)):
        sysinfo = systems.get(system)
        forms = library.forms(system)
        packed = convert.archives(sysinfo, library.names(system))
        sizes = {_name(f.path): f.size for f in snap.get(system, [])}
        on_dev: dict[str, list[Unit]] = {}
        for u in dev_units.get(system, []):
            on_dev.setdefault(u.stem, []).append(u)
        for stem in sorted(set(forms) | set(on_dev), key=str.lower):
            lib_forms = forms.get(stem, [])
            dev_unit = _merge(on_dev[stem]) if stem in on_dev else None
            lib_unit = pick_device_form(sysinfo, lib_forms, {dev_unit.ext} if dev_unit else set())
            archive, conv = "", None
            if dev_unit:
                state = "on_device" if lib_forms else "device_only"
                per_file = {n: sizes.get(n, 0) for n in dev_unit.files}
            elif lib_unit:
                state = "library_only"
                per_file = {n: library.size(system, n) or 0 for n in lib_unit.files}
            else:
                state = "needs_conversion"
                archive = packed.get(stem, "")
                if archive and wanted.get((system, stem)) and library.online:
                    conv = convert.look(library, system, stem)      # opens the archive: only for a ticked game
                if conv and not conv.problem:
                    per_file = dict(conv.sizes)
                else:
                    biggest = max(lib_forms, key=lambda u: library.unit_size(system, u))
                    per_file = {n: library.size(system, n) or 0 for n in biggest.files}
            s = stats.get((system, stem), Stats())
            rows.append(GameRow(system, stem, s.name or clean_title(stem), state, lib_unit, dev_unit,
                                sum(per_file.values()), s.plays, s.seconds, s.last_played,
                                dev_unit is not None, wanted.get((system, stem), dev_unit is not None),
                                bool(lib_forms), per_file, archive, conv))
    return rows


@dataclass(frozen=True)
class SmartList:
    id: str
    title: str


SMART_LISTS = {s.id: s for s in (
    SmartList("never_played", "Never Played"),
    SmartList("not_played", "Not Played Lately"),
    SmartList("most_played", "Most Played"),
    SmartList("largest", "Largest on Device"),
    SmartList("device_only", "Device Only"),
)}


def smart(rows: list[GameRow], list_id: str, cfg: Config, now: datetime | None = None) -> list[GameRow]:
    now = now or datetime.now()
    floor = cfg.never_played_seconds
    if list_id == "never_played":
        return [r for r in rows if r.on_device and r.seconds < floor]
    if list_id == "not_played":
        cutoff = now - timedelta(days=cfg.not_played_days)
        return [r for r in rows if r.on_device and r.seconds >= floor
                and (r.last_played is None or r.last_played < cutoff)]
    if list_id == "most_played":
        return sorted((r for r in rows if r.seconds > 0), key=lambda r: -r.seconds)
    if list_id == "largest":
        return sorted((r for r in rows if r.on_device), key=lambda r: -r.size)
    if list_id == "device_only":
        return [r for r in rows if r.state == "device_only"]
    for ul in cfg.user_lists:
        if ul.id == list_id:
            return smartlists.apply(rows, ul, now, floor)
    raise KeyError(list_id)


def usage_by_system(rows: list[GameRow]) -> list[tuple[System, int]]:
    totals: dict[str, int] = {}
    for r in rows:
        if r.on_device:
            totals[r.system] = totals.get(r.system, 0) + r.size
    return sorted(((systems.get(k), v) for k, v in totals.items()), key=lambda kv: -kv[1])
