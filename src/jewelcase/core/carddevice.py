"""A device that is a mounted folder: an SD card in a reader, or any folder. No adb, no apps."""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat as stat_mod
from pathlib import Path
from typing import Iterable

from .device import (Device, DeviceError, DeviceGone, DeviceInfo, PackageInfo, Progress, RemoteFile,
                     Storage)

NO_APPS = "not available on a card"


def card_is_mounted(folder: Path) -> bool:
    """A folder that is there and has something in it. An empty one is a mount point with nothing mounted."""
    try:
        return Path(folder).is_dir() and any(True for _ in Path(folder).iterdir())
    except OSError:
        return False


def card_is_empty(folder: Path) -> bool:
    """A folder that is there and has nothing in it: a freshly formatted card, or nothing mounted yet."""
    try:
        return Path(folder).is_dir() and not any(True for _ in Path(folder).iterdir())
    except OSError:
        return False


def mount_point(path: Path) -> Path:
    """The folder the filesystem holding `path` is mounted at."""
    p = Path(path).resolve()
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return p


def filesystem_uuid(path: Path) -> str | None:
    """The filesystem's UUID, read from /dev/disk/by-uuid (readable without root); None when not found."""
    try:
        wanted = os.stat(path).st_dev
        links = list(Path("/dev/disk/by-uuid").iterdir())
    except OSError:
        return None
    for link in links:
        try:
            if os.stat(link).st_rdev == wanted:
                return link.name
        except OSError:
            continue
    return None


def _identity(root: Path) -> tuple[int, str, str | None]:
    """What is at `root` right now: its device number, the mount point it is under and the filesystem id.
    Raises OSError when the folder cannot be looked at. A pulled card whose mount folder stays behind, or
    another card mounted at the same place, gives a different answer."""
    st = os.stat(root)
    return st.st_dev, str(mount_point(root)), filesystem_uuid(root)


def _serial_and_name(root: Path, uuid: str | None = None) -> tuple[str, str]:
    """Something that stays the same for the same card on every mount. Nothing is written to the card."""
    root = Path(root).resolve()
    mount = mount_point(root)
    name = mount.name if mount == root else root.name
    name = name or "Card"
    uuid = uuid or filesystem_uuid(root)
    if uuid:
        serial = "card-" + uuid.lower()
        if mount != root:                       # a folder inside the card is not the whole card
            serial += "-" + hashlib.sha1(root.relative_to(mount).as_posix().encode()).hexdigest()[:6]
        return serial, name
    # No filesystem id (btrfs subvolumes, tmpfs, overlay, no udev): name, size and where it is mounted.
    # That tells two folders on one filesystem apart, even when both are called "roms". It cannot tell the
    # same card apart when it is mounted at another path: that counts as a different device.
    try:
        size = shutil.disk_usage(root).total
    except OSError:
        size = 0
    return "card-" + hashlib.sha1(f"{name}|{size}|{root}".encode()).hexdigest()[:12], name


