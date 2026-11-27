"""New downloads: which files look like games, which system they are for, and moving them into the library."""
from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import convert, systems
from .db import Store
from .device import md5_file
from .games import group_units
from .library import (Library, ToolMissing, archive_member_head, archive_members, archive_ok,
                      pick_device_form)

HEAD = 2 * 1024 * 1024
HEADROOM = 16 * 1024 * 1024
UNIQUE = {
    ".gba": "gba", ".gbc": "gbc", ".gb": "gb", ".nes": "nes", ".fds": "nes", ".sfc": "snes", ".smc": "snes",
    ".n64": "n64", ".z64": "n64", ".v64": "n64", ".nds": "nds", ".3ds": "n3ds", ".cci": "n3ds",
    ".wbfs": "wii", ".gdi": "dreamcast", ".cso": "psp", ".gen": "megadrive",
    ".sms": "mastersystem", ".a26": "atari2600", ".a78": "atari7800", ".j64": "atarijaguar",
    ".pce": "pcengine", ".xci": "switch", ".nsp": "switch", ".vpk": "psvita", ".wua": "wiiu",
}
BY_HEADER = {".iso", ".rvz", ".wia", ".gcm", ".md"}      # ".md" is a Mega Drive ROM or a markdown note
ONLY_IF_RECOGNISED = {".md"}
AMBIGUOUS = {".chd", ".cue", ".m3u", ".pbp", ".ciso", ".gcz"}
ARCHIVES = {".zip", ".7z"}
PARTIAL = (".crdownload", ".part", ".tmp", ".download")
NOT_GAMES = {".pdf", ".doc", ".docx", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".mkv",
             ".html", ".htm", ".exe", ".msi", ".apk", ".csv", ".xlsx", ".json", ".xml", ".py", ".sh",
             ".deb", ".rpm", ".svg", ".nfo", ".url", ".ini", ".log"}
LISTED = set(UNIQUE) | BY_HEADER | AMBIGUOUS | ARCHIVES


class InboxError(Exception):
    """An import that could not be done, in words for the user."""


@dataclass(frozen=True)
class InboxItem:
    path: Path                      # the file, or the .cue/.m3u that owns the others
    files: tuple[Path, ...]
    size: int
    system: str | None
    how: str
    state: str                      # ready | pick_system | downloading

    @property
    def stem(self) -> str:
        return self.path.stem


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


def system_from_head(head: bytes) -> str | None:
    """Identify a disc image from its first bytes. Never from its name."""
    if head[0x1C:0x20] == bytes.fromhex("C2339F3D"):
        return "gamecube"
    if head[0x18:0x1C] == bytes.fromhex("5D1C9EA3"):
        return "wii"
    if head[:4] in (b"RVZ\x01", b"WIA\x01") and len(head) >= 0x4C:
        return {1: "gamecube", 2: "wii"}.get(struct.unpack(">I", head[0x48:0x4C])[0])
    if head[0x100:0x104] == b"SEGA":
        return "megadrive"
    if head[:15] == b"SEGA SEGAKATANA":
        return "dreamcast"
    if head[:15] == b"SEGA SEGASATURN":
        return "saturn"
    if head[0x8001:0x8006] == b"CD001":
        ident = head[0x8008:0x8028]
        if ident.startswith(b"PSP GAME"):
            return "psp"
        if ident.startswith(b"PLAYSTATION"):
            if b"BOOT2" in head:
                return "ps2"
            if re.search(rb"BOOT\s*=", head):
                return "psx"
    return None


def _members(path: Path) -> list[str]:
    if _ext(path.name) == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                return [i.filename for i in z.infolist() if not i.is_dir()]
        except (zipfile.BadZipFile, OSError):
            return []
    try:
        return [n for n, _ in archive_members(path)]
    except (ToolMissing, OSError):
        return []


def _member_head(path: Path, member: str) -> bytes:
    try:
        if _ext(path.name) == ".zip":
            with zipfile.ZipFile(path) as z, z.open(member) as f:
                return f.read(HEAD)
        return archive_member_head(path, Path(member).name, HEAD)
    except (ToolMissing, OSError, zipfile.BadZipFile, RuntimeError):
        return b""


def _game_members(path: Path) -> list[str]:
    return [m for m in _members(path) if _ext(m) not in NOT_GAMES and not m.endswith("/")]


def looks_like_game(path: Path) -> bool:
    ext = _ext(path.name)
    if ext in ONLY_IF_RECOGNISED:
        return detect_system(path)[0] is not None
    if ext not in ARCHIVES:
        return ext in LISTED
    members = _game_members(path)
    if any(_ext(m) in LISTED - ARCHIVES or _ext(m) == ".bin" for m in members):
        return True
    # arcade sets: short lowercase names holding numbered chip dumps
    return bool(members) and bool(re.fullmatch(r"[a-z0-9_]{2,16}", path.stem))


