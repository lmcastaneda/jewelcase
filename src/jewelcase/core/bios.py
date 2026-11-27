"""BIOS check: which console BIOS and firmware files are on the handheld, and whether they are the right ones.

Jewelcase only looks. It never copies, downloads or names a source for a BIOS file. A file is recognised by
its md5 checksum (the file name is only a hint), and the checksum is remembered with the file's size
and modification time so a file is not read again until it changes.
"""
from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from . import systems
from .device import Device, DeviceError

BUILTIN = Path(__file__).resolve().parent.parent / "data" / "bios.toml"
MAX_HASH_BYTES = 16 * 1024 * 1024       # BIOS files are small; anything bigger is never read
HASH_SYSTEM = "_bios"                   # where the remembered hashes live in the store
NOTE = ("Jewelcase finds BIOS files on the handheld. Each emulator still has to be pointed at the folder "
        "in its own settings.")
LOOK_DEPTH = 4                          # folders below a BIOS folder that are listed
SEARCH_DEPTH = 3                        # how far down shared storage is searched for folders named *bios*
SKIP_WHEN_SEARCHING = {"android", "es-de", ".thumbnails", "lost.dir"}


@dataclass(frozen=True)
class KnownFile:
    name: str
    size: int
    md5: str
    role: str = ""                      # files with the same role are alternatives; each role is wanted


@dataclass(frozen=True)
class Spec:
    """What one system needs, and the files that count."""
    folder: str
    need: str                           # "needed" | "optional"
    files: tuple[KnownFile, ...] = ()
    note: str = ""
    hints: tuple[str, ...] = ()         # file name patterns of valid dumps the list may not know
    hint_sizes: tuple[int, ...] = ()    # ... and the sizes such a dump has (none: the hints do not count)
    also_for: tuple[str, ...] = ()      # other folders whose disc games use these files too


@dataclass(frozen=True)
class Firmware:
    """Firmware an emulator keeps inside its own app folder: a folder that exists means "installed"."""
    folder: str
    emulator: str
    path: str


@dataclass(frozen=True)
class BiosData:
    systems: dict[str, Spec]
    firmware: tuple[Firmware, ...] = ()

    def sizes(self) -> set[int]:
        return {f.size for s in self.systems.values() for f in s.files}

    def by_md5(self) -> dict[str, list[tuple[str, str, str]]]:
        """Checksum -> (system folder, name, role) of every entry that has it."""
        out: dict[str, list[tuple[str, str, str]]] = {}
        for s in self.systems.values():
            for f in s.files:
                out.setdefault(f.md5, []).append((s.folder, f.name, f.role))
        return out


@dataclass(frozen=True)
class Found:
    path: str
    size: int
    state: str                          # "verified" | "unrecognised" | "installed"
    known_as: str = ""                  # the name(s) the list gives a verified file
    roles: tuple[str, ...] = ()         # the roles those names fill


@dataclass(frozen=True)
class SystemBios:
    folder: str
    name: str
    need: str                           # "needed" | "optional"
    state: str                          # "verified" | "incomplete" | "unrecognised" | "missing" | "installed"
    files: tuple[Found, ...] = ()
    note: str = ""
    has_games: bool = False             # games on the handheld, or ticked to go there
    missing: str = ""                   # when "incomplete": the files still wanted, in a sentence


# ---------------------------------------------------------------- the data
def _specs(data: dict) -> list[Spec]:
    out = []
    for e in data.get("system", []):
        if not e.get("folder"):
            continue
        files = tuple(KnownFile(f["name"], int(f["size"]), str(f["md5"]).lower(), str(f.get("role", "")))
                      for f in e.get("file", []) if f.get("name") and f.get("md5") and f.get("size"))
        out.append(Spec(e["folder"], e.get("need", ""), files, e.get("note", ""), tuple(e.get("hints", ())),
                        tuple(int(n) for n in e.get("hint_sizes", ())), tuple(e.get("also_for", ()))))
    return out


def _firmware(data: dict) -> list[Firmware]:
    return [Firmware(e["folder"], e.get("emulator", ""), e["path"])
            for e in data.get("firmware", []) if e.get("folder") and e.get("path")]


