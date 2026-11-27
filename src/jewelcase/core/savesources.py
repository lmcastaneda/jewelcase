"""Where emulators keep saves on the device, and which files there are saves.

A source is one folder of one emulator plus the patterns that pick the saves out of it. Folders also
hold BIOS images, keys, settings and caches, so nothing counts as a save unless a pattern says so.
"""
from __future__ import annotations

import re
import struct
import tomllib
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .device import Device, DeviceError
from .gameids import GameIds, match_save

BUILTIN = Path(__file__).resolve().parent.parent / "data" / "saves.toml"
MAX_FILE = 256 << 20
DEFAULT_EXCLUDE = ("**/.nomedia",)
KINDS = ("save", "state", "settings")
ITEM_MODES = ("name", "folder", "file")


@dataclass(frozen=True)
class Source:
    id: str
    package: str                    # the emulator's package, as in the emulator catalogue
    label: str
    kind: str                       # "save" | "state" | "settings"
    root: str                       # device folder
    include: tuple[str, ...]        # globs below root; "**" spans folders; case is ignored
    items: str                      # "name": files named after a game; "folder": one directory per game;
                                    # "file": every file on its own (memory cards)
    item_depth: int = 1             # items == "folder": path parts below root that name the item
    suffix: str = ""                # items == "name": regex removed from the file name to get the game's name
    root_from: tuple[str, str] | None = None    # (device file, key); key "" means the file's first line
    below: str = ""                 # appended to a root taken from root_from
    advice: str = ""                # what to do when folders here cannot be read
    exclude: tuple[str, ...] = ()
    restore: bool = True            # False when this emulator cannot read files Jewelcase puts back
    noun: str = ""                  # what its items are called when not "Save": "Memory Card"


@dataclass(frozen=True)
class SaveFile:
    source: str
    rel: str                        # path below the source's folder
    size: int
    mtime: int


@dataclass(frozen=True)
class SourceScan:
    source: Source
    root: str
    exists: bool
    files: tuple[SaveFile, ...]
    unreadable: tuple[str, ...]     # device folders that may hold saves and cannot be entered
    too_large: tuple[str, ...]      # rels over MAX_FILE
    blocked: tuple[str, ...] = ()   # rels of saves that are listed but cannot be opened


@dataclass(frozen=True)
class Item:
    key: str                        # "<source id>/<item name>"
    source: str
    name: str
    kind: str
    shape: str                      # "game" | "folder" | "shared"
    files: tuple[str, ...]
    game: tuple[str, str] | None    # (system folder, stem)
    title: str
    linked: str = ""                # the title of the game whose ID the item's name carries
    games: tuple[tuple[str, str], ...] = ()     # every game with that ID (`game` is the first)


# ---------------------------------------------------------------- the list of sources
def _read(path: Path) -> list[Source]:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for e in data.get("source", []):
        include = e.get("include")
        if not (e.get("id") and e.get("package") and str(e.get("root", "")).startswith("/")
                and e.get("kind") in KINDS and e.get("items") in ITEM_MODES
                and isinstance(include, list) and include):
            continue
        rf = e.get("root_from")
        root_from = (str(rf["file"]), str(rf.get("key", ""))) if isinstance(rf, dict) and rf.get("file") else None
        out.append(Source(
            id=str(e["id"]), package=str(e["package"]), label=str(e.get("label") or e["package"]),
            kind=e["kind"], root=str(e["root"]).rstrip("/"), include=tuple(str(p) for p in include),
            items=e["items"], item_depth=max(1, int(e.get("item_depth", 1))), suffix=str(e.get("suffix", "")),
            root_from=root_from, below=str(e.get("below", "")).strip("/"), advice=str(e.get("advice", "")),
            exclude=tuple(str(p) for p in e.get("exclude", ())), restore=bool(e.get("restore", True)),
            noun=str(e.get("noun", ""))))
    return out


def load_sources(extra: Path | None = None) -> list[Source]:
    """The bundled sources, with a user file of the same shape adding to them or replacing by id."""
    by_id = {s.id: s for s in _read(BUILTIN)}
    if extra:
        by_id.update({s.id: s for s in _read(extra)})
    return list(by_id.values())


# ---------------------------------------------------------------- memory cards
# An SD card's id (/storage/C0DE-CAFE/...) belongs to one card. A backup of another handheld can name it
# in a source's folder or inside a settings file; on this handheld the same files live on its own card.
_CARD_ID = re.compile(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}")
_CARD_IN_PATH = re.compile(rb"/storage/([0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})(?![0-9A-Za-z-])")


