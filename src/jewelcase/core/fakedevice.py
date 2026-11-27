"""A device backed by a local directory. Used by the tests and by demo mode."""
from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .device import (Device, DeviceError, DeviceGone, DeviceInfo, PackageInfo, Progress,
                     RemoteFile, Storage, md5_file)


class FakeDevice(Device):
    def __init__(self, root: Path, serial: str = "FAKE0001"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.serial = serial
        self.model = "Fake Handheld"
        self.battery = 80
        self.power = "usb"
        self.foreground = ""
        self.online = True
        self.storage_total = 64 * 1024 ** 3
        self.fail_after_bytes: int | None = None     # next push dies after N bytes (one-shot)
        self.corrupt_pushes = 0                      # next N pushes land with wrong bytes
        self.push_error: str | None = None           # next push fails with this message, still connected
        self.fail_next_write = False                 # next write_bytes dies half way, device goes away
        self.broadcasts: list[str] = []
        self.fake_packages: dict[str, PackageInfo] = {}
        self.installed: list[Path] = []
        self.install_error: str | None = None
        self.calls: list[tuple] = []                 # (method, path) for tests that care about order
        self.unreadable: set[str] = set()            # folders that refuse to be entered, as Android does
        self.unreadable_files: set[str] = set()      # files that can be listed but not opened
        self.mutate_on_pull = 0                      # next N pulls deliver other bytes than md5() saw
        self.drop_on_pull = 0                        # the Nth pull from now loses the device half way
        # Android: a file adb pushes into an app's folder belongs to adb, and the app cannot write to it
        # until it is made writable for everyone.
        self.shell_made: set[str] = set()
        self.spoil_in_place = 0                      # next N put_in_place calls leave damaged contents
        self.fake_apks: dict[str, str] = {}          # package -> where its APK is on the device
        self.apk_packages: dict[str, PackageInfo] = {}   # APK file name -> the package installing it brings
        self.fake_grants: dict[str, list[str]] = {}  # package -> folders it was given through the folder picker
        self.before_stop = None                      # called once, just before the next app is stopped

    def _p(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def _check(self, what: str = "", path: str = "") -> None:
        if not self.online:
            raise DeviceGone("fake device is offline")
        if what:
            self.calls.append((what, path))

    def app_can_use(self, path: str) -> bool:
        """Whether the app that owns the folder could read and write the file."""
        return path not in self.shell_made

    def _denied(self, path: str) -> bool:
        return path in self.unreadable_files or \
            any(path == u or path.startswith(u.rstrip("/") + "/") for u in self.unreadable)

    def _readable(self, path: str) -> None:
        if self._denied(path):
            raise DeviceError(f"{path}: Permission denied")

    def unreadable_paths(self, root: str) -> list[str]:
        self._check()
        prefix = root.rstrip("/") + "/"
        return sorted([u for u in self.unreadable if (u == root or u.startswith(prefix)) and self._p(u).is_dir()]
                      + [u for u in self.unreadable_files if u.startswith(prefix) and self._p(u).is_file()])

    # ------------------------------------------------------------ state
    def info(self) -> DeviceInfo:
        self._check()
        return DeviceInfo(self.serial, self.model, "14", self.battery, self.power, "usb", 34)

    def storage(self, path: str) -> Storage:
        self._check()
        used = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        return Storage(self.storage_total, used, max(0, self.storage_total - used))

    def list_dirs(self, path: str) -> list[str]:
        self._check()
        p = self._p(path)
        return sorted(d.name for d in p.iterdir() if d.is_dir()) if p.is_dir() else []

    def list_tree_dirs(self, root: str, maxdepth: int) -> list[str]:
        self._check("tree", root)
        base = self._p(root)
        if not base.is_dir():
            return []
        return sorted(root.rstrip("/") + "/" + d.relative_to(base).as_posix()
                      for d in base.rglob("*") if d.is_dir() and len(d.relative_to(base).parts) <= maxdepth)

    def list_files(self, root: str, maxdepth: int | None = None) -> list[RemoteFile]:
        self._check()
        base = self._p(root)
        out = []
        if not base.is_dir():
            return out
        for f in sorted(base.rglob("*")):
            if f.is_file():
                rel = f.relative_to(base)
                if maxdepth is not None and len(rel.parts) > maxdepth:
                    continue
                path = root.rstrip("/") + "/" + rel.as_posix()
                if self.unreadable and path not in self.unreadable_files and self._denied(path):
                    continue                # inside a folder that cannot be entered: not even listed
                st = f.stat()
                out.append(RemoteFile(root.rstrip("/") + "/" + rel.as_posix(), st.st_size, int(st.st_mtime)))
        return out

    def exists(self, path: str) -> bool:
        self._check()
        return self._p(path).exists()

    def stat(self, path: str) -> RemoteFile | None:
        self._check()
        p = self._p(path)
        if not p.is_file():
            return None
        st = p.stat()
        return RemoteFile(path, st.st_size, int(st.st_mtime))

    def foreground_package(self) -> str:
        self._check()
        return self.foreground

    # ------------------------------------------------------------ files
    def read_bytes(self, path: str) -> bytes:
        self._check("read", path)
        self._readable(path)
        try:
            return self._p(path).read_bytes()
        except OSError as e:
            raise DeviceError(str(e))

    def write_bytes(self, path: str, data: bytes) -> None:
        self._check("write", path)
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".jewelcase-tmp")
        if self.fail_next_write:
            self.fail_next_write = False
            tmp.write_bytes(data[: len(data) // 2])
            self.online = False
            raise DeviceGone("fake device unplugged during a write")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def push(self, local: Path, remote: str, progress: Progress | None = None) -> None:
        self._check("push", remote)
        dst = self._p(remote)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            self.shell_made.add(remote)
        done = 0
        limit, self.fail_after_bytes = self.fail_after_bytes, None
        if self.push_error:
            message, self.push_error = self.push_error, None
            dst.write_bytes(Path(local).read_bytes()[:10])
            raise DeviceError(message)
        with open(local, "rb") as src, open(dst, "wb") as out:
            while True:
                block = src.read(64 * 1024)
                if not block:
                    break
                if limit is not None and done + len(block) > limit:
                    out.write(block[: max(0, limit - done)])
                    out.flush()
                    self.online = False
                    raise DeviceGone("fake device unplugged during push")
                out.write(block)
                done += len(block)
                if progress:
                    progress(done)
        if self.corrupt_pushes > 0:
            self.corrupt_pushes -= 1
            with open(dst, "r+b") as f:
                first = f.read(1) or b"\0"
                f.seek(0)
                f.write(bytes([first[0] ^ 0xFF]))

    def pull(self, remote: str, local: Path, progress: Progress | None = None) -> str:
        self._check("pull", remote)
        self._readable(remote)
        src = self._p(remote)
        if not src.is_file():
            raise DeviceError(f"{remote}: no such file")
        if self.drop_on_pull > 0:
            self.drop_on_pull -= 1
            if self.drop_on_pull == 0:
                Path(local).write_bytes(src.read_bytes()[:1])
                self.online = False
                raise DeviceGone("fake device unplugged during a pull")
        shutil.copyfile(src, local)
        if self.mutate_on_pull > 0:              # the emulator wrote the file while it was being copied
            self.mutate_on_pull -= 1
            with open(local, "ab") as f:
                f.write(b"!")
        if progress:
            progress(src.stat().st_size)
        return md5_file(Path(local))

    def md5(self, path: str) -> str:
        self._check("md5", path)
        self._readable(path)
        try:
            return md5_file(self._p(path))
        except OSError as e:
            raise DeviceError(str(e))

    def remove(self, path: str) -> None:
        self._check("remove", path)
        self.shell_made.discard(path)
        try:
            self._p(path).unlink()
        except FileNotFoundError:
            pass

    def rename(self, src: str, dst: str) -> None:
        self._check("rename", dst)
        os.replace(self._p(src), self._p(dst))
        made = src in self.shell_made               # the file that was at dst is gone; src's owner comes along
        self.shell_made -= {src, dst}
        if made:
            self.shell_made.add(dst)

    def put_in_place(self, src: str, dst: str) -> None:
        self._check("place", dst)
        s, d = self._p(src), self._p(dst)
        data = s.read_bytes()
        if self.spoil_in_place > 0:
            self.spoil_in_place -= 1
            data = data[: len(data) // 2] + b"?"
        if d.exists():                              # written where it is: the file keeps its owner
            d.write_bytes(data)
            s.unlink()
        else:                                       # a new file, made writable for the app
            s.write_bytes(data)
            os.replace(s, d)
            self.shell_made.discard(dst)
        self.shell_made.discard(src)

    def mkdir(self, path: str) -> None:
        self._check()
        self._p(path).mkdir(parents=True, exist_ok=True)

    def chmod(self, path: str, mode: str) -> None:
        self._check("chmod", path)

    # ------------------------------------------------------------ apps
    def broadcast(self, action: str) -> None:
        self._check()
        self.broadcasts.append(action)

    def packages(self, names: Iterable[str]) -> list[PackageInfo]:
        self._check()
        return [self.fake_packages[n] for n in names if n in self.fake_packages]

    def installed_packages(self) -> set[str]:
        self._check()
        return {p for p, info in self.fake_packages.items() if info.enabled}

    def uri_grants(self) -> dict[str, tuple[str, ...]]:
        self._check()
        return {p: tuple(v) for p, v in self.fake_grants.items()}

    def apk_path(self, package: str) -> str | None:
        self._check()
        return self.fake_apks.get(package)

    def stop_app(self, package: str) -> str:
        self._check("stop", package)
        hook, self.before_stop = self.before_stop, None
        if hook:
            hook()
        if self.foreground != package:
            return ""
        self.foreground = ""
        return package + "/.MainActivity"

    def start_activity(self, component: str) -> None:
        self._check("start", component)
        self.foreground = component.split("/")[0]

    def install(self, apk: Path) -> None:
        self._check()
        if self.install_error:
            raise DeviceError(f"adb: failed to install {Path(apk).name}: Failure [{self.install_error}]")
        self.installed.append(apk)
        brings = self.apk_packages.get(Path(apk).name)
        if brings:
            self.fake_packages[brings.package] = brings

    def uninstall(self, package: str, keep_data: bool) -> None:
        self._check()
        info = self.fake_packages.get(package)
        if info is None:
            raise DeviceError(f"{package} is not installed")
        if info.system and not info.updated_system:
            raise DeviceError(f"{package} is a system app and cannot be uninstalled")
        if info.updated_system:
            self.fake_packages[package] = replace(info, updated_system=False, version="factory")
        else:
            del self.fake_packages[package]

    def set_enabled(self, package: str, enabled: bool) -> None:
        self._check()
        self.fake_packages[package] = replace(self.fake_packages[package], enabled=enabled)
