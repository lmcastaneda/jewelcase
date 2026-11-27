"""The game library on the PC: an index of names and sizes, with hashes filled in only when needed."""
from __future__ import annotations

import csv
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from . import systems
from .db import LibFile, Store
from .device import Progress, md5_file
from .games import Unit, group_units
from .systems import System

MANIFEST = "card-pull-manifest.tsv"


class ToolMissing(Exception):
    """An optional external tool (7z) is not installed."""


class Library:
    def __init__(self, root: Path | None, store: Store):
        self.root = Path(root) if root else None
        self.store = store
        self._index: dict[str, dict[str, LibFile]] = {}
        self._units: dict[str, list[Unit]] = {}

    # ------------------------------------------------------------ state
    @property
    def online(self) -> bool:
        """False for a missing folder and for an empty one (an unmounted mount point)."""
        try:
            return bool(self.root) and any(p.is_dir() for p in self.root.iterdir())
        except OSError:
            return False

    def invalidate(self) -> None:
        self._index.clear()
        self._units.clear()

    def scan(self) -> bool:
        """Refresh names, sizes and mtimes. Never reads file contents. Returns False when offline."""
        if not self.online:
            return False
        seen = set()
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", ".")):
                continue
            system = systems.get(d.name)
            try:
                files = []
                for f in sorted(d.iterdir()):
                    if f.is_file() and systems.is_rom_file(system, f.name):
                        st = f.stat()
                        files.append(LibFile(d.name, f.name, st.st_size, st.st_mtime_ns, None))
            except OSError:
                seen.add(d.name)        # unreadable right now: keep what we knew
                continue
            seen.add(d.name)
            self.store.replace_lib_files(d.name, files)
        for gone in set(self.store.lib_systems()) - seen:
            self.store.drop_lib_system(gone)
        self.invalidate()
        for system in self.store.lib_systems():
            self.units(system)          # reads (and remembers) the small .cue/.m3u files while the disk is here
        return True

    # ------------------------------------------------------------ index
    def _files(self, system: str) -> dict[str, LibFile]:
        if system not in self._index:
            self._index[system] = {f.name: f for f in self.store.lib_files(system)}
        return self._index[system]

    def systems(self) -> list[str]:
        return self.store.lib_systems()

    def has(self, system: str, name: str) -> bool:
        return name in self._files(system)

    def names(self, system: str) -> list[str]:
        return list(self._files(system))

    def size(self, system: str, name: str) -> int | None:
        f = self._files(system).get(name)
        return f.size if f else None

    def cached_md5(self, system: str, name: str) -> str | None:
        f = self._files(system).get(name)
        return f.md5 if f else None

    def fresh_md5(self, system: str, name: str) -> str | None:
        """The remembered hash, only if the file on disk still has the size and time it was hashed at."""
        known = self._files(system).get(name)
        if not known or not known.md5:
            return None
        try:
            st = self.file_path(system, name).stat()
        except OSError:
            return None
        return known.md5 if (st.st_size, st.st_mtime_ns) == (known.size, known.mtime_ns) else None

    def file_path(self, system: str, name: str) -> Path:
        return (self.root or Path("/nonexistent")) / system / name

    def units(self, system: str) -> list[Unit]:
        if system not in self._units:
            files = self._files(system)

            def read(name: str) -> str:
                # playlists are remembered, so an offline or failing disk does not change what a game is
                f = files[name]
                body = self.store.lib_text(system, name, f.size, f.mtime_ns)
                if body is None:
                    body = self.file_path(system, name).read_text(encoding="utf-8", errors="replace")
                    self.store.set_lib_text(system, name, f.size, f.mtime_ns, body)
                return body
            self._units[system] = group_units(files, read)
        return self._units[system]

    def forms(self, system: str) -> dict[str, list[Unit]]:
        out: dict[str, list[Unit]] = {}
        for u in self.units(system):
            out.setdefault(u.stem, []).append(u)
        return out

    def unit_size(self, system: str, unit: Unit) -> int:
        return sum(self.size(system, n) or 0 for n in unit.files)

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free if self.root else 0

    # ------------------------------------------------------------ hashing
    def md5(self, system: str, name: str, progress: Progress | None = None) -> str:
        """Hash of a library file, cached against its size and mtime. OSError propagates."""
        path = self.file_path(system, name)
        st = path.stat()
        known = self._files(system).get(name)
        if known and known.md5 and known.size == st.st_size and known.mtime_ns == st.st_mtime_ns:
            return known.md5
        if not known or known.size != st.st_size or known.mtime_ns != st.st_mtime_ns:
            fresh = [f for f in self._files(system).values() if f.name != name]
            fresh.append(LibFile(system, name, st.st_size, st.st_mtime_ns, None))
            self.store.replace_lib_files(system, fresh)
        digest = md5_file(path, progress)
        self.remember_md5(system, name, st.st_size, st.st_mtime_ns, digest)
        return digest

    def remember_md5(self, system: str, name: str, size: int, mtime_ns: int, md5: str) -> None:
        self.store.set_lib_md5(system, name, size, mtime_ns, md5)
        self._index.pop(system, None)
        self._units.pop(system, None)

    def add_file(self, system: str, name: str, md5: str | None = None) -> None:
        """Index a file that was just written into the library (import, copy from device)."""
        st = self.file_path(system, name).stat()
        files = [f for f in self.store.lib_files(system) if f.name != name]
        files.append(LibFile(system, name, st.st_size, st.st_mtime_ns, md5))
        self.store.replace_lib_files(system, files)
        self._index.pop(system, None)
        self._units.pop(system, None)

    def remove_file(self, system: str, name: str) -> None:
        """Forget a file that was just taken out of the library."""
        self.store.replace_lib_files(system, [f for f in self.store.lib_files(system) if f.name != name])
        self._index.pop(system, None)
        self._units.pop(system, None)

    # ------------------------------------------------------------ manifest
    def _manifest_rows(self) -> list[dict]:
        if not self.root:
            return []
        try:
            with open(self.root / MANIFEST, newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f, delimiter="\t"))
        except OSError:
            return []

    def import_manifest(self) -> int:
        """Take verified library hashes from a card-pull-manifest.tsv. Never writes that file."""
        used = 0
        applied = set(self.store.meta("", "manifest_applied", []))
        before = len(applied)
        for r in self._manifest_rows():
            md5 = (r.get("md5_venus") or "").strip()
            if r.get("state") != "ok" or not md5 or md5 != (r.get("md5_card") or "").strip():
                continue
            known = self._files(r.get("system", "")).get(r.get("file", ""))
            if known and str(known.size) == (r.get("size_venus") or "").strip():
                key = f"{known.system}/{known.name}"
                # once per file, and never over a hash we computed or one cleared because the file changed
                if key not in applied:
                    applied.add(key)
                    if known.md5 is None:
                        self.store.set_lib_md5(known.system, known.name, known.size, known.mtime_ns, md5)
                used += 1
        if len(applied) != before:
            self.store.set_meta("", "manifest_applied", sorted(applied))
            self.invalidate()
        return used

    def manifest_device_hashes(self) -> list[tuple[str, str, int, str]]:
        out = []
        for r in self._manifest_rows():
            md5 = (r.get("md5_card") or "").strip()
            if r.get("state") == "ok" and re.fullmatch(r"[0-9a-f]{32}", md5) and (r.get("bytes") or "").isdigit():
                out.append((r["system"], r["file"], int(r["bytes"]), md5))
        return out