def card_ids(dev: Device) -> list[str]:
    """The ids of the memory cards in the handheld, as /storage names them."""
    return sorted(d for d in dev.list_dirs("/storage") if _CARD_ID.fullmatch(d))


def _own_card(foreign: set[str], cards: list[str], subject: str) -> tuple[str | None, str]:
    """The one card to put in place of another card's id, or the reason there is none (`subject` starts it)."""
    names = " and ".join(sorted(foreign))
    said = f"{subject} memory card {names}, which the connected handheld does not have"
    if len(cards) == 1 and len(foreign) == 1:
        return cards[0], ""
    if not cards:
        return None, f"{said}, and it has no memory card."
    if len(foreign) > 1:
        return None, f"{said}, and Jewelcase cannot tell which card each one becomes."
    return None, f"{said}, and it has {len(cards)} memory cards, so Jewelcase cannot tell which one is meant."


def map_root(root: str, cards: list[str]) -> tuple[str | None, str]:
    """(folder on this handheld, "") for a folder that may sit on another handheld's card; (None, why) if unsure."""
    m = _CARD_IN_PATH.match(root.encode())
    if not m or m.group(1).decode().upper() in {c.upper() for c in cards}:
        return root, ""
    card, why = _own_card({m.group(1).decode()}, cards, "Its folder is on")
    return (f"/storage/{card}" + root[len(m.group(0)):], "") if card else (None, why)


# The ways a settings file may write a card's id: /storage/ID, /mnt/media_rw/ID, the same with JSON's
# escaped slashes (\/storage\/ID), and Android document links (.../tree/ID%3AFolder, .../document/ID%3A...).
_SLASH = rb"(?:\\?/)"
_CARD_SPELLINGS = re.compile(
    rb"(?:" + _SLASH + rb"(?:storage|mnt" + _SLASH + rb"media_rw)" + _SLASH + rb"(?P<path>[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})"
    rb"(?![0-9A-Za-z-])|(?:tree|document)" + _SLASH + rb"(?P<doc>[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})(?=%3[Aa]))")


def _spelled_ids(data: bytes):
    for m in _CARD_SPELLINGS.finditer(data):
        group = "path" if m.group("path") else "doc"
        yield m.start(group), m.end(group), m.group(group).decode()


def map_text(data: bytes, cards: list[str]) -> tuple[bytes | None, str]:
    """A settings file with another card's id in its paths changed to this handheld's card.

    Every spelling of the id counts (see above). Only done when there is exactly one other id and exactly
    one card here; otherwise (None, why). A file without any other card's id comes back untouched.
    """
    own = {c.upper() for c in cards}
    spans = [s for s in _spelled_ids(data) if s[2].upper() not in own]
    foreign = {s[2].upper() for s in spans}
    if not foreign:
        return data, ""
    card, why = _own_card(foreign, cards, "It names")
    if not card:
        return None, why
    out, last = [], 0
    for start, end, _ in spans:
        out += [data[last:start], card.encode()]
        last = end
    return b"".join(out + [data[last:]]), ""


# ---------------------------------------------------------------- patterns
@lru_cache(maxsize=512)
def _glob(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z", re.IGNORECASE)


def is_save(source: Source, rel: str) -> bool:
    if any(_glob(p).match(rel) for p in DEFAULT_EXCLUDE + source.exclude):
        return False
    return any(_glob(p).match(rel) for p in source.include)


def _could_hold(pattern: str, dir_rel: str) -> bool:
    """Could a file matching this pattern sit inside this folder?"""
    parts, dirs = pattern.split("/"), dir_rel.split("/")
    for i, part in enumerate(parts):
        if "**" in part or i == len(dirs):
            return True
        if not _glob(part).match(dirs[i]):
            return False
    return False


# ---------------------------------------------------------------- scanning the device
def resolve_root(dev: Device, source: Source) -> str:
    """The folder to look in: the one the emulator's own settings name, else the usual one."""
    if not source.root_from:
        return source.root
    path, key = source.root_from
    try:
        text = dev.read_bytes(path).decode("utf-8", "replace")
    except DeviceError:
        return source.root
    if key:
        m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"?([^"\r\n]*)"?\s*$', text, re.MULTILINE)
        value = m.group(1).strip() if m else ""
    else:
        value = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not value.startswith("/"):
        return source.root                  # "default", "~/…" or nothing: the emulator uses its usual folder
    root = value.rstrip("/")
    return f"{root}/{source.below}" if source.below else root