def detect_system(path: Path) -> tuple[str | None, str]:
    ext = _ext(path.name)
    if ext in UNIQUE:
        return UNIQUE[ext], "file type"
    if ext in BY_HEADER:
        try:
            with open(path, "rb") as f:
                found = system_from_head(f.read(HEAD))
        except OSError:
            found = None
        return (found, "disc header") if found else (None, "ambiguous")
    if ext in ARCHIVES:
        found = set()
        members = _game_members(path)
        for m in members:
            mext = _ext(m)
            if mext in UNIQUE:
                found.add(UNIQUE[mext])
            elif mext in BY_HEADER and len(members) == 1:
                found.add(system_from_head(_member_head(path, m)))
            else:
                found.add(None)
        if len(found) == 1 and None not in found:
            return found.pop(), "inside the archive"
    return None, "ambiguous"


class Inbox:
    def __init__(self, folder: Path | None):
        self.folder = Path(folder) if folder else None
        self._seen: dict[tuple, tuple] = {}

    def scan(self, now: float | None = None) -> list[InboxItem]:
        now = now if now is not None else time.time()
        try:
            entries = [p for p in sorted(self.folder.iterdir()) if p.is_file()] if self.folder else []
        except OSError:
            return []
        names = {p.name: p for p in entries}
        items: list[InboxItem] = []
        complete = []
        for p in entries:
            lower = p.name.lower()
            if lower.endswith(PARTIAL):
                inner = p.name.rsplit(".", 1)[0]
                if inner in names:
                    continue                       # shown through its placeholder file below
                if _ext(inner) in LISTED:
                    items.append(InboxItem(p, (p,), p.stat().st_size, None, "", "downloading"))
                continue
            complete.append(p.name)

        def read(name: str) -> str:
            return names[name].read_text(encoding="utf-8", errors="replace")

        for unit in group_units(complete, read):
            head = names[unit.head]
            files = tuple(names[n] for n in unit.files)
            if _ext(head.name) not in LISTED:
                continue
            stats = [f.stat() for f in files]
            size = sum(s.st_size for s in stats)
            busy = any(n + suffix in names for n in unit.files for suffix in PARTIAL) \
                or any(now - s.st_mtime < 5 for s in stats)
            key = (str(head), size, max(s.st_mtime_ns for s in stats))
            if busy:
                # still being written: say so, but only for things that are plainly games
                if _ext(head.name) not in ARCHIVES or looks_like_game(head):
                    items.append(InboxItem(head, files, size, None, "", "downloading"))
                continue
            if key not in self._seen:
                self._seen[key] = (looks_like_game(head), *detect_system(head))
            is_game, system, how = self._seen[key]
            if not is_game:
                continue
            items.append(InboxItem(head, files, size, system, how, "ready" if system else "pick_system"))
        return items


def _archive_is_sound(path: Path) -> bool:
    if _ext(path.name) == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                return z.testzip() is None
        except Exception:
            return False
    try:
        return archive_ok(path)
    except ToolMissing:
        raise InboxError("7z is needed to check this archive, and it is not installed")


CONVERTING = ("unpacking", "compressing", "verifying_game")


@dataclass(frozen=True)
class ImportProgress:
    """Where an import is. Work is counted in bytes read, and every file is read twice: once to copy it,
    once to verify the copy."""
    phase: str                      # checking (an archive) | copying | verifying | then, if it is converted:
                                    # unpacking | compressing | verifying_game
    name: str                       # the file being worked on
    file: int                       # which file that is, from 1
    files: int
    done: int
    total: int
    copy_done: int = 0              # the same for the import alone, without the work of converting
    copy_total: int = 0             # (0: not told, as with a conversion that has no import)

    @property
    def converting(self) -> bool:
        """True while the game is being made out of the archive, after the import itself is done."""
        return self.phase in CONVERTING

    @property
    def fraction(self) -> float | None:
        """0..1, or None while an archive is checked: that has no byte count."""
        if self.phase == "checking":
            return None
        return min(1.0, self.done / self.total) if self.total else 1.0


Report = Callable[[str, str, int], None]        # phase, file name, bytes of work done in this item so far


def _copy_verified(src: Path, dest: Path, report: Report, base: int) -> str:
    part = dest.with_name(dest.name + ".part")
    h = hashlib.md5()
    done = 0
    try:
        with open(src, "rb") as fin, open(part, "wb") as fout:
            while True:
                block = fin.read(1 << 20)
                if not block:
                    break
                h.update(block)
                fout.write(block)
                done += len(block)
                report("copying", src.name, base + done)
            fout.flush()
            os.fsync(fout.fileno())         # a failing disk must show up before the original is deleted
        if md5_file(part, lambda n: report("verifying", src.name, base + done + n)) != h.hexdigest():
            raise InboxError(f"{src.name} did not copy correctly; the original is untouched")
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    return h.hexdigest()