def pick_device_form(system: System, forms: list[Unit], on_device_exts: set[str]) -> Unit | None:
    """Which form of a game goes to the device: what is already there, else the table's preference."""
    ready = [u for u in forms if systems.device_ready(system, u.ext)]
    for u in ready:
        if u.ext in on_device_exts:
            return u
    if not ready:
        return None
    order = system.device_exts
    return min(ready, key=lambda u: order.index(u.ext) if u.ext in order else len(order))


# ---------------------------------------------------------------- archives (7z)
def _seven_zip() -> str:
    for name in ("7z", "7zz", "7za"):
        found = shutil.which(name)
        if found:
            return found
    raise ToolMissing("7z is not installed")


def archive_members(archive: Path) -> list[tuple[str, int]]:
    exe = _seven_zip()
    cp = subprocess.run([exe, "l", "-slt", "-ba", "--", str(archive)], stdin=subprocess.DEVNULL,
                        capture_output=True, timeout=300)       # no terminal: never wait for a password
    if cp.returncode != 0:
        return []
    out = []
    for block in cp.stdout.decode("utf-8", "replace").split("\n\n"):
        path = re.search(r"^Path = (.*)$", block, re.M)
        size = re.search(r"^Size = (\d+)$", block, re.M)
        folder = re.search(r"^(?:Folder = \+|Attributes = D)", block, re.M)
        if path and size and not folder:
            out.append((path.group(1).strip(), int(size.group(1))))
    return out


def find_archive_member(archive: Path, member_name: str, size: int) -> bool:
    return any(Path(n).name == member_name and s == size for n, s in archive_members(archive))


def archive_member_md5(archive: Path, member_name: str) -> str:
    return _stream_member(archive, member_name, None)[0]


def archive_member_head(archive: Path, member_name: str, limit: int) -> bytes:
    """The first `limit` bytes of one member, without extracting the rest."""
    return _stream_member(archive, member_name, limit)[1]


def _stream_member(archive: Path, member_name: str, limit: int | None) -> tuple[str, bytes]:
    exe = _seven_zip()
    full = next((n for n, _ in archive_members(archive) if Path(n).name == member_name), member_name)
    proc = subprocess.Popen([exe, "x", "-so", "--", str(archive), full], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    h, head = hashlib.md5(), b""
    try:
        while True:
            block = proc.stdout.read(1 << 20)
            if not block:
                break
            if limit is not None:
                head += block
                if len(head) >= limit:
                    proc.kill()
                    return "", head[:limit]
            else:
                h.update(block)
    finally:
        proc.stdout.close()
        proc.wait()
    if limit is None and proc.returncode != 0:
        raise OSError(f"7z could not read {member_name} from {archive.name}")
    return h.hexdigest(), head


def archive_ok(archive: Path) -> bool:
    exe = _seven_zip()
    return subprocess.run([exe, "t", "--", str(archive)], stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=3600).returncode == 0