def scan_source(dev: Device, source: Source, claimed: set[str] | None = None,
                root: str | None = None) -> SourceScan:
    """List the saves of one source. `claimed` holds device paths an earlier source already took.

    `root` is the folder to look in when the caller has already worked it out (a restore, which may
    have mapped it to this handheld's memory card).
    """
    root = root or resolve_root(dev, source)
    if not dev.exists(root):
        return SourceScan(source, root, False, (), (), ())
    files, too_large, blocked = [], [], []
    prefix = root + "/"
    listed = dev.list_files(root)
    names = {f.path for f in listed}
    refused = dev.unreadable_paths(root)
    if root in refused:                         # the folder itself cannot be entered: whatever is in it is unseen
        return SourceScan(source, root, True, (), (root,), ())
    refused = [p for p in refused if p.startswith(prefix)]
    closed = set(refused) & names               # files that are listed but may not be opened
    for f in listed:
        if not f.path.startswith(prefix):
            continue
        rel = f.path[len(prefix):]
        if not is_save(source, rel) or (claimed is not None and f.path in claimed):
            continue
        if claimed is not None:
            claimed.add(f.path)
        if f.path in closed:
            blocked.append(rel)
        elif f.size > MAX_FILE:
            too_large.append(rel)
        else:
            files.append(SaveFile(source.id, rel, f.size, f.mtime))
    unreadable = tuple(d for d in refused if d not in names
                       and any(_could_hold(p, d[len(prefix):]) for p in source.include))
    return SourceScan(source, root, True, tuple(files), unreadable, tuple(too_large), tuple(blocked))


def scan_all(dev: Device, sources: list[Source], include_states: bool = True,
             include_settings: bool = True) -> list[SourceScan]:
    """Scan every source. A file two sources would both take belongs to the first."""
    claimed: set[str] = set()
    return [scan_source(dev, s, claimed) for s in sources
            if (include_states or s.kind != "state") and (include_settings or s.kind != "settings")]


# ---------------------------------------------------------------- items: what a person calls "a save"
def item_name(source: Source, rel: str) -> tuple[str, str]:
    """(item name, shape before game matching) for one file."""
    if source.items == "folder":
        parts = rel.split("/")
        if len(parts) > source.item_depth:
            return "/".join(parts[:source.item_depth]), "folder"
        return rel, "shared"
    if source.items == "name":
        folder, _, base = rel.rpartition("/")
        stem = re.sub(source.suffix, "", base, flags=re.IGNORECASE) if source.suffix else base.rsplit(".", 1)[0]
        return (f"{folder}/{stem}" if folder else stem), "name"
    return rel, "shared"


def group_items(source: Source, rels: Iterable[str], stems: dict[str, set[str]],
                systems: tuple[str, ...], ids: GameIds | None = None) -> list[Item]:
    """Group a source's files into items. `stems` maps a system folder to the names of its games.

    `ids` (see gameids) links items named by a game's ID, such as a PS2 save state, to that game.
    """
    grouped: dict[str, tuple[str, list[str]]] = {}
    for rel in rels:
        name, shape = item_name(source, rel)
        grouped.setdefault(name, (shape, []))[1].append(rel)
    items = []
    for name, (shape, files) in grouped.items():
        game, title = None, name.rsplit("/", 1)[-1]
        if shape == "name":
            game = next(((s, title) for s in systems if title in stems.get(s, ())), None)
            shape = "game" if game else "shared"
        item = Item(f"{source.id}/{name}", source.id, name, source.kind, shape, tuple(files), game, title)
        if ids and game is None:
            refs = match_save(title, systems, ids)
            if refs:
                item = replace(item, shape="game", game=(refs[0].system, refs[0].stem), linked=refs[0].title,
                               games=tuple(dict.fromkeys((r.system, r.stem) for r in refs)))
        items.append(item)
    return items


def sfo_title(data: bytes) -> str | None:
    """The TITLE entry of a PlayStation PARAM.SFO, which names the game a save folder belongs to."""
    try:
        if data[:4] != b"\0PSF":
            return None
        _, key_start, data_start, count = struct.unpack_from("<IIII", data, 4)
        for i in range(count):
            key_off, _fmt, length, _max, data_off = struct.unpack_from("<HHIII", data, 20 + 16 * i)
            end = data.index(b"\0", key_start + key_off)
            if data[key_start + key_off:end] == b"TITLE":
                raw = data[data_start + data_off:data_start + data_off + length]
                if len(raw) < length:
                    return None
                return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip() or None
    except (struct.error, ValueError):
        return None
    return None
