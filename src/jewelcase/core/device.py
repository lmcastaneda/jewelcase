"""The interface every device backend implements, and the values it returns."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

Progress = Callable[[int], None]


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    mtime: int


@dataclass(frozen=True)
class Storage:
    total: int
    used: int
    free: int


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    model: str
    android: str
    battery: int
    power: str        # 'ac' | 'usb' | 'none'
    transport: str    # 'usb' | 'wifi'
    sdk: int = 0      # Android API level, 0 when unknown


@dataclass(frozen=True)
class PackageInfo:
    package: str
    version: str
    system: bool
    updated_system: bool
    enabled: bool


class DeviceGone(Exception):
    """The device cannot be reached right now (unplugged, offline, asleep)."""


class DeviceError(Exception):
    """The device answered, and the answer was a failure."""


def md5_file(path: Path, progress: Progress | None = None, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    done = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
            done += len(block)
            if progress:
                progress(done)
    return h.hexdigest()


class Device(ABC):
    serial: str
    has_apps = True         # False for a card or folder: no packages, no foreground app, nothing to restart

    @abstractmethod
    def info(self) -> DeviceInfo: ...
    @abstractmethod
    def storage(self, path: str) -> Storage: ...
    @abstractmethod
    def list_dirs(self, path: str) -> list[str]: ...
    def list_tree_dirs(self, root: str, maxdepth: int) -> list[str]:
        """Every folder up to `maxdepth` levels below `root`, as paths. Devices that can answer in one
        request override this; the default asks level by level."""
        found, queue = [], [(root.rstrip("/"), 1)]
        while queue:
            folder, level = queue.pop(0)
            for name in self.list_dirs(folder):
                found.append(f"{folder}/{name}")
                if level < maxdepth:
                    queue.append((f"{folder}/{name}", level + 1))
        return found

    @abstractmethod
    def list_files(self, root: str, maxdepth: int | None = None) -> list[RemoteFile]: ...
    @abstractmethod
    def unreadable_paths(self, root: str) -> list[str]: ...   # folders it may not enter, files it may not open
    @abstractmethod
    def exists(self, path: str) -> bool: ...
    @abstractmethod
    def stat(self, path: str) -> RemoteFile | None: ...
    @abstractmethod
    def read_bytes(self, path: str) -> bytes: ...
    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None: ...
    @abstractmethod
    def push(self, local: Path, remote: str, progress: Progress | None = None) -> None: ...
    @abstractmethod
    def pull(self, remote: str, local: Path, progress: Progress | None = None) -> str: ...
    @abstractmethod
    def md5(self, path: str) -> str: ...
    @abstractmethod
    def remove(self, path: str) -> None: ...
    @abstractmethod
    def rename(self, src: str, dst: str) -> None: ...
    @abstractmethod
    def put_in_place(self, src: str, dst: str) -> None: ...   # dst gets src's contents and stays writable for its app
    @abstractmethod
    def mkdir(self, path: str) -> None: ...
    @abstractmethod
    def chmod(self, path: str, mode: str) -> None: ...
    @abstractmethod
    def foreground_package(self) -> str: ...
    @abstractmethod
    def broadcast(self, action: str) -> None: ...
    @abstractmethod
    def packages(self, names: Iterable[str]) -> list[PackageInfo]: ...
    @abstractmethod
    def installed_packages(self) -> set[str]: ...       # every app that is installed and enabled
    @abstractmethod
    def uri_grants(self) -> dict[str, tuple[str, ...]]: ...     # app -> folders it holds a kept grant for; {} if unknown
    @abstractmethod
    def apk_path(self, package: str) -> str | None: ...     # where the app's APK is on the device
    @abstractmethod
    def stop_app(self, package: str) -> str: ...        # force-stop; its activity if it was in front, else ""
    @abstractmethod
    def start_activity(self, component: str) -> None: ...
    @abstractmethod
    def install(self, apk: Path) -> None: ...
    @abstractmethod
    def uninstall(self, package: str, keep_data: bool) -> None: ...
    @abstractmethod
    def set_enabled(self, package: str, enabled: bool) -> None: ...
