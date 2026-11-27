"""Emulators on the device: what is installed, what could be updated, and changing that safely."""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import net
from .device import Device, DeviceError, PackageInfo, Progress

BUILTIN = Path(__file__).resolve().parent.parent / "data" / "emulators.toml"
SIGNATURE_MESSAGE = ("Signed by a different publisher than the installed copy. Jewelcase will not uninstall it "
                     "for you, because that deletes its saves.")


class EmulatorError(Exception):
    """Something about an emulator that could not be done, in words for the user."""


@dataclass(frozen=True)
class Source:
    kind: str           # "github"
    repo: str
    asset: str          # regular expression for the APK's file name
    pick: str = ""      # "sdk": choose the build for the device's Android level


@dataclass(frozen=True)
class CatalogEntry:
    package: str
    name: str
    systems: tuple[str, ...]
    source: Source | None = None
    homepage: str = ""
    kind: str = "emulator"      # or "frontend"
    esde: str = ""              # the ES-DE command that suits what Jewelcase syncs, when ES-DE has several


@dataclass(frozen=True)
class EmuRow:
    entry: CatalogEntry
    installed: PackageInfo | None
    latest: str | None = None
    apk_url: str | None = None
    error: str = ""

    @property
    def update_available(self) -> bool:
        return bool(self.installed and self.latest and self.apk_url
                    and is_newer(self.installed.version, self.latest))


# ---------------------------------------------------------------- catalogue
def _read(path: Path) -> list[CatalogEntry]:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for e in data.get("emulator", []):
        if not e.get("package"):
            continue
        src = e.get("source")
        source = Source(src.get("kind", "github"), src["repo"], src["asset"], src.get("pick", "")) \
            if isinstance(src, dict) and src.get("repo") and src.get("asset") else None
        out.append(CatalogEntry(e["package"], e.get("name") or e["package"], tuple(e.get("systems", ())),
                                source, e.get("homepage", ""), e.get("kind", "emulator"), e.get("esde", "")))
    return out


def load_catalog(extra: Path | None = None) -> dict[str, CatalogEntry]:
    catalog = {e.package: e for e in _read(BUILTIN)}
    if extra:
        catalog.update({e.package: e for e in _read(extra)})
    return catalog


def emulator_packages(catalog: dict[str, CatalogEntry]) -> set[str]:
    """Packages that mean "a game is running" when they are in the foreground."""
    return {p for p, e in catalog.items() if e.kind == "emulator"}


def list_emulators(dev: Device, catalog: dict[str, CatalogEntry]) -> list[EmuRow]:
    found = {p.package: p for p in dev.packages(sorted(catalog))}
    installed = [EmuRow(catalog[p], info) for p, info in found.items()]
    installed.sort(key=lambda r: r.entry.name.lower())
    addable = [EmuRow(e, None) for p, e in catalog.items() if p not in found and e.source]
    addable.sort(key=lambda r: r.entry.name.lower())
    return installed + addable


# ---------------------------------------------------------------- updates
request = net.request


def _fetch_json(url: str) -> dict:
    with net.open_url(url, timeout=30) as r:
        return json.load(r)


def _open(url: str):
    return net.open_url(url, timeout=60)