@dataclass(frozen=True)
class ImportRecord:
    """What one import did, so it can be undone."""
    system: str
    stem: str
    files: tuple[tuple[Path, Path, bool], ...]      # (where it was, where it is in the library, created there)
    hashes: dict
    ticked: bool
    serial: str | None = None       # the device whose tick this import set
    made: tuple[tuple[str, int, int], ...] = ()     # (name, bytes, mtime_ns) of the files conversion made
    convert_problem: str = ""       # why the archive was imported but not converted, if so


_UNKNOWN = object()


def _still_right(conv: convert.Conversion, system: str, library: Library) -> bool:
    """Is a conversion worked out before the import still what importing should do? Not when something the
    import itself (or another import) put in the library has changed the answer."""
    names = library.forms(system).get(conv.stem, [])
    return not (any(systems.device_ready(systems.get(system), u.ext) for u in names)
                or any(library.has(system, n) or library.file_path(system, n).exists() for n in conv.files))


def _convert_imported(item: InboxItem, system: str, library: Library, cache: Path, tell: Report,
                      offset: int, planned=_UNKNOWN) -> tuple[tuple[tuple[str, int, int], ...], str]:
    """Make the playable game out of an archive that has just landed in the library.

    Returns what was made (name, bytes, mtime) and, if it could not be, why. Never raises for a failed conversion:
    the archive is imported either way.
    """
    name = item.files[0].name
    try:
        if planned is not _UNKNOWN and (planned is None or planned.problem or _still_right(planned, system, library)):
            conv = planned                  # the archive was already read when the work was counted
        else:
            conv = convert.for_import(library, system, library.file_path(system, name))
        if conv is None:
            return (), ""
        if conv.problem:
            return (), conv.problem

        def report(phase: str, done: int) -> None:
            tell("verifying_game" if phase == "verifying" else phase, name, offset + done)

        made = convert.run(library, conv, cache, report)
        stats = [(n, *_fingerprint(library.file_path(system, n))) for n in made]
        return tuple(stats), ""
    except convert.ConvertError as e:
        return (), str(e)
    except (OSError, subprocess.SubprocessError) as e:
        return (), f"{e.strerror or e}"


