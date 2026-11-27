"""Game IDs read out of the games themselves, so saves that emulators name by ID can be linked to a game.

PlayStation 2 and PSP discs are ISO 9660: the ID is in a small file in the root folder. GameCube and Wii
discs carry it in their first bytes. Only a few KB of a disc are ever read, and each file only once: the
answer (or "none found") is remembered against the file's size and modification time.
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import Store

SECTOR = 2048
MAX_DIR = 64 * 1024                     # a root folder bigger than this is not a game disc
MAX_FILE = 4096                         # SYSTEM.CNF and UMD_DATA.BIN are tiny; read no more than this
TIMEOUT = 60

# system folder -> file extensions that can be read for an ID
READABLE = {
    "ps2": (".iso", ".chd"),
    "psp": (".iso", ".chd"),
    "gamecube": (".iso", ".rvz", ".gcz"),
    "wii": (".iso", ".rvz", ".wbfs"),
}
NEEDS_DOLPHIN = (".rvz", ".gcz", ".wbfs")

ReadAt = Callable[[int, int], bytes]


class Unreadable(OSError):
    """The ID could not be read this time (a tool is missing or failed): not the same as "this disc has none"."""


_attempted: set[tuple] = set()          # files tried (and failed) in this run of the program: tried only once


@dataclass(frozen=True)
class GameRef:
    system: str
    stem: str
    title: str
    display: str                        # the ID as discs print it: SLUS-20312, ULUS10509, GM8E01


GameIds = dict[str, list[GameRef]]      # normalised ID -> the games that have it


def _chdman() -> str | None:
    return shutil.which("chdman")


def _dolphin_tool() -> str | None:
    return shutil.which("dolphin-tool")


def normalise(text: str) -> str:
    """"SLUS-20312" and "SLUS_203.12" and "slus20312" all become "SLUS20312"."""
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()


# ---------------------------------------------------------------- ISO 9660, a few sectors at a time
def _iso_file(read_at: ReadAt, name: str) -> bytes | None:
    """The start of one file in the root folder of an ISO 9660 image, or None."""
    head = read_at(16 * SECTOR, SECTOR)
    if len(head) < 190 or head[:6] != b"\x01CD001":
        return None
    extent, size = struct.unpack_from("<II", head, 156 + 2)[0], struct.unpack_from("<I", head, 156 + 10)[0]
    if not 0 < size <= MAX_DIR:
        return None
    root = read_at(extent * SECTOR, size)
    pos = 0
    while pos < len(root):
        length = root[pos]
        if length == 0:                     # records never cross a sector: the rest of it is padding
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        record = root[pos:pos + length]
        pos += length
        if len(record) < 34:
            break
        found = record[33:33 + record[32]].decode("latin-1").split(";")[0].upper()
        if found == name.upper() and not record[25] & 2:
            start, count = struct.unpack_from("<II", record, 2)[0], struct.unpack_from("<I", record, 10)[0]
            return read_at(start * SECTOR, min(count, MAX_FILE))
    return None


def _file_reader(path: Path) -> ReadAt:
    def read_at(offset: int, length: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(length)
    return read_at


def _chd_reader(path: Path, scratch: Path) -> ReadAt | None:
    """Reads a byte range of a DVD-type CHD by having chdman extract only that range."""
    exe = _chdman()
    if not exe:
        raise Unreadable("chdman is not installed")
    out = scratch / "range.bin"

    def read_at(offset: int, length: int) -> bytes:
        out.unlink(missing_ok=True)
        try:
            done = subprocess.run([exe, "extractdvd", "-i", str(path), "-o", str(out), "-f",
                                   "-isb", str(offset), "-ib", str(length)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise Unreadable(f"chdman did not finish: {e}") from None
        if done.returncode != 0 or not out.exists():
            raise Unreadable("chdman could not read the range")
        return out.read_bytes()[:length]
    return read_at


# ---------------------------------------------------------------- what each system keeps in its files
_PS2_BOOT = re.compile(rb"^\s*BOOT2?\s*=\s*cdrom0?:[\\/]*(?:[^\\/;\r\n]*[\\/])*([A-Za-z]{4})_(\d{3})\.(\d{2})",
                       re.MULTILINE | re.IGNORECASE)
_UMD = re.compile(rb"^([A-Za-z]{4})-?(\d{5})")
_GC_ID = re.compile(rb"[0-9A-Z]{6}")


def ps2_id(cnf: bytes) -> str | None:
    """"BOOT2 = cdrom0:\\SLUS_203.12;1" -> "SLUS-20312"."""
    m = _PS2_BOOT.search(cnf)
    return f"{m.group(1).decode().upper()}-{m.group(2).decode()}{m.group(3).decode()}" if m else None


def psp_id(umd: bytes) -> str | None:
    """UMD_DATA.BIN, which starts "ULUS-10509|..." -> "ULUS10509"."""
    m = _UMD.match(umd)
    return (m.group(1) + m.group(2)).decode().upper() if m else None


def gc_id(head: bytes) -> str | None:
    """The first bytes of a GameCube or Wii disc image: the 6-character game ID, with the disc magic after it."""
    if _GC_ID.fullmatch(head[:6]) and (head[0x1C:0x20] == b"\xC2\x33\x9F\x3D" or head[0x18:0x1C] == b"\x5D\x1C\x9E\xA3"):
        return head[:6].decode()
    return None


def dolphin_id(path: Path) -> str | None:
    """The game ID of a compressed Dolphin image, from `dolphin-tool header`, or None when it has none.

    Raises Unreadable when the tool is missing or fails."""
    exe = _dolphin_tool()
    if not exe:
        raise Unreadable("dolphin-tool is not installed")
    try:
        done = subprocess.run([exe, "header", "-i", str(path)], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise Unreadable(f"dolphin-tool did not finish: {e}") from None
    if done.returncode != 0:
        raise Unreadable("dolphin-tool could not read the file")
    m = re.search(r"Game ID:\s*([0-9A-Za-z]{4,6})\b", done.stdout)
    return m.group(1).upper() if m else None


def read_id(path: Path, system: str) -> str | None:
    """The game ID inside one disc image, or None when it was read and there is none.

    Raises OSError when the file cannot be read right now, also Unreadable when a needed tool is missing
    or fails (so the caller does not remember "none").
    """
    ext = path.suffix.lower()
    if ext not in READABLE.get(system, ()):
        return None
    if system in ("gamecube", "wii"):
        if ext in NEEDS_DOLPHIN:
            return dolphin_id(path)
        with open(path, "rb") as f:
            return gc_id(f.read(0x20))
    file, parse = ("SYSTEM.CNF", ps2_id) if system == "ps2" else ("UMD_DATA.BIN", psp_id)
    if ext == ".iso":
        data = _iso_file(_file_reader(path), file)
        return parse(data) if data else None
    with open(path, "rb"):                                  # a missing or unreadable file is an error here
        pass
    with tempfile.TemporaryDirectory(prefix="jewelcase-id-") as scratch:
        data = _iso_file(_chd_reader(path, Path(scratch)), file)
    return parse(data) if data else None


# ---------------------------------------------------------------- reading once, remembering
def _candidates(library, store: Store, rows, systems=None, keys=None):
    """(row, library file) of the games whose library file can be asked for an ID."""
    wanted = set(keys) if keys is not None else None
    files: dict[str, dict] = {}
    for r in rows:
        if r.system not in READABLE or (systems is not None and r.system not in systems) or r.lib_unit is None:
            continue
        if wanted is not None and r.key not in wanted:
            continue
        if Path(r.lib_unit.head).suffix.lower() not in READABLE[r.system]:
            continue
        if r.system not in files:
            files[r.system] = {f.name: f for f in store.lib_files(r.system)}
        f = files[r.system].get(r.lib_unit.head)
        if f:
            yield r, f


def known(library, store: Store, rows, systems=None, keys=None) -> GameIds:
    """The IDs already remembered. Reads no game file and touches no disk."""
    out: GameIds = {}
    for r, f in _candidates(library, store, rows, systems, keys):
        gid = store.lib_game_id(r.system, f.name, f.size, f.mtime_ns)
        if gid:
            out.setdefault(normalise(gid), []).append(GameRef(r.system, r.stem, r.title, gid))
    return out


def read_missing(library, store: Store, rows, systems=None, keys=None, should_stop=lambda: False) -> int:
    """Read and remember the ID of every candidate game that has not been read yet. Returns how many were read."""
    if not library.online:
        return 0
    done = 0
    for r, f in list(_candidates(library, store, rows, systems, keys)):
        if should_stop():
            break
        if store.lib_game_id(r.system, f.name, f.size, f.mtime_ns) is not None:
            continue
        attempt = (str(library.root), r.system, f.name, f.size, f.mtime_ns)
        if attempt in _attempted:               # failed earlier in this run: not again until the program restarts
            continue
        try:
            gid = read_id(library.file_path(r.system, f.name), r.system)
        except OSError:                         # could not be read now: not remembered as "none"
            _attempted.add(attempt)
            continue
        store.set_lib_game_id(r.system, f.name, f.size, f.mtime_ns, gid or "")
        done += 1
    return done


# ---------------------------------------------------------------- saves named by an ID
_PS_CODE = re.compile(r"^([A-Z]{4})[-_ ]?(\d{3})[._]?(\d{2})")                 # SLUS-20312, ULUS10509001
_GC_CODE = re.compile(r"^(?:[0-9A-Z]{2}-)?([0-9A-Z]{4}|[0-9A-Z]{6})(?=[-_. ]|$)")  # 01-GM8E-Title.gci, GM8E01.gci
ID_SYSTEMS = tuple(READABLE)


def save_code(name: str, system: str) -> str | None:
    """The game ID (or, for GameCube, its 4 or 6 first characters) a save's file or folder name starts with."""
    base = name.rsplit("/", 1)[-1]
    if system in ("gamecube", "wii"):
        m = _GC_CODE.match(base)
        return m.group(1) if m else None
    m = _PS_CODE.match(base)
    return "".join(m.groups()) if m else None


def match_save(name: str, systems, ids: GameIds) -> list[GameRef]:
    """The games whose ID the save's name carries; empty when there is none."""
    out: list[GameRef] = []
    for system in systems:
        code = save_code(name, system)
        if not code:
            continue
        for gid, refs in ids.items():
            if gid == code or (system in ("gamecube", "wii") and gid.startswith(code)):
                out += [r for r in refs if r.system == system]
    return out