def _numbers(version: str) -> tuple[int, ...]:
    m = re.search(r"\d+(?:\.\d+)*", version or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else ()


def is_newer(installed: str, latest: str) -> bool:
    a, b = _numbers(installed), _numbers(latest)
    if not a or not b:
        return False
    parts = [tuple(int(x) for x in g.split(".")) for g in re.findall(r"\d+(?:\.\d+)*", installed)]
    if len(parts) > 1:
        # "0.2.0-12" against a release tagged "12": the tag is the build number at the end
        return b > parts[-1] if len(b) == len(parts[-1]) and len(b) != len(a) else b > a
    return b > a


def _choose_asset(source: Source, assets: list[dict], sdk: int | None) -> tuple[str | None, str]:
    pattern = re.compile(source.asset)
    matches = [(a, pattern.search(a.get("name", ""))) for a in assets]
    matches = [(a, m) for a, m in matches if m]
    if not matches:
        return None, "the latest release has no Android build"
    if source.pick == "sdk":
        usable = [(int(m.group(1)), a) for a, m in matches if m.groups() and (not sdk or int(m.group(1)) <= sdk)]
        if not usable:
            return None, "the latest release has no build for this Android version"
        return max(usable, key=lambda x: x[0])[1].get("browser_download_url"), ""
    return matches[0][0].get("browser_download_url"), ""


def check_updates(rows: list[EmuRow], sdk: int | None = None, fetch_json=_fetch_json) -> list[EmuRow]:
    """Ask each project's release feed for its latest build. A failure stays with its own row."""
    out = []
    for row in rows:
        src = row.entry.source
        if not src or src.kind != "github":
            out.append(row)
            continue
        try:
            data = fetch_json(f"https://api.github.com/repos/{src.repo}/releases/latest")
            tag = str(data.get("tag_name") or "").lstrip("vV")
            url, problem = _choose_asset(src, data.get("assets") or [], sdk)
            out.append(replace(row, latest=tag or None, apk_url=url, error=problem))
        except (OSError, ValueError, KeyError) as e:
            out.append(replace(row, latest=None, apk_url=None, error=f"could not check: {e}"))
    return out


# ---------------------------------------------------------------- changing things
def _install_error(e: DeviceError) -> EmulatorError:
    text = str(e)
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in text:
        return EmulatorError(SIGNATURE_MESSAGE)
    if "INSTALL_FAILED_VERSION_DOWNGRADE" in text:
        return EmulatorError("The copy on the device is newer than this file, so nothing was changed.")
    if "INSTALL_FAILED_INSUFFICIENT_STORAGE" in text:
        return EmulatorError("There is not enough free space on the device to install this.")
    if "INSTALL_FAILED_OLDER_SDK" in text or "INSTALL_FAILED_NO_MATCHING_ABIS" in text:
        return EmulatorError("This build is not made for this device.")
    m = re.search(r"\[(INSTALL_[A-Z_]+)", text)
    return EmulatorError(f"The device refused the install ({m.group(1)})." if m else text[-300:])


def install_from_file(dev: Device, apk: Path) -> None:
    try:
        dev.install(Path(apk))
    except DeviceError as e:
        raise _install_error(e) from None


def install_latest(dev: Device, row: EmuRow, cache: Path, progress: Progress | None = None, fetch=_open) -> Path:
    if not row.apk_url:
        raise EmulatorError("Check for updates first, so Jewelcase knows which build to fetch.")
    folder = Path(cache) / "apk"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.\-]", "_", row.apk_url.rsplit("/", 1)[-1]) or f"{row.entry.package}.apk"
    dest = folder / name
    part = dest.with_name(name + ".part")
    try:
        done = 0
        with fetch(row.apk_url) as response, open(part, "wb") as out:
            while True:
                block = response.read(1 << 18)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress:
                    progress(done)
        part.replace(dest)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise EmulatorError(f"The download did not finish: {e}") from None
    install_from_file(dev, dest)
    return dest


def remove(dev: Device, row: EmuRow, keep_data: bool) -> None:
    info = row.installed
    if info is None:
        raise EmulatorError(f"{row.entry.name} is not installed.")
    if info.system:
        raise EmulatorError(f"{row.entry.name} came with the device and cannot be removed. Disable it instead"
                            + (", or go back to the factory version." if info.updated_system else "."))
    try:
        dev.uninstall(info.package, keep_data)
    except DeviceError as e:
        raise EmulatorError(str(e)[-300:]) from None


def revert_to_factory(dev: Device, row: EmuRow) -> None:
    info = row.installed
    if info is None or not info.updated_system:
        raise EmulatorError(f"{row.entry.name} has no factory version to go back to.")
    try:
        dev.uninstall(info.package, keep_data=True)
    except DeviceError as e:
        raise EmulatorError(str(e)[-300:]) from None


def set_enabled(dev: Device, row: EmuRow, enabled: bool) -> None:
    if row.installed is None:
        raise EmulatorError(f"{row.entry.name} is not installed.")
    try:
        dev.set_enabled(row.installed.package, enabled)
    except DeviceError as e:
        raise EmulatorError(str(e)[-300:]) from None


def describe(row: EmuRow) -> str:
    info = row.installed
    if info is None:
        return "Not installed"
    kind = "came with the device" if info.system else "installed by you"
    if info.updated_system:
        kind = "came with the device, updated since"
    return ("Enabled" if info.enabled else "Disabled") + f" · {kind}"


def cmd_emulators(session, args, out) -> int:
    dev = session.device
    if dev is None:
        print("No device connected.")
        return 2
    rows = list_emulators(dev, load_catalog(session.user_catalog))
    if getattr(args, "check_updates", False):
        info = session.info()
        rows = check_updates(rows, info.sdk if info else None)
    data = [{"package": r.entry.package, "name": r.entry.name, "systems": list(r.entry.systems),
             "installed": r.installed.version if r.installed else None,
             "system_app": bool(r.installed and r.installed.system),
             "enabled": bool(r.installed and r.installed.enabled), "latest": r.latest,
             "update_available": r.update_available, "error": r.error} for r in rows]
    text = "\n".join(f"{r.entry.name:<22} {(r.installed.version if r.installed else '–'):<16} "
                     f"{describe(r):<46} {('latest ' + r.latest) if r.latest else r.error}" for r in rows)
    out(args, data, text)
    return 0
