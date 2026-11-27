"""Making the file the handheld plays out of an archive, and keeping it in the library beside the archive.

The archive is never changed. What comes out of it is written under a temporary name, checked, and only
then given its real name, so a conversion that stops half way leaves nothing behind.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import secrets
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from . import systems
from .device import md5_file
from .library import Library, ToolMissing, _seven_zip, archive_members
from .systems import System

ARCHIVES = (".7z", ".rar", ".zip")
DISC_IMAGES = (".iso", ".cue", ".gdi")      # what chdman can make a CHD from
DVD_SYSTEMS = {"ps2", "psp"}                # an .iso of these is a DVD (or UMD), everything else is a CD
HEADROOM = 64 * 1024 * 1024
Report = Callable[[str, int], None]         # phase (unpacking | compressing | verifying), units of work done


class ConvertError(Exception):
    """The conversion did not happen. The text says why, for the user."""


@dataclass(frozen=True)
class Disc:
    """One disc (or the one game file) of a conversion: what it is made from and what it becomes."""
    members: tuple[tuple[str, int], ...]            # (source, bytes); the disc's main file first
    files: tuple[str, ...]                          # the library files it makes; the main file first
    how: str = "extract"                            # extract | chd | rvz
    disc: str = ""                                  # for a CHD: dvd | cd
    loose: bool = False                             # made from files the library already has, not an archive

    @property
    def source(self) -> int:
        return sum(size for _, size in self.members)

    @property
    def work(self) -> int:
        """Units of work for progress: every byte is unpacked and read back (an archive only); a CHD is then
        made and checked, an RVZ made."""
        passes = {"extract": 2, "chd": 4, "rvz": 3}[self.how]
        return self.source * (passes - 2 if self.loose and self.how != "extract" else passes)


@dataclass(frozen=True)
class Conversion:
    system: str
    stem: str
    archive: str                                    # the archive's file name in the library ("" for loose files)
    members: tuple[tuple[str, int], ...] = ()       # (path inside the archive, bytes); the game's main file first
    files: tuple[str, ...] = ()                     # the library files it makes; the game's main file first
    sizes: dict = field(default_factory=dict, compare=False)    # file -> bytes (a CHD: at most this)
    how: str = "extract"                            # extract | chd | rvz (of the first disc)
    disc: str = ""                                  # for a CHD: dvd | cd
    problem: str = ""                               # why it cannot be converted
    discs: tuple[Disc, ...] = ()                    # one for a game, one each for a game on several discs
    playlist: str = ""                              # the .m3u that joins several discs (the first of `files`)
    playlist_text: str = ""

    @property
    def bytes(self) -> int:
        return sum(self.sizes.values())

    @property
    def source(self) -> int:
        return sum(size for _, size in self.members)

    @property
    def work(self) -> int:
        return sum(d.work for d in self.discs)


def _single(system: str, stem: str, archive: str, disc: Disc, sizes: dict) -> Conversion:
    return Conversion(system, stem, archive, disc.members, disc.files, sizes, disc.how, disc.disc, discs=(disc,))


def _chdman() -> str | None:
    return shutil.which("chdman")


def _dolphin() -> str | None:
    return shutil.which("dolphin-tool")


def archives(system: System, names: Iterable[str]) -> dict[str, str]:
    """Game -> the archive it could be converted from: named like the game, and not itself playable."""
    found: dict[str, str] = {}
    for name in sorted(names):
        p = PurePosixPath(name)
        if p.suffix.lower() in ARCHIVES and not systems.device_ready(system, p.suffix):
            found.setdefault(p.stem, name)
    return found


def archive_of(system: System, names: Iterable[str], stem: str) -> str:
    return archives(system, names).get(stem, "")


def wanted_form(system: System, ext: str) -> str:
    """The form this system's games are kept in, when a source of type `ext` is better made into it: ".chd",
    ".rvz", or "" when the source is fine as it is. The table's order is the preference: a form that comes
    before the source's own is the one wanted."""
    ready = system.device_exts

    def before(form: str) -> bool:
        return form in ready and (ext not in ready or ready.index(form) < ready.index(ext))

    if ext in DISC_IMAGES and before(".chd"):
        return ".chd"
    if ext == ".iso" and before(".rvz"):
        return ".rvz"
    return ""


