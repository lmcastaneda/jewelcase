"""Proving a device file has a twin in the library, and turning ticks into a sync plan."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from .catalog import GameRow, Profile, files_missing_from_library
from .convert import Conversion
from .db import Store
from .device import Device, DeviceInfo, Storage
from .library import Library, ToolMissing, archive_member_md5, find_archive_member

MARGIN = 256 * 1024 * 1024
DEFAULT_RATE = 35_000_000          # bytes per second onto a UHS-I card
CONVERT_RATE = 40_000_000          # bytes per second through one pass of unpacking, compressing or checking


@dataclass(frozen=True)
class TwinResult:
    ok: bool
    how: str          # record | hash | archive | ""
    reason: str = ""


def prove_twin(dev: Device, library: Library, store: Store, profile: Profile, system: str, name: str,
               stem: str | None = None) -> TwinResult:
    """Is there a library file with exactly the bytes of this device file, as both are right now?

    A remembered hash counts only while the file it was taken from still has the same size and
    modification time, on the device and in the library alike.
    """
    if not library.online:
        return TwinResult(False, "", "library offline")
    serial = dev.serial
    remote = f"{profile.rom_root.rstrip('/')}/{system}/{name}"
    st = dev.stat(remote)
    if st is None:
        return TwinResult(False, "", "not on the device")
    size = st.size
    recorded = store.dev_hash(serial, system, name, size, st.mtime)
    dev_md5 = recorded
    reason = "no library copy"

    def device_md5() -> str:
        nonlocal dev_md5
        if dev_md5 is None:
            dev_md5 = dev.md5(remote)
            store.set_dev_hash(serial, system, name, size, st.mtime, dev_md5)
        return dev_md5

    try:
        if library.size(system, name) is not None:
            path = library.file_path(system, name)
            if not path.is_file():
                reason = "no library copy"
            elif path.stat().st_size != size:
                reason = "size differs"
            else:
                fresh = library.fresh_md5(system, name)
                if (fresh or library.md5(system, name)) == device_md5():
                    return TwinResult(True, "record" if fresh and recorded else "hash")
                return TwinResult(False, "", "contents differ")

        stems = {PurePosixPath(name).stem} | ({stem} if stem else set())
        for candidate in library.names(system):
            p = PurePosixPath(candidate)
            if p.suffix.lower() in (".7z", ".zip") and p.stem in stems and candidate != name:
                archive = library.file_path(system, candidate)
                if find_archive_member(archive, name, size):
                    if archive_member_md5(archive, name) == device_md5():
                        return TwinResult(True, "archive")
                    return TwinResult(False, "", "contents differ")
    except ToolMissing:
        return TwinResult(False, "", "7z is not installed")
    except OSError as e:
        return TwinResult(False, "", f"library read error: {e.strerror or e}")
    return TwinResult(False, "", reason)


@dataclass(frozen=True)
class PlanItem:
    row: GameRow
    files: tuple[str, ...]
    bytes: int
    convert: Conversion | None = None       # an addition whose files are first made from an archive


@dataclass(frozen=True)
class SyncPlan:
    adds: list[PlanItem] = field(default_factory=list)
    removes: list[PlanItem] = field(default_factory=list)
    pulls: list[PlanItem] = field(default_factory=list)       # copy to the library (first)
    skipped: list[tuple[GameRow, str]] = field(default_factory=list)
    free_before: int = 0
    free_after: int = 0
    total: int = 0
    seconds: int = 0
    fits: bool = False
    warnings: list[str] = field(default_factory=list)
    info: DeviceInfo | None = None
    rate: float = DEFAULT_RATE
    have_device: bool = False
    kept: dict = field(default_factory=dict, compare=False)   # system -> {game: files} staying on the device
    covers: frozenset = field(default_factory=frozenset, compare=False)   # added games whose cover goes to ES-DE

    @property
    def empty(self) -> bool:
        return not (self.adds or self.removes or self.pulls)

    @property
    def add_bytes(self) -> int:
        return sum(i.bytes for i in self.adds)

    @property
    def remove_bytes(self) -> int:
        return sum(i.bytes for i in self.removes)


def _remove_item(row: GameRow, staying: dict[str, tuple]) -> PlanItem:
    """What removing this game deletes: its files, minus any a game that stays still uses."""
    kept = {n for files in staying.values() for n in files}
    files = tuple(n for n in row.dev_unit.files if n not in kept)
    return PlanItem(row, files, sum(row.sizes.get(n, 0) for n in files))


def _finish(plan: SyncPlan) -> SyncPlan:
    free_after = plan.free_before + plan.remove_bytes - plan.add_bytes
    moved = plan.add_bytes + sum(i.bytes for i in plan.pulls)
    converting = sum(i.convert.work for i in plan.adds if i.convert)
    seconds = int(moved / plan.rate + 2 * len(plan.removes) + converting / CONVERT_RATE)
    warnings = []
    if seconds > 1800 and plan.info and plan.info.power == "usb":
        warnings.append(f"This will take about {seconds // 60} minutes and the device is charging from USB "
                        "only, so it will lose charge. Use the wall adapter.")
    margin = min(MARGIN, plan.total // 100)
    # only adding needs room; removing games always "fits", however full the card is
    fits = plan.have_device and (plan.add_bytes == 0 or free_after >= margin)
    return replace(plan, free_after=free_after, seconds=seconds, warnings=warnings, fits=fits)


def build_plan(rows: list[GameRow], dev_storage: Storage | None, library: Library,
               info: DeviceInfo | None, rate: float = DEFAULT_RATE) -> SyncPlan:
    online = library.online
    adds, leaving, skipped = [], [], []
    for r in rows:
        if r.want and not r.on_device:
            if not online:
                skipped.append((r, "library offline"))
            elif r.lib_unit is None and r.convert is None:
                skipped.append((r, "needs conversion"))
            elif r.lib_unit is None and r.convert.problem:
                skipped.append((r, r.convert.problem))
            elif r.lib_unit is None:
                adds.append(PlanItem(r, r.convert.files, r.convert.bytes, r.convert))
            elif r.lib_unit.missing:
                skipped.append((r, "incomplete in the library"))
            else:
                adds.append(PlanItem(r, r.lib_unit.files, r.size))
        elif r.on_device and not r.want:
            if not online:
                skipped.append((r, "library offline"))
            elif files_missing_from_library(r, library):
                skipped.append((r, "no library copy"))
            else:
                leaving.append(r)
    gone = {r.key for r in leaving}
    staying: dict[str, dict[str, tuple]] = {}        # every game that will still be on the device
    for r in rows:
        if r.on_device and r.key not in gone:
            staying.setdefault(r.system, {})[r.stem] = r.dev_unit.files
    removes = [_remove_item(r, staying.get(r.system, {})) for r in leaving]
    return _finish(SyncPlan(adds, removes, [], skipped,
                            free_before=dev_storage.free if dev_storage else 0,
                            total=dev_storage.total if dev_storage else 0,
                            info=info, rate=rate, have_device=dev_storage is not None, kept=staying))


def with_copy_first(plan: SyncPlan, keys: set[tuple[str, str]]) -> SyncPlan:
    """Move 'no library copy' games into the plan: copy each to the library, then remove it."""
    pulls, skipped, moved = list(plan.pulls), [], []
    for row, why in plan.skipped:
        if why == "no library copy" and row.key in keys:
            pulls.append(PlanItem(row, row.dev_unit.files, row.size))
            moved.append(row)
        else:
            skipped.append((row, why))
    staying = {system: {stem: files for stem, files in games.items()
                        if (system, stem) not in {r.key for r in moved}}
               for system, games in plan.kept.items()}
    leaving = [i.row for i in plan.removes] + moved
    removes = [_remove_item(r, staying.get(r.system, {})) for r in leaving]
    return _finish(replace(plan, pulls=pulls, removes=removes, skipped=skipped, kept=staying))