def _fingerprint(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def import_item(item: InboxItem, system: str, library: Library, store: Store, serial: str | None,
                report: Report | None = None, convert_cache: Path | None = None,
                planned=_UNKNOWN) -> ImportRecord:
    """Move a download into <library>/<system>/: copy, verify, then delete the original.

    With `convert_cache` (the work folder for conversions), an archive that can be turned into the game the
    handheld plays is converted right after it lands.
    """
    def tell(phase: str, name: str, n: int) -> None:
        if report:
            report(phase, name, n)

    if item.state == "downloading":
        raise InboxError("this file is still downloading")
    if not system:
        raise InboxError("choose a system first")
    if not library.online:
        raise InboxError("the library folder is not available right now")
    for f in item.files:
        if _ext(f.name) in ARCHIVES:
            tell("checking", f.name, 0)
            if not _archive_is_sound(f):
                raise InboxError(f"{f.name} is damaged or incomplete; download it again")
    if library.free_bytes() < item.size + HEADROOM:
        raise InboxError("the library disk is full")
    folder = library.root / system
    folder.mkdir(parents=True, exist_ok=True)
    plan = []
    done = 0                        # bytes of work so far: two reads of every file
    hashes = {}
    for f in item.files:
        dest = folder / f.name
        if dest.exists():           # the same file is already there: nothing to copy, only to compare
            size = f.stat().st_size
            same = dest.stat().st_size == size
            if same:
                hashes[f.name] = md5_file(dest, lambda n, f=f, at=done: tell("verifying", f.name, at + n))
                same = hashes[f.name] == md5_file(f, lambda n, f=f, at=done + size: tell("verifying", f.name, at + n))
            if not same:
                raise InboxError(f"the library already has a different file named {f.name}")
            plan.append((f, dest, False))
            done += 2 * size
        else:
            plan.append((f, dest, True))
    try:
        for f, dest, create in plan:
            if create:
                size = f.stat().st_size
                hashes[f.name] = _copy_verified(f, dest, tell, done)
                library.add_file(system, f.name, hashes[f.name])
                done += 2 * size
    except OSError as e:
        raise InboxError(f"could not write to the library: {e.strerror or e}")
    for f, _, _ in plan:
        f.unlink(missing_ok=True)
    made, problem = (), ""
    if convert_cache is not None and len(item.files) == 1 and _ext(item.files[0].name) in ARCHIVES:
        made, problem = _convert_imported(item, system, library, convert_cache, tell, 2 * item.size, planned)
    ticked = False
    if serial:
        forms = library.forms(system).get(item.stem, [])
        if pick_device_form(systems.get(system), forms, set()) is not None:
            ticked = store.wanted(serial).get((system, item.stem)) is not True
            store.set_wanted(serial, system, item.stem, True)
    return ImportRecord(system, item.stem, tuple(plan), hashes, ticked, serial, made, problem)


def _conversion_plan(item: InboxItem, system: str, library: Library):
    """What converting this download will be, as far as can be told before it is imported (`_UNKNOWN` when that
    cannot be told here)."""
    if len(item.files) != 1 or _ext(item.files[0].name) not in ARCHIVES:
        return None
    try:
        return convert.for_import(library, system, item.path)
    except (OSError, subprocess.SubprocessError):
        return _UNKNOWN


def import_items(pairs: list[tuple[InboxItem, str]], library: Library, store: Store, serial: str | None,
                 progress: Callable[[ImportProgress], None] | None = None,
                 convert_cache: Path | None = None) -> tuple[list[ImportRecord], list[str]]:
    """Import one item after another. Returns what was imported, and for the others why not.

    With `convert_cache`, archives are converted as they are imported; their work counts in the progress too.
    """
    # each archive is read once: what is learnt here is what the conversion after the import uses
    plans = [_conversion_plan(item, system, library) if convert_cache is not None and library.online else None
             for item, system in pairs]
    extra = [p.work if isinstance(p, convert.Conversion) and not p.problem else 0 for p in plans]
    copy_total = 2 * sum(item.size for item, _ in pairs)
    total = copy_total + sum(extra)
    files = sum(len(item.files) for item, _ in pairs)
    records: list[ImportRecord] = []
    problems: list[str] = []
    base = first = copied = 0       # work and files of the items before this one; the import's own part of it
    for (item, system), more, plan in zip(pairs, extra, plans):
        index = {f.name: first + n for n, f in enumerate(item.files, 1)}
        cap = 2 * item.size + more      # what this item may use of the bar, whatever it turns out to take

        def report(phase: str, name: str, n: int, base=base, index=index, cap=cap, copied=copied, item=item) -> None:
            if progress:
                mine = 2 * item.size if phase in CONVERTING else min(n, 2 * item.size)
                progress(ImportProgress(phase, name, index.get(name, first + 1), files,
                                        min(total, base + min(n, cap)), total, copied + mine, copy_total))

        try:
            records.append(import_item(item, system, library, store, serial, report, convert_cache, plan))
        except InboxError as e:
            problems.append(f"{item.path.name}: {e}")
        base += 2 * item.size + more
        copied += 2 * item.size
        first += len(item.files)
        if progress:                # an item that was skipped still moves the bar past its share
            progress(ImportProgress("verifying", item.files[-1].name, first, files, min(total, base), total,
                                    copied, copy_total))
    return records, problems


def undo_import(record: ImportRecord, library: Library, store: Store, serial: str | None) -> list[str]:
    """Put an imported download back where it was. Refuses if anything has changed since.

    Files that conversion made after the import are taken out too, but only those that are still exactly as
    it left them. Returns the names of any it left in the library because they have changed.
    """
    if not library.online:
        raise InboxError("the library folder is not available right now")
    for src, dest, _ in record.files:
        if src.exists():
            raise InboxError(f"{src.name} is already in the downloads folder again")
        if not dest.is_file() or md5_file(dest) != record.hashes.get(src.name):
            raise InboxError(f"{src.name} has changed since it was imported, so it was left in the library")
    removable, left = [], []
    for name, size, mtime_ns in record.made:
        try:
            ours = _fingerprint(library.file_path(record.system, name)) == (size, mtime_ns)
        except OSError:
            continue                # already gone: nothing to take out
        (removable if ours else left).append(name)
    try:
        for src, dest, created in record.files:
            src.parent.mkdir(parents=True, exist_ok=True)
            _copy_verified(dest, src, lambda *_: None, 0)
            if created:
                dest.unlink()
                library.remove_file(record.system, dest.name)
        for name in removable:
            library.file_path(record.system, name).unlink(missing_ok=True)
            library.remove_file(record.system, name)
    except OSError as e:
        raise InboxError(f"could not move {record.stem} back: {e.strerror or e}")
    if record.ticked and (record.serial or serial):
        store.set_wanted(record.serial or serial, record.system, record.stem, None)
    return left


def cmd_inbox(session, args, out) -> int:
    from .fmt import human_size
    items = Inbox(session.cfg.inbox).scan()
    data = [{"file": i.path.name, "size": i.size, "system": i.system, "how": i.how, "state": i.state}
            for i in items]
    text = "\n".join(f"{i.state:<12} {human_size(i.size):>9}  {i.system or '?':<12} {i.path.name}" for i in items)
    out(args, data, text or f"Nothing that looks like a game in {session.cfg.inbox}.")
    return 0