class CardDevice(Device):
    """Device paths are relative to the folder: '/' is the folder itself."""
    has_apps = False

    def __init__(self, root: Path):
        # Resolved once: a relative root, `..` or a symlink in it must not confuse the containment check.
        self.root = Path(root).resolve()
        try:
            self._seen = _identity(self.root)
        except OSError:
            self._seen = None
        self.serial, self.name = _serial_and_name(self.root, self._seen[2] if self._seen else None)

    # ------------------------------------------------------------ plumbing
    def _check(self) -> None:
        """Raises DeviceGone unless what is at the path is still the card this device was made for."""
        try:
            same = self.root.is_dir() and _identity(self.root) == self._seen
        except OSError:
            same = False
        if not same:
            raise DeviceGone(f"{self.name} is not connected")

    def _p(self, path: str) -> Path:
        """The real location of a card path. Refuses anything whose real path (symlinks followed) leaves the card."""
        lexical = Path(os.path.normpath(str(self.root) + "/" + path.lstrip("/")))
        full = Path(os.path.realpath(lexical))
        if full != self.root and self.root not in full.parents:
            raise DeviceError(f"{path}: outside the card")
        return full

    def _inside(self, p: Path) -> bool:
        """Whether a path found while listing stays on the card once symlinks are followed."""
        real = Path(os.path.realpath(p))
        return real == self.root or self.root in real.parents

    def _fail(self, e: OSError, path: str = ""):
        """A card pulled out half way is DeviceGone; anything else is an error about that file."""
        self._check()
        if e.errno in (errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN):
            return DeviceGone(f"{self.name} is not connected")
        return DeviceError(f"{path}: {e.strerror or e}".strip(": "))

    @staticmethod
    def _library_error(e: OSError, local: Path) -> DeviceError:
        """A failure on the PC side (the library, often on an external disk): named as such, never blamed on the card."""
        return DeviceError(f"The library file {local}: {e.strerror or e}")

    # ------------------------------------------------------------ state
    def info(self) -> DeviceInfo:
        self._check()
        return DeviceInfo(self.serial, self.name, "", -1, "none", "card", 0)

    def storage(self, path: str) -> Storage:
        self._check()
        p = self._p(path)
        while not p.exists() and p != self.root:    # a folder not made yet is judged by the card it will be on
            p = p.parent
        try:
            u = shutil.disk_usage(p)
        except OSError as e:
            raise self._fail(e, path)
        return Storage(u.total, u.total - u.free, u.free)

    def list_dirs(self, path: str) -> list[str]:
        self._check()
        p = self._p(path)
        try:
            if not p.is_dir():
                self._check()                   # a folder that is not there is an empty list; a card that is gone is not
                return []
            return sorted(d.name for d in p.iterdir() if d.is_dir() and self._inside(d))
        except OSError as e:
            raise self._fail(e, path)

    def list_files(self, root: str, maxdepth: int | None = None) -> list[RemoteFile]:
        self._check()
        base = self._p(root)
        out: list[RemoteFile] = []
        if not base.is_dir():
            self._check()
            return out
        prefix = root.rstrip("/")

        for dirpath, dirs, names in os.walk(base, onerror=lambda e: None if e.errno == errno.EACCES
                                            else self._raise(e, root)):
            depth = len(Path(dirpath).relative_to(base).parts)
            if maxdepth is not None and depth + 1 >= maxdepth:
                dirs[:] = []
            dirs.sort()
            for n in sorted(names):
                f = Path(dirpath) / n
                if maxdepth is not None and depth + 1 > maxdepth:
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                if not f.is_file() or not self._inside(f):
                    continue
                out.append(RemoteFile(prefix + "/" + f.relative_to(base).as_posix(), st.st_size, int(st.st_mtime)))
        self._check()                           # the card was still there at the end of the walk
        return sorted(out, key=lambda f: f.path)

    def _raise(self, e: OSError, path: str):
        raise self._fail(e, path)

    def unreadable_paths(self, root: str) -> list[str]:
        return []

    def exists(self, path: str) -> bool:
        self._check()
        p = self._p(path)
        try:
            os.stat(p)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError as e:
            raise self._fail(e, path)
        return True

    def stat(self, path: str) -> RemoteFile | None:
        self._check()
        p = self._p(path)
        try:
            st = os.stat(p)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as e:
            raise self._fail(e, path)
        if not stat_mod.S_ISREG(st.st_mode):
            return None
        return RemoteFile(path, st.st_size, int(st.st_mtime))

    # ------------------------------------------------------------ files
    def read_bytes(self, path: str) -> bytes:
        self._check()
        try:
            return self._p(path).read_bytes()
        except OSError as e:
            raise self._fail(e, path)

    def _flushed(self, f) -> None:
        """Push what was written all the way to the medium before anyone trusts it."""
        f.flush()
        os.fsync(f.fileno())

    def write_bytes(self, path: str, data: bytes) -> None:
        self._check()
        p = self._p(path)
        tmp = p.with_name(p.name + ".jewelcase-tmp")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(data)
                self._flushed(f)
            os.replace(tmp, p)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise self._fail(e, path)

    def push(self, local: Path, remote: str, progress: Progress | None = None) -> None:
        self._check()
        dst = self._p(remote)
        done = 0
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise self._fail(e, remote)
        try:
            src = open(local, "rb")
        except OSError as e:
            raise self._library_error(e, local)
        with src:
            try:
                out = open(dst, "wb")
            except OSError as e:
                raise self._fail(e, remote)
            with out:
                while True:
                    try:
                        block = src.read(1 << 20)
                    except OSError as e:
                        raise self._library_error(e, local)
                    if not block:
                        break
                    try:
                        out.write(block)
                    except OSError as e:
                        raise self._fail(e, remote)
                    done += len(block)
                    if progress:
                        progress(done)
                try:
                    self._flushed(out)
                except OSError as e:
                    raise self._fail(e, remote)

    def pull(self, remote: str, local: Path, progress: Progress | None = None) -> str:
        self._check()
        src = self._p(remote)
        if not src.is_file():
            raise DeviceError(f"{remote}: no such file")
        h, done = hashlib.md5(), 0
        try:
            f = open(src, "rb")
        except OSError as e:
            raise self._fail(e, remote)
        with f:
            try:
                out = open(local, "wb")
            except OSError as e:
                raise self._library_error(e, local)
            with out:
                while True:
                    try:
                        block = f.read(1 << 20)
                    except OSError as e:
                        raise self._fail(e, remote)
                    if not block:
                        break
                    try:
                        out.write(block)
                    except OSError as e:
                        raise self._library_error(e, local)
                    h.update(block)
                    done += len(block)
                    if progress:
                        progress(done)
        return h.hexdigest()

    def md5(self, path: str) -> str:
        """Hashes what is on the medium: pages cached since the write are dropped first where the system allows."""
        self._check()
        h = hashlib.md5()
        try:
            with open(self._p(path), "rb") as f:
                try:
                    os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                except (AttributeError, OSError):
                    pass
                while True:
                    block = f.read(1 << 20)
                    if not block:
                        break
                    h.update(block)
        except OSError as e:
            raise self._fail(e, path)
        return h.hexdigest()

    def remove(self, path: str) -> None:
        self._check()
        try:
            self._p(path).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            raise self._fail(e, path)

    def rename(self, src: str, dst: str) -> None:
        self._check()
        try:
            os.replace(self._p(src), self._p(dst))
        except OSError as e:
            raise self._fail(e, src)

    def put_in_place(self, src: str, dst: str) -> None:
        self.write_bytes(dst, self.read_bytes(src))
        self.remove(src)

    def mkdir(self, path: str) -> None:
        self._check()
        try:
            self._p(path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise self._fail(e, path)

    def chmod(self, path: str, mode: str) -> None:
        """A harmless no-op: files on a card have no owner an app could be locked out by."""
        self._check()

    # ------------------------------------------------------------ apps: there are none
    # Questions ("what is running?") are answered with nothing; changes ("install this") are refused.
    def foreground_package(self) -> str:
        return ""

    def broadcast(self, action: str) -> None:
        """Harmless no-op: nobody is listening on a card."""

    def packages(self, names: Iterable[str]) -> list[PackageInfo]:
        return []

    def installed_packages(self) -> set[str]:
        return set()

    def uri_grants(self) -> dict[str, tuple[str, ...]]:
        return {}                       # no apps, so nobody was granted anything

    def apk_path(self, package: str) -> str | None:
        return None

    def stop_app(self, package: str) -> str:
        """Harmless no-op: nothing runs on a card, so nothing was in front."""
        return ""

    def start_activity(self, component: str) -> None:
        raise DeviceError(f"Starting an app is {NO_APPS}.")

    def install(self, apk: Path) -> None:
        raise DeviceError(f"Installing an app is {NO_APPS}.")

    def uninstall(self, package: str, keep_data: bool) -> None:
        raise DeviceError(f"Removing an app is {NO_APPS}.")

    def set_enabled(self, package: str, enabled: bool) -> None:
        raise DeviceError(f"Switching an app on or off is {NO_APPS}.")