def available_form(system: System, ext: str) -> str:
    """`wanted_form`, or "" when the tool that makes it is not installed."""
    form = wanted_form(system, ext)
    tool = {".chd": _chdman, ".rvz": _dolphin}.get(form)
    return form if tool and tool() else ""


_listings: dict[tuple, list[tuple[str, int]]] = {}


def _members(path: Path) -> list[tuple[str, int]]:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _listings:
        _listings[key] = archive_members(path)
    return _listings[key]


def _name(path: str) -> str:
    return PurePosixPath(path).name


def _natural(name: str) -> list:
    """Sort key that puts "Disc 2" before "Disc 10"."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.casefold())]


def _disc_for(system: str, out_stem: str, members: tuple[tuple[str, int], ...], ext: str, form: str,
              rename_head: bool, loose: bool = False) -> Disc:
    """What one disc becomes. `form` is ".chd", ".rvz" or "" (kept as it is, under its own file names)."""
    if form == ".chd":
        kind = "dvd" if ext == ".iso" and system in DVD_SYSTEMS else "cd"
        return Disc(members, (out_stem + ".chd",), "chd", kind, loose)
    if form == ".rvz":
        return Disc(members, (out_stem + ".rvz",), "rvz", "", loose)
    names = [_name(path) for path, _ in members]
    if rename_head:
        names[0] = out_stem + ext
    return Disc(members, tuple(names), "extract", "", loose)


def _form_for(info: System, ext: str) -> str:
    """The form a source of this type is made into, or ConvertError when it cannot be made into one the
    handheld takes."""
    form = available_form(info, ext)
    if not form and not systems.device_ready(info, ext):
        if wanted_form(info, ext) == ".chd":
            raise ConvertError(f"chdman is not installed. It is needed to convert a {ext} file for {info.name}.")
        raise ConvertError(f"The archive holds a {ext} file, which is not a format the handheld's {info.name} "
                           "emulators take.")
    return form


def look(library: Library, system: str, stem: str) -> Conversion | None:
    """What converting this game would make, or why it cannot be done. None when it has no archive."""
    info = systems.get(system)
    archive = archive_of(info, library.names(system), stem)
    if not archive:
        return None
    return _look_archive(library, info, system, stem, archive, library.file_path(system, archive))


def for_import(library: Library, system: str, path: Path) -> Conversion | None:
    """What importing this archive (in the downloads folder or already in the library) should convert it to.

    None when there is nothing to do: it is not an archive that needs converting, or the library already has
    the game in a form the handheld plays. Otherwise a Conversion, which may carry the reason it cannot be made.
    """
    info = systems.get(system)
    ext = path.suffix.lower()
    if ext not in ARCHIVES or systems.device_ready(info, ext):
        return None
    forms = library.forms(system).get(path.stem, [])
    if any(systems.device_ready(info, u.ext) for u in forms):
        return None
    return _look_archive(library, info, system, path.stem, path.name, path)


def _look_archive(library: Library, info: System, system: str, stem: str, archive: str, path: Path) -> Conversion:
    def no(problem: str) -> Conversion:
        return Conversion(system, stem, archive, problem=problem)

    try:
        members = _members(path)
    except ToolMissing:
        return no("7z is not installed.")
    except (OSError, subprocess.SubprocessError):
        members = []
    if not members:
        return no("The archive could not be read.")
    roms = [m for m in members if systems.is_rom_file(info, _name(m[0]))]
    if not roms:
        return no("The archive holds no game file.")
    exts = {m[0]: systems.ext_of(m[0]) for m in roms}
    heads = [m for m in roms if exts[m[0]] in (".cue", ".gdi")]
    isos = [m for m in roms if exts[m[0]] == ".iso"]
    if ".m3u" in exts.values():
        return no("The archive holds more than one game, which Jewelcase cannot convert yet.")
    try:
        if len(heads) > 1 or (not heads and len(isos) > 1):
            if ".m3u" not in info.device_exts:
                return no(f"The archive holds more than one disc, and the handheld's {info.name} emulators take no "
                          "playlist to put them together as one game.")
            conv = _look_discs(info, system, stem, archive, path, heads or isos, roms, exts)
        else:
            if not heads and len(roms) > 1:
                return no("The archive holds more than one game, which Jewelcase cannot convert yet.")
            head = heads[0] if heads else roms[0]
            ordered = (head, *(m for m in roms if m != head))
            inside = [_name(m[0]).casefold() for m in ordered]
            if len(set(inside)) != len(inside):
                return no("The archive holds two files with the same name.")
            ext = exts[head[0]]
            disc = _disc_for(system, stem, ordered, ext, _form_for(info, ext), rename_head=True)
            if len({n.casefold() for n in disc.files}) != len(disc.files):
                return no("The archive holds two files with the same name.")
            conv = _single(system, stem, archive, disc, _sizes(disc))
    except ConvertError as e:
        return no(str(e))
    for name in conv.files:
        if library.has(system, name) or library.file_path(system, name).exists():
            return no(f"The library already has a file named {name}.")
    return conv


def _sizes(disc: Disc) -> dict:
    """File -> bytes it will have (a CHD or RVZ: at most the size of what it is made from)."""
    if disc.how == "extract":
        return {name: size for name, (_, size) in zip(disc.files, disc.members)}
    return {disc.files[0]: disc.source}


def _look_discs(info: System, system: str, stem: str, archive: str, path: Path, heads: list, roms: list,
                exts: dict) -> Conversion:
    """An archive of several discs: each one is made like a single game, and a playlist joins them."""
    heads = sorted(heads, key=lambda m: _natural(m[0]))
    owned_by, forms = [], []
    for head in heads:
        owned = [head]
        ext = exts[head[0]]
        if ext in (".cue", ".gdi"):                 # the disc's own track files, as its sheet names them
            text = _read_member(path, head[0])
            for ref in (_cue_refs(text) if ext == ".cue" else _gdi_refs(text)):
                found = _find_track(roms, heads, ref, head[0])
                if found is None:
                    raise ConvertError(f"{_name(head[0])} names {ref}, which is not in the archive.")
                if found not in owned:
                    owned.append(found)
        inside = [_name(m[0]).casefold() for m in owned]
        if len(set(inside)) != len(inside):
            raise ConvertError("The archive holds two files with the same name.")
        owned_by.append(tuple(owned))
        forms.append(_form_for(info, ext))
    named = {m for owned in owned_by for m in owned}
    stray = [_name(m[0]) for m in roms if m not in named]
    if stray:                                       # never drop a member silently
        raise ConvertError(f"The archive holds files that no disc names: {', '.join(stray)}.")
    stems = [PurePosixPath(h[0]).stem for h in heads]
    if all(forms) and len({s_.casefold() for s_ in stems}) != len(stems):
        stems = [f"{stem} (Disc {n})" for n, _ in enumerate(heads, 1)]      # every disc is called disc.gdi, say
    discs = [_disc_for(system, out, members, exts[h[0]], form, rename_head=False)
             for h, out, members, form in zip(heads, stems, owned_by, forms)]
    playlist = stem + ".m3u"
    text = "".join(d.files[0] + "\n" for d in discs)
    files = (playlist, *(n for d in discs for n in d.files))
    if len({n.casefold() for n in files}) != len(files):
        raise ConvertError("The archive holds two files with the same name.")
    sizes = {playlist: len(text.encode())}
    for d in discs:
        sizes.update(_sizes(d))
    members = tuple(m for d in discs for m in d.members)
    return Conversion(system, stem, archive, members, files, sizes, discs[0].how, discs[0].disc,
                      discs=tuple(discs), playlist=playlist, playlist_text=text)


def _cue_refs(text: str) -> list[str]:
    from .games import cue_files
    return cue_files(text)


def _gdi_refs(text: str) -> list[str]:
    """The track files a .gdi lists: its first line is a count, then `number start type sector file offset`.
    The file name may be quoted, or not, and may hold quotes and apostrophes."""
    out = []
    for line in text.lstrip("\ufeff").splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        rest = parts[4].strip()
        tail = rest.rsplit(None, 1)
        if rest.startswith('"') and rest.find('"', 1) > 0:
            name = rest[1:rest.find('"', 1)]
        else:                                       # no quotes, or one that never closes: the offset ends it
            name = tail[0] if len(tail) == 2 and tail[1].isdigit() else rest
            name = name.strip('"')
        if name:
            out.append(name)
    return out


def _find_track(roms: list, heads: list, ref: str, sheet: str) -> tuple[str, int] | None:
    """The member a sheet names: of those with that file name, the one beside the sheet if there is one."""
    others = [m for m in roms if m not in heads]
    same = [m for m in others if _name(m[0]) == ref] or [m for m in others if _name(m[0]).casefold() == ref.casefold()]
    beside = [m for m in same if PurePosixPath(m[0]).parent == PurePosixPath(sheet).parent]
    return (beside or same or [None])[0]


_texts: dict[tuple, str] = {}


def _start_7z(args: list[str], **kw) -> subprocess.Popen:
    """Start 7z on an archive, or say why that cannot be done."""
    try:
        return subprocess.Popen([_seven_zip(), *args], stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except ToolMissing:
        raise ConvertError("7z is not installed.") from None
    except OSError as e:
        raise ConvertError(f"7z could not be started: {e.strerror or e}.") from None


def _read_member(archive: Path, member: str, limit: int = 1 << 20) -> str:
    """The text of a small member (a cue sheet), straight from the archive. Remembered like `_members`:
    reading it can mean unpacking everything before it in a solid archive."""
    try:
        st = archive.stat()
    except OSError as e:
        raise ConvertError(f"{archive.name} could not be read: {e.strerror or e}.") from None
    key = (str(archive), st.st_size, st.st_mtime_ns, member)
    if key in _texts:
        return _texts[key]
    proc = _start_7z(["x", "-so", "--", str(archive), member], stdout=subprocess.PIPE)
    try:
        body = proc.stdout.read(limit + 1)
    finally:
        proc.kill()
        proc.stdout.close()
        proc.wait()
    if not body or len(body) > limit:
        raise ConvertError(f"{_name(member)} could not be read from {archive.name}.")
    _texts[key] = body.decode("utf-8-sig", "replace")
    return _texts[key]


def look_loose(library: Library, system: str, stem: str, forms: dict | None = None) -> Conversion | None:
    """What making a smaller form of a disc image the library already has would make, or why not.

    None when there is nothing better to make: no loose .iso, .cue or .gdi, no tool for the form the system
    prefers, or the library already has that form. The original stays where it is.
    `forms` is `library.forms(system)`, for a caller that asks about many games of one system.
    """
    info = systems.get(system)
    forms = (library.forms(system) if forms is None else forms).get(stem, [])
    for unit in forms:
        form = available_form(info, unit.ext) if unit.ext in DISC_IMAGES else ""
        if not form or any(u.ext == form for u in forms):
            continue
        name = unit.stem + form
        if unit.missing:
            return Conversion(system, stem, "", problem=f"The library is missing {unit.missing[0]}, "
                                                       f"which {unit.head} needs.")
        if library.has(system, name) or library.file_path(system, name).exists():
            return Conversion(system, stem, "", problem=f"The library already has a file named {name}.")
        members = tuple((n, library.size(system, n) or 0) for n in unit.files)
        disc = _disc_for(system, unit.stem, members, unit.ext, form, rename_head=False, loose=True)
        return _single(system, stem, "", disc, _sizes(disc))
    return None


# ---------------------------------------------------------------- doing it
def _unpack_member(archive: Path, member: str, size: int, dest: Path, tell: Callable[[int], None]) -> str:
    """Write one member of the archive to `dest`. Returns the md5 of what 7z gave."""
    proc = _start_7z(["x", "-so", "--", str(archive), member], stdout=subprocess.PIPE)
    digest, done = hashlib.md5(), 0
    try:
        with open(dest, "wb") as out:
            while True:
                block = proc.stdout.read(1 << 20)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                done += len(block)
                tell(done)
            out.flush()
            os.fsync(out.fileno())          # a failing disk must show up before the file is trusted
        code = proc.wait()                  # 7z checks the archive's own checksum last: its verdict counts
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
    if code != 0 or done != size:
        raise ConvertError(f"{PurePosixPath(member).name} could not be unpacked from {archive.name}. "
                           "The archive may be damaged.")
    return digest.hexdigest()


_PERCENT = re.compile(rb"(\d+(?:\.\d+)?)% complete")


def _tool(cmd: list[str], percent: Callable[[float], None]) -> tuple[int, str]:
    """Run chdman (or dolphin-tool), following the percentage it prints. Returns its exit code and the last
    thing it said."""
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        raise ConvertError(f"{Path(cmd[0]).name} could not be started: {e.strerror or e}.") from None
    said, last = b"", ""
    try:
        while True:
            block = proc.stderr.read1(4096)
            if not block:
                break
            said = (said + block)[-4096:]
            found = _PERCENT.findall(block)
            if found:
                percent(float(found[-1]))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stderr.close()
    lines = [line.strip() for line in re.split(r"[\r\n]+", said.decode("utf-8", "replace")) if line.strip()]
    last = next((line for line in reversed(lines) if "% complete" not in line), "")
    return proc.returncode, last


def _lock_claim(path: Path, wait: bool = False) -> int | None:
    """Take the lock file `path` (made if need be); None when someone else holds it."""
    while True:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except OSError:
            os.close(fd)
            return None
        if os.fstat(fd).st_nlink:                   # not removed by a cleanup between the open and the lock
            return fd
        os.close(fd)


@contextmanager
def _work_folder(cache_dir: Path):
    """A folder under <cache>/convert for one conversion, locked for as long as it is in use so that no other
    conversion (in this or another process) clears it away as stale."""
    cache = Path(cache_dir) / "convert"
    cache.mkdir(parents=True, exist_ok=True)
    while True:
        name = "work-" + secrets.token_hex(6)
        fd = _lock_claim(cache / (name + ".lock"))
        if fd is not None:
            break
    work = cache / name
    try:
        work.mkdir()
        yield work
    finally:
        shutil.rmtree(work, ignore_errors=True)
        (cache / (name + ".lock")).unlink(missing_ok=True)
        os.close(fd)


def _clear_stale(cache_dir: Path) -> None:
    """Remove what a conversion that was killed left in <cache>/convert, but never a folder that a conversion
    which is still running holds."""
    cache = Path(cache_dir) / "convert"
    cache.mkdir(parents=True, exist_ok=True)
    for entry in list(cache.iterdir()):
        if entry.suffix == ".lock" and entry.is_file():
            fd = _lock_claim(entry)
            if fd is None:                          # held: its conversion is running
                continue
            try:
                shutil.rmtree(entry.with_suffix(""), ignore_errors=True)
                entry.unlink(missing_ok=True)
            finally:
                os.close(fd)
        elif entry.is_dir():
            if not entry.with_name(entry.name + ".lock").exists():      # a folder is always locked first
                shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def _place(part: Path, final: Path) -> None:
    """Give a finished part its real name, failing (never replacing) when something is already there."""
    taken = ConvertError(f"The library already has a file named {final.name}.")
    try:
        os.link(part, final)                        # fails if `final` exists, even if it appeared a moment ago
    except FileExistsError:
        raise taken from None
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS):
            raise
        if final.exists():                          # a disk without hard links: the best that can be done
            raise taken from None
        part.rename(final)
        return
    part.unlink()


def run(library: Library, conv: Conversion, cache_dir: Path, report: Report | None = None) -> tuple[str, ...]:
    """Make the game's files in the library, beside the archive. Raises ConvertError; never replaces a file.
    Returns the names of the files it made."""
    if conv.problem:
        raise ConvertError(conv.problem)
    folder = library.root / conv.system
    archive = folder / conv.archive if conv.archive else None
    for name in conv.files:
        if (folder / name).exists():
            raise ConvertError(f"The library already has a file named {name}.")
    if library.free_bytes() < conv.bytes + HEADROOM:
        raise ConvertError("There is not enough room in the library for the converted game.")
    tell = report or (lambda phase, done: None)
    parts = {name: folder / (name + ".part") for name in conv.files}
    try:
        for part in parts.values():
            part.unlink(missing_ok=True)            # left by a run that was killed
        cache = Path(cache_dir) / "convert"
        if any(d.how != "extract" and not d.loose for d in conv.discs):
            _clear_stale(cache_dir)                 # what a conversion that was killed left behind
        hashes: dict[str, str] = {}
        if conv.playlist:
            body = conv.playlist_text.encode()
            with open(parts[conv.playlist], "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            hashes[conv.playlist] = hashlib.md5(body).hexdigest()
        base = 0                                    # work of the discs before this one: progress only moves forward
        for disc in conv.discs:
            def at(phase: str, done: int, base=base) -> None:
                tell(phase, base + done)

            if disc.how == "extract":
                hashes.update(_unpack(disc, archive, parts, at))
            else:
                if disc.loose:
                    hashes.update(_make_disc(disc, archive, folder, None, parts[disc.files[0]], at))
                else:
                    with _work_folder(cache_dir) as work:
                        if shutil.disk_usage(work).free < disc.source + HEADROOM:
                            raise ConvertError(f"There is not enough room in {cache} to unpack the archive.")
                        hashes.update(_make_disc(disc, archive, folder, work, parts[disc.files[0]], at))
            base += disc.work
        tell("verifying", conv.work)                # the last chance to stop: after this the game is kept
        placed: list[str] = []
        try:
            for name in (*conv.files[1:], conv.files[0]):       # the file that makes it a game goes in last
                _place(parts[name], folder / name)
                placed.append(name)
            for name in conv.files:
                library.add_file(conv.system, name, hashes.get(name))
            return conv.files
        except BaseException:
            for name in placed:                     # all of its files, or none
                (folder / name).unlink(missing_ok=True)
                library.remove_file(conv.system, name)
            raise
    except OSError as e:
        raise ConvertError(f"The converted game could not be written: {e.strerror or e}.") from e
    finally:
        for part in parts.values():
            part.unlink(missing_ok=True)


def _unpack(disc: Disc, archive: Path, parts: dict[str, Path], tell: Report) -> dict[str, str]:
    hashes, base = {}, 0
    for (member, size), name in zip(disc.members, disc.files):
        hashes[name] = _unpack_member(archive, member, size, parts[name],
                                      lambda done, base=base: tell("unpacking", base + done))
        base += size
    for (_, size), name in zip(disc.members, disc.files):
        if md5_file(parts[name], lambda done, base=base: tell("verifying", base + done)) != hashes[name]:
            raise ConvertError(f"{name} did not read back the same after it was written.")
        base += size
    return hashes


def _make_disc(disc: Disc, archive: Path | None, folder: Path, work: Path | None, out: Path,
               tell: Report) -> dict[str, str]:
    """Make a CHD or RVZ of one disc: from the files the library has, or from the archive through `work`."""
    source, base = disc.source, 0
    if disc.loose:
        head, start = folder / disc.members[0][0], 0
    else:
        unpacked = {}
        for member, size in disc.members:
            name = PurePosixPath(member).name
            unpacked[name] = _unpack_member(archive, member, size, work / name,
                                            lambda done, base=base: tell("unpacking", base + done))
            base += size
        for name, (_, size) in zip(unpacked, disc.members):
            if md5_file(work / name, lambda done, base=base: tell("unpacking", base + done)) != unpacked[name]:
                raise ConvertError(f"{name} did not read back the same after it was unpacked.")
            base += size
        head, start = work / PurePosixPath(disc.members[0][0]).name, 2 * source
    chd = disc.how == "chd"
    name = "chdman" if chd else "dolphin-tool"
    tool = _chdman() if chd else _dolphin()
    if not tool:
        raise ConvertError(f"{name} is not installed.")
    if chd:
        cmd = [tool, "createdvd" if disc.disc == "dvd" else "createcd", "-i", str(head), "-o", str(out)]
    else:
        cmd = [tool, "convert", "-i", str(head), "-o", str(out), "-f", "rvz", "-b", "131072", "-c", "zstd", "-l", "5"]
    code, said = _tool(cmd, lambda pct: tell("compressing", start + int(source * pct / 100)))
    if code != 0 or not out.is_file() or out.stat().st_size == 0:
        raise ConvertError(f"{name} could not make the {'CHD' if chd else 'RVZ'}: {said or 'it gave no reason'}")
    tell("compressing", start + source)
    if chd:
        code, said = _tool([tool, "verify", "-i", str(out)],
                           lambda pct: tell("verifying", start + source + int(source * pct / 100)))
        if code != 0:
            raise ConvertError("The CHD did not pass chdman's check, so it was thrown away.")
    with open(out, "rb") as f:
        os.fsync(f.fileno())
    return {}


# ---------------------------------------------------------------- command line
def cmd_convert(session, args, out) -> int:
    """`jewelcase convert KEY…`: make the playable files (or a smaller form of a disc image) now, without a sync
    or a device."""
    import sys

    from .fmt import human_size
    from .sync import Busy, job_lock
    rows = {f"{r.system}/{r.stem}": r for r in session.rows()}
    missing = [k for k in args.keys if k not in rows]
    if missing:
        raise SystemExit("No such game: " + ", ".join(missing))
    if not session.library.online:
        print("The library folder is not available right now.")
        return 2
    converted, failed, lines = [], [], []
    try:
        with job_lock(session.data_dir):
            for key in args.keys:
                row = rows[key]
                # a game the library has in a playable form can still be made smaller; one it has only as an
                # archive is made playable
                try:
                    conv = (look_loose if row.lib_unit else look)(session.library, row.system, row.stem)
                except ToolMissing as e:
                    conv = Conversion(row.system, row.stem, "", problem=f"{e}.")
                except (OSError, subprocess.SubprocessError) as e:
                    conv = Conversion(row.system, row.stem, "", problem=f"{getattr(e, 'strerror', None) or e}.")
                if conv is None:
                    problem = ("the library already has it in a form the handheld plays." if row.lib_unit
                               else "the library has no archive of it.")
                elif conv.problem:
                    problem = conv.problem
                else:
                    seen: list[str] = []

                    def report(phase: str, done: int) -> None:
                        if phase not in seen and not args.json:
                            seen.append(phase)
                            print(f"  {row.title}: {phase}…", file=sys.stderr)
                    try:
                        run(session.library, conv, session.cache_dir, report)
                        problem = ""
                    except ConvertError as e:
                        problem = str(e)
                    except ToolMissing as e:
                        problem = f"{e}."
                    except (OSError, subprocess.SubprocessError) as e:
                        problem = f"{getattr(e, 'strerror', None) or e}."
                if problem:
                    failed.append({"key": key, "reason": problem})
                    lines.append(f"{row.title}: {problem}")
                    continue
                size = sum(session.library.size(row.system, n) or 0 for n in conv.files)
                converted.append({"key": key, "files": list(conv.files), "bytes": size})
                beside = "its archive" if conv.archive else "the original, which is kept"
                lines.append(f"{row.title}: made {', '.join(conv.files)} ({human_size(size)}) beside {beside}.")
    except Busy as e:
        print(e)
        return 2
    out(args, {"converted": converted, "failed": failed}, "\n".join(lines))
    return 2 if failed else 0