def _read(source) -> dict:
    if isinstance(source, dict):
        return source
    try:
        return tomllib.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load(*extras) -> BiosData:
    """The shipped data, then each extra (a file path or a dict of the same shape) adding to it.

    A system named again keeps its files and gains the new ones; its note, need and hints are replaced
    by the new ones when the new entry says something."""
    merged: dict[str, Spec] = {}
    firmware: list[Firmware] = []
    for source in (BUILTIN, *extras):
        if source is None:
            continue
        data = _read(source)
        for spec in _specs(data):
            old = merged.get(spec.folder)
            if old is None:
                merged[spec.folder] = spec
                continue
            seen = {(f.name, f.md5) for f in old.files}
            first = old.files[0].role if old.files else ""      # a file with no role is one more alternative
            added = tuple(replace(f, role=f.role or first) for f in spec.files if (f.name, f.md5) not in seen)
            merged[spec.folder] = Spec(
                spec.folder, spec.need or old.need, old.files + added,
                spec.note or old.note, old.hints + tuple(h for h in spec.hints if h not in old.hints),
                old.hint_sizes + tuple(n for n in spec.hint_sizes if n not in old.hint_sizes),
                old.also_for + tuple(a for a in spec.also_for if a not in old.also_for))
        firmware += [f for f in _firmware(data) if f not in firmware]
    return BiosData(merged, tuple(firmware))


# ---------------------------------------------------------------- where to look
def _list_dirs(dev: Device, path: str) -> list[str]:
    try:
        return dev.list_dirs(path)
    except DeviceError:
        return []


def _tree(dev: Device, root: str, depth: int) -> list[str]:
    try:
        return dev.list_tree_dirs(root, depth)
    except DeviceError:
        return []


def _bios_folders(dev: Device, root: str, rom_root: str) -> list[str]:
    """Folders under `root` whose name contains "bios", a few levels down: one listing of the whole tree.
    The games folder, hidden folders and the apps' own folders are not entered, nor is a folder already found."""
    root = root.rstrip("/")
    found: list[str] = []
    for path in _tree(dev, root, SEARCH_DEPTH):
        parts = path[len(root) + 1:].split("/")
        chain = [root] + [root + "/" + "/".join(parts[:i]) for i in range(1, len(parts))]     # the folders it sits in
        if "bios" not in parts[-1].lower():
            continue
        if any(up == rom_root for up in chain[:-1]):            # the games folder is not entered
            continue
        if any((name := d.rsplit("/", 1)[-1].lower()) in SKIP_WHEN_SEARCHING or name.startswith(".") or "bios" in name
               for d in chain[1:]):
            continue
        found.append(path)
    return found


def locations(dev: Device, card: str, rom_root: str, packages: set[str]) -> list[str]:
    """Every folder worth listing, in the order they are looked at, without repeats."""
    out = ["/sdcard/RetroArch/system", card.rstrip("/") + "/RetroArch/system", "/sdcard/duckstation/bios"]
    for root in dict.fromkeys(["/sdcard", card.rstrip("/")]):
        out += _bios_folders(dev, root, rom_root.rstrip("/"))
    apps = "/sdcard/Android/data"
    if packages:
        for path in _tree(dev, apps, 3):            # package / files / (bios | system)
            parts = path[len(apps) + 1:].split("/")
            if len(parts) == 3 and parts[0] in packages and parts[1] == "files" and parts[2].lower() in ("bios", "system"):
                out.append(path)
    return list(dict.fromkeys(out))


def _files_in(dev: Device, folders: list[str]) -> list:
    seen, out = set(), []
    for folder in folders:
        try:
            files = dev.list_files(folder, LOOK_DEPTH)
        except DeviceError:
            continue                    # a folder that cannot be read is skipped
        for f in files:
            if f.path not in seen:
                seen.add(f.path)
                out.append(f)
    return out


# ---------------------------------------------------------------- recognising
def _hinted(spec: Spec, name: str, size: int) -> bool:
    lower = name.lower()
    return size in spec.hint_sizes and any(fnmatch.fnmatchcase(lower, h.lower()) for h in spec.hints)


def _md5(dev: Device, store, serial: str, f) -> str | None:
    """The file's md5, from the store while its size and time are unchanged, else read on the handheld."""
    known = store.dev_hash(serial, HASH_SYSTEM, f.path, f.size, f.mtime)
    if known:
        return known
    try:
        md5 = dev.md5(f.path)
    except DeviceError:
        return None
    store.set_dev_hash(serial, HASH_SYSTEM, f.path, f.size, f.mtime, md5)
    return md5


def recognise(dev: Device, store, serial: str, data: BiosData, files: list) -> dict[str, list[Found]]:
    """Files found per system folder. Only files that could be a BIOS (by size or name) are read."""
    sizes, by_md5 = data.sizes(), data.by_md5()
    found: dict[str, list[Found]] = {}
    for f in files:
        if f.size <= 0 or f.size > MAX_HASH_BYTES:
            continue
        base = PurePosixPath(f.path).name
        hinted = [s for s in data.systems.values() if _hinted(s, base, f.size)]
        if f.size not in sizes and not hinted:
            continue
        md5 = _md5(dev, store, serial, f)
        if md5 is None:
            continue
        if md5 in by_md5:
            names: dict[str, list[tuple[str, str]]] = {}
            for folder, name, role in by_md5[md5]:          # one file, however many names the list knows it by
                names.setdefault(folder, []).append((name, role))
            for folder, known in names.items():
                found.setdefault(folder, []).append(Found(
                    f.path, f.size, "verified", ", ".join(dict.fromkeys(n for n, _ in known)),
                    tuple(dict.fromkeys(r for _, r in known))))
        else:
            for s in hinted:
                found.setdefault(s.folder, []).append(Found(f.path, f.size, "unrecognised"))
    return found


