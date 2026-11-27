"""Real devices, reached through the adb command-line tool."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from shlex import quote as q
from typing import Iterable
from urllib.parse import unquote

from .device import (Device, DeviceError, DeviceGone, DeviceInfo, PackageInfo, Progress,
                     RemoteFile, Storage, md5_file)

_GONE = re.compile(
    r"^(adb: |error: )*(device '.*' not found|device offline|device unauthorized|"
    r"device still authorizing|no devices/emulators found|closed|protocol fault|"
    r"connection reset|failed to get feature set|device not found|cannot connect)", re.M | re.I)


@dataclass(frozen=True)
class AdbTarget:
    serial: str
    state: str        # 'device', 'unauthorized', 'offline', 'no permissions', ...
    model: str
    transport: str    # 'usb' | 'wifi'


def find_adb(configured: str | None, data_dir: Path | None = None) -> str | None:
    if configured and os.access(configured, os.X_OK):
        return configured
    found = shutil.which("adb")
    if found:
        return found
    if data_dir:
        bundled = Path(data_dir) / "platform-tools" / "adb"
        if os.access(bundled, os.X_OK):
            return str(bundled)
    return None


PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"


def _fetch(url: str):
    from . import net
    return net.open_url(url, timeout=60)


def download_platform_tools(data_dir: Path, fetch=_fetch) -> str:
    """Fetch Google's platform-tools into the app's data folder and return the adb inside it."""
    import io
    import zipfile
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with fetch(PLATFORM_TOOLS_URL) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        for member in z.namelist():
            if not member.startswith("platform-tools/") or ".." in member:
                raise OSError("unexpected contents in the platform-tools download")
        z.extractall(data_dir)
    exe = data_dir / "platform-tools" / "adb"
    for tool in (data_dir / "platform-tools").iterdir():
        if tool.is_file() and "." not in tool.name:
            tool.chmod(0o755)
    return str(exe)


def parse_targets(text: str) -> list[AdbTarget]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue                    # not a device line
        serial, rest = parts
        state = "no permissions" if rest.startswith("no permissions") else rest.split(None, 1)[0] if rest else ""
        m = re.search(r"\bmodel:(\S+)", rest)
        wifi = "._adb-tls-connect._tcp" in serial or ":" in serial
        out.append(AdbTarget(serial, state, m.group(1).replace("_", " ") if m else "", "wifi" if wifi else "usb"))
    return out


def pick_targets(targets: list[AdbTarget]) -> list[AdbTarget]:
    """One entry per physical device, USB preferred over its own Wi-Fi twin."""
    usb = [t for t in targets if t.transport == "usb"]
    out = list(usb)
    for t in targets:
        if t.transport == "wifi" and not any(t.serial.startswith(f"adb-{u.serial}-") for u in usb):
            out.append(t)
    return out


def list_targets(adb: str, run=subprocess.run) -> list[AdbTarget]:
    try:
        cp = run([adb, "devices", "-l"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_targets((cp.stdout or b"").decode("utf-8", "replace"))


def parse_battery(text: str) -> tuple[int, str]:
    def flag(name: str) -> bool:
        return bool(re.search(rf"{name} powered:\s*true", text))
    m = re.search(r"^\s*level:\s*(\d+)", text, re.M)
    level = int(m.group(1)) if m else -1
    if flag("AC") or flag("Wireless") or flag("Dock"):
        return level, "ac"
    return level, "usb" if flag("USB") else "none"


def parse_df(text: str) -> Storage:
    for line in reversed(text.strip().splitlines()):
        nums = re.findall(r"\s(\d+)\s+(\d+)\s+(\d+)\s+\d+%", " " + line)
        if nums:
            total, used, free = (int(x) * 1024 for x in nums[0])
            return Storage(total, used, free)
    raise DeviceError("could not read free space: " + text.strip()[:120])


def parse_stat_lines(text: str) -> list[RemoteFile]:
    out = []
    for line in text.splitlines():
        m = re.match(r"(\d+) (\d+) (/.*)$", line)
        if m:
            out.append(RemoteFile(m.group(3), int(m.group(1)), int(m.group(2))))
    return out


_TOP_ACTIVITY = ("dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity| ResumedActivity:|"
                 "mFocusedApp'")


def parse_top_activity(text: str) -> str:
    """The app in front. Asleep, Android drops topResumedActivity but still names the resumed/focused app."""
    for marker in ("topResumedActivity", "mResumedActivity", " ResumedActivity:", "mFocusedApp"):
        m = re.search(re.escape(marker) + r"\S*.*? u\d+ ([\w.]+)/", text)
        if m:
            return m.group(1)
    return ""


def parse_package_dump(package: str, text: str) -> PackageInfo | None:
    if "Unable to find package" in text:
        return None
    version = re.search(r"versionName=(\S+)", text)
    if not version:
        return None
    flags = re.search(r"flags=\[(.*?)\]", text)
    words = flags.group(1).split() if flags else []
    enabled = re.search(r"\benabled=(\d)", text)
    return PackageInfo(package, version.group(1), "SYSTEM" in words, "UPDATED_SYSTEM_APP" in words,
                       (enabled.group(1) if enabled else "0") in ("0", "1"))


_TREE = re.compile(r"content://com\.android\.externalstorage\.documents/tree/([^\s/\]}]+)")


def parse_uri_grants(text: str) -> dict[str, tuple[str, ...]]:
    """`dumpsys activity permissions`: app -> the folders (device paths) it was given through Android's folder
    picker and keeps. A tree id is the URL-encoded "<volume>:<path>"; "primary" is the internal storage."""
    found: dict[str, set[str]] = {}
    for block in text.split("UriPermission{")[1:]:
        tree, pkg, kept = _TREE.search(block), re.search(r"targetPkg=([\w.]+)", block), re.search(r"persisted=0x(\w+)", block)
        if not tree or not pkg or (kept and int(kept.group(1), 16) == 0):
            continue
        volume, _, path = unquote(tree.group(1)).partition(":")
        base = "/storage/emulated/0" if volume == "primary" else "/storage/" + volume
        found.setdefault(pkg.group(1), set()).add((base + "/" + path.strip("/")).rstrip("/"))
    return {pkg: tuple(sorted(paths)) for pkg, paths in found.items()}


def _text(b: bytes | None) -> str:
    return (b or b"").decode("utf-8", "surrogateescape")


class AdbDevice(Device):
    def __init__(self, adb: str, target: AdbTarget, run=subprocess.run, popen=subprocess.Popen):
        self.adb, self.target, self.serial = adb, target, target.serial
        self._run, self._popen = run, popen

    # ------------------------------------------------------------ plumbing
    def _fail(self, rc: int, out: bytes | None, err: bytes | None):
        msg = (_text(err).strip() or _text(out).strip())[-400:] or f"adb exited with {rc}"
        if _GONE.search(msg):
            raise DeviceGone(msg)
        raise DeviceError(msg)

    def _alive(self) -> bool:
        """Ask adb whether the device is still there. adb's own errors do not always say."""
        try:
            cp = self._run([self.adb, "-s", self.target.serial, "get-state"], capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return cp.returncode == 0 and _text(cp.stdout).strip() == "device"

    def _adb(self, args: list[str], input: bytes | None = None, timeout: float = 120, check: bool = True):
        try:
            cp = self._run([self.adb, "-s", self.target.serial, *args], input=input,
                           capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DeviceGone(f"no answer from the device within {int(timeout)} s")
        except OSError as e:
            raise DeviceGone(f"could not run adb: {e}")
        if cp.returncode != 0:
            msg = _text(cp.stderr) + _text(cp.stdout)[-400:]
            if _GONE.search(msg) or not self._alive():
                raise DeviceGone(msg.strip() or "the device stopped answering")
            if check:
                self._fail(cp.returncode, cp.stdout, cp.stderr)
        return cp

    def use_hardware_serial(self) -> None:
        """Identify the device by its own serial, so USB and Wi-Fi connections are the same device."""
        hw = self._sh("getprop ro.serialno", timeout=20, check=False).strip()
        if hw and " " not in hw:
            self.serial = hw

    def _sh(self, cmd: str, timeout: float = 120, check: bool = True) -> str:
        return _text(self._adb(["shell", cmd], timeout=timeout, check=check).stdout)

    # ------------------------------------------------------------ state
    def info(self) -> DeviceInfo:
        out = self._sh("getprop ro.product.model; echo ----; getprop ro.build.version.release; "
                       "echo ----; dumpsys battery; echo ----; getprop ro.build.version.sdk")
        parts = out.split("----")
        level, power = parse_battery(parts[2] if len(parts) > 2 else "")
        sdk = parts[3].strip() if len(parts) > 3 else ""
        return DeviceInfo(self.serial, parts[0].strip() or self.target.model,
                          parts[1].strip() if len(parts) > 1 else "", level, power, self.target.transport,
                          int(sdk) if sdk.isdigit() else 0)

    def storage(self, path: str) -> Storage:
        return parse_df(self._sh("df -k " + q(path)))

    def list_dirs(self, path: str) -> list[str]:
        out = self._sh(f"find {q(path)} -mindepth 1 -maxdepth 1 -type d", check=False)
        return sorted(line.rsplit("/", 1)[-1] for line in out.splitlines() if line.startswith("/"))

    def list_tree_dirs(self, root: str, maxdepth: int) -> list[str]:
        out = self._sh(f"find {q(root)} -mindepth 1 -maxdepth {int(maxdepth)} -type d", check=False)
        return sorted(line for line in out.splitlines() if line.startswith("/"))

    def list_files(self, root: str, maxdepth: int | None = None) -> list[RemoteFile]:
        depth = f"-maxdepth {int(maxdepth)} " if maxdepth is not None else ""
        out = self._sh(f"find {q(root)} {depth}-type f -exec stat -c '%s %Y %n' {{}} +",
                       timeout=300, check=False)
        return parse_stat_lines(out)

    def unreadable_paths(self, root: str) -> list[str]:
        # Android lets the shell list an app's folder and still refuses some folders and files in it
        # (they belong to the app's own group). find does not say so, so each path is tested.
        out = self._sh(f"find {q(root)} 2>/dev/null | while IFS= read -r p; do "
                       f'if [ -d "$p" ]; then [ -r "$p" ] && [ -x "$p" ] || echo "$p"; '
                       f'else [ -r "$p" ] || echo "$p"; fi; done', timeout=300, check=False)
        return sorted(line for line in out.splitlines() if line.startswith("/"))

    def exists(self, path: str) -> bool:
        return self._sh(f"[ -e {q(path)} ] && echo Y || echo N").strip() == "Y"

    def stat(self, path: str) -> RemoteFile | None:
        found = parse_stat_lines(self._sh(f"[ -f {q(path)} ] && stat -c '%s %Y %n' {q(path)}", check=False))
        return found[0] if found else None

    def foreground_package(self) -> str:
        return parse_top_activity(self._sh(_TOP_ACTIVITY, check=False))

    # ------------------------------------------------------------ files
    def read_bytes(self, path: str) -> bytes:
        return self._adb(["shell", "cat " + q(path)], timeout=300).stdout or b""

    def write_bytes(self, path: str, data: bytes) -> None:
        """Write beside the target, check it, then swap it in: an interrupted write changes nothing."""
        import hashlib
        tmp = path + ".jewelcase-tmp"
        self._adb(["shell", "cat > " + q(tmp)], input=data, timeout=300)
        if self.md5(tmp) != hashlib.md5(data).hexdigest():
            self._sh("rm -f " + q(tmp), check=False)
            raise DeviceError(f"{path} did not read back the same after writing; it was left as it was")
        self._sh(f"mv -f {q(tmp)} {q(path)}")

    def md5(self, path: str) -> str:
        out = self._sh("md5sum " + q(path), timeout=3600)
        token = out.split()[0] if out.split() else ""
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise DeviceError(f"unexpected md5sum answer for {path}: {out.strip()[:120]}")
        return token

    def remove(self, path: str) -> None:
        self._sh("rm -f " + q(path))

    def rename(self, src: str, dst: str) -> None:
        self._sh(f"mv -f {q(src)} {q(dst)}")

    def put_in_place(self, src: str, dst: str) -> None:
        """Give dst the contents of src, which is then gone.

        A file adb pushes into an app's own folder arrives owned by adb and read-only for everyone else,
        so the app could not write to it. A file that exists is therefore overwritten where it is and
        keeps its owner and permissions; a new one is made writable (the folder above still limits who
        gets to it)."""
        self._sh(f"if [ -e {q(dst)} ]; then cat {q(src)} > {q(dst)} && rm -f {q(src)}; "
                 f"else chmod a+rw {q(src)}; mv -f {q(src)} {q(dst)}; fi", timeout=3600)

    def mkdir(self, path: str) -> None:
        self._sh("mkdir -p " + q(path))

    def chmod(self, path: str, mode: str) -> None:
        self._sh(f"chmod {q(mode)} {q(path)}", check=False)

    def _remote_size(self, path: str) -> int:
        out = self._sh("stat -c %s " + q(path), timeout=20, check=False).strip()
        return int(out) if out.isdigit() else 0

    def _transfer(self, args: list[str], progress: Progress | None, measure, final: int | None) -> None:
        proc = self._popen([self.adb, "-s", self.target.serial, *args],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            while True:
                try:
                    proc.wait(timeout=1.0)
                    break
                except subprocess.TimeoutExpired:
                    if progress:
                        try:
                            done = measure()
                        except (DeviceGone, DeviceError, OSError):
                            continue
                        progress(done)      # may raise to cancel the transfer
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode != 0:
            err = proc.stderr.read() if proc.stderr else b""
            if not self._alive():
                raise DeviceGone(_text(err).strip() or "the device stopped answering")
            self._fail(proc.returncode, b"", err)
        if progress and final is not None:
            progress(final)

    def push(self, local: Path, remote: str, progress: Progress | None = None) -> None:
        self._transfer(["push", str(local), remote], progress,
                       lambda: self._remote_size(remote), Path(local).stat().st_size)

    def pull(self, remote: str, local: Path, progress: Progress | None = None) -> str:
        local = Path(local)
        self._transfer(["pull", remote, str(local)], progress,
                       lambda: local.stat().st_size if local.exists() else 0, None)
        return md5_file(local)

    # ------------------------------------------------------------ apps
    def broadcast(self, action: str) -> None:
        self._sh("am broadcast -a " + q(action), check=False)

    def packages(self, names: Iterable[str]) -> list[PackageInfo]:
        names = [n for n in names if re.fullmatch(r"[\w.]+", n)]
        if not names:
            return []
        script = "for p in " + " ".join(names) + "; do echo \"@@$p\"; dumpsys package $p | " \
                 "grep -E 'Unable to find package|versionName=|flags=\\[|enabled=[0-9]'; done"
        out = self._sh(script, timeout=180, check=False)
        found = []
        for chunk in out.split("@@")[1:]:
            name, _, body = chunk.partition("\n")
            info = parse_package_dump(name.strip(), body)
            if info:
                found.append(info)
        return found

    def installed_packages(self) -> set[str]:
        out = self._sh("pm list packages -e", timeout=60, check=False)
        return {line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("package:")}

    def uri_grants(self) -> dict[str, tuple[str, ...]]:
        try:
            return parse_uri_grants(self._sh("dumpsys activity permissions", timeout=60))
        except DeviceError:
            return {}               # not readable: make no claim

    def apk_path(self, package: str) -> str | None:
        if not re.fullmatch(r"[\w.]+", package):
            return None
        out = self._sh("pm path " + package, check=False)
        return next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("package:")), None)

    def stop_app(self, package: str) -> str:
        top = self._sh(_TOP_ACTIVITY, check=False)
        front = re.search(r"(?<![\w.])(" + re.escape(package) + r"/[\w.$]+)", top) \
            if parse_top_activity(top) == package else None        # the same answer foreground_package gives
        self._sh("am force-stop " + q(package))
        return front.group(1) if front else ""

    def start_activity(self, component: str) -> None:
        self._sh("am start -n " + q(component), check=False)

    def install(self, apk: Path) -> None:
        self._adb(["install", "-r", str(apk)], timeout=900)

    def uninstall(self, package: str, keep_data: bool) -> None:
        self._adb(["uninstall", *(["-k"] if keep_data else []), package], timeout=180)

    def set_enabled(self, package: str, enabled: bool) -> None:
        self._sh(("pm enable " if enabled else "pm disable-user --user 0 ") + q(package))