def _state(spec: Spec, files: list[Found]) -> tuple[str, str]:
    """The system's state, and for "incomplete" the sentence naming what is still wanted."""
    if any(f.state == "verified" for f in files):
        have = {r for f in files if f.state == "verified" for r in f.roles}
        wanted: dict[str, list[str]] = {}
        for k in spec.files:
            wanted.setdefault(k.role, []).append(k.name)
        lacking = [names for role, names in wanted.items() if role not in have]
        if not lacking:
            return "verified", ""
        parts = []
        for names in lacking:
            names = list(dict.fromkeys(names))
            parts.append(names[0] if len(names) == 1 else "one of " + ", ".join(names[:4]) + ("…" if len(names) > 4 else ""))
        return "incomplete", "Still needed: " + "; ".join(parts) + "."
    return ("unrecognised" if files else "missing"), ""


def _has_games(spec: Spec, rows) -> bool:
    own = set(systems.get(spec.folder).device_exts) - {".zip"}
    for r in rows:
        if not (r.on_device or r.want):
            continue
        if r.system == spec.folder:
            return True
        if r.system in spec.also_for:   # e.g. Mega Drive folder games that are Sega CD discs
            unit = r.dev_unit if r.on_device and r.dev_unit else r.lib_unit
            if unit is not None and unit.ext in own:
                return True
    return False


def check_device(dev: Device, store, serial: str, card: str, rom_root: str, data: BiosData,
                 rows=(), packages: set[str] = frozenset()) -> list[SystemBios]:
    """Look at the handheld and say, per system that needs something, what is there."""
    files = _files_in(dev, locations(dev, card, rom_root, set(packages)))
    found = recognise(dev, store, serial, data, files)
    out = []
    for spec in data.systems.values():
        got = found.get(spec.folder, [])
        state, lacking = _state(spec, got)
        out.append(SystemBios(spec.folder, systems.get(spec.folder).name, spec.need, state,
                              tuple(sorted(got, key=lambda g: g.path)), spec.note, _has_games(spec, rows), lacking))
    for fw in data.firmware:
        if dev.exists(fw.path):
            out.append(SystemBios(fw.folder, systems.get(fw.folder).name, "needed", "installed",
                                  (Found(fw.path, 0, "installed", fw.emulator),), "", _has_games(
                                      Spec(fw.folder, "needed"), rows)))
    # systems with games on the handheld first; the table's order otherwise
    return sorted(out, key=lambda s: not s.has_games)


def check(session, rows=None) -> list[SystemBios]:
    """The check for a session's handheld. Raises DeviceGone when it is not connected."""
    from .device import DeviceGone
    dev = session.device
    if dev is None or session.profile is None:
        raise DeviceGone("no device connected")
    from .emulators import load_catalog
    packages = dev.installed_packages() & set(load_catalog(session.user_catalog))
    return check_device(dev, session.store, session.serial, session.profile.card_root, session.profile.rom_root,
                        session.bios_data(), session.rows() if rows is None else rows, packages)


# ---------------------------------------------------------------- command line
STATE_TEXT = {"verified": "verified", "incomplete": "incomplete", "unrecognised": "found, not recognised", "missing": "missing",
              "installed": "installed"}


def result_dict(results: list[SystemBios]) -> dict:
    return {"note": NOTE, "systems": [
        {"folder": r.folder, "name": r.name, "need": r.need, "state": r.state, "has_games": r.has_games,
         "note": r.note, "missing": r.missing, "files": [{"path": f.path, "size": f.size, "state": f.state, "known_as": f.known_as}
                                   for f in r.files]} for r in results]}


def result_text(results: list[SystemBios]) -> str:
    lines = [NOTE, ""]
    for r in results:
        lines.append(f"{r.name} ({r.need}): {STATE_TEXT[r.state]}" + ("  [games on the handheld]" if r.has_games else ""))
        if r.note:
            lines.append(f"  {r.note}")
        if r.missing:
            lines.append(f"  {r.missing}")
        for f in r.files:
            lines.append(f"  {f.path}" + (f"  ({f.known_as})" if f.known_as else ""))
    return "\n".join(lines)


def cmd_bios(s, args, out) -> int:
    from .device import DeviceGone
    try:
        results = check(s)
    except DeviceGone:
        print("No device connected." + (f" {s.problem}" if s.problem else ""))
        return 2
    out(args, result_dict(results), result_text(results))
    return 0
