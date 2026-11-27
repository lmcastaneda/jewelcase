"""Copies of the device's saves in the library: the newest of each file, and the versions it replaced.

    <root>/latest/<source id>/<path below the source's folder>
    <root>/history/<source id>/<same path>/<YYYYMMDD-HHMMSS>-<file name>

The folder is the truth and is usable from a file manager; the database only remembers what the device
looked like when each file was copied, so unchanged files are not read again. Nothing here is removed
because of what the device looks like: a save that disappears keeps its copy.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .db import SaveRow, Store
from .device import Device, DeviceError, DeviceGone, md5_file
from .savesources import SaveFile, SourceScan

STAMP = "%Y%m%d-%H%M%S"
KEEP_NEWEST, KEEP_DAYS, KEEP_STATES = 10, 30, 2
KEEP_SETTINGS = 5                               # newest versions of a settings file; plus a daily one, see keep()
_VERSION_NAME = re.compile(r"(\d{8}-\d{6})-(.+)\Z", re.DOTALL)


class _Stop(Exception):
    pass


class _LibraryGone(Exception):
    pass


@dataclass
class RunResult:
    run: str
    complete: bool = False                      # got through every source (files may still have been skipped)
    copied: int = 0
    unchanged: int = 0
    skipped: list[tuple[str, str, str]] = field(default_factory=list)     # (source id, rel, reason)
    unreadable: dict[str, int] = field(default_factory=dict)              # source id -> folders
    blocked: dict[str, int] = field(default_factory=dict)                 # source id -> files that cannot be opened
    current: set[tuple[str, str]] = field(default_factory=set)            # (source id, rel) proven to be in latest/
    stopped: str = ""                           # "" | "device" | "library" | "asked"


def saves_root(library: Path, model: str, serial: str) -> Path:
    """<library>/_saves/<model>-<serial>. A folder already kept for this serial wins over a new name."""
    base = Path(library) / "_saves"
    try:
        existing = sorted(d for d in base.iterdir() if d.is_dir() and d.name.endswith("-" + serial))
    except OSError:
        existing = []
    if existing:
        return existing[0]
    return base / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', model).strip('_') or 'device'}-{serial}"


def keep(stamps: list[str], kind: str, today: date) -> set[str]:
    """Which older versions of one file survive.

    Settings: the newest 5 and the last version of each day for 30 days. Save states: the newest few.
    Saves: the newest few, the last version of each recent day, and the last version of every month, so
    a save spoiled today can go back to yesterday's and to the end of any earlier month however often
    the file changed since.
    """
    ordered = sorted(stamps)
    if kind == "state":
        return set(ordered[-KEEP_STATES:])
    kept = set(ordered[-(KEEP_SETTINGS if kind == "settings" else KEEP_NEWEST):])
    first_day = (today - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    last_of_day: dict[str, str] = {}
    last_of_month: dict[str, str] = {}
    for stamp in ordered:                       # ascending: the last one written wins
        if stamp[:8] > first_day:
            last_of_day[stamp[:8]] = stamp
        last_of_month[stamp[:6]] = stamp
    if kind == "settings":      # small files that rarely change and are rarely wanted back from long ago: no monthly ones
        return kept | set(last_of_day.values())
    return kept | set(last_of_day.values()) | set(last_of_month.values())


def _safe(rel: str) -> bool:
    return bool(rel) and all(part not in ("", ".", "..") for part in rel.split("/"))


class SaveBackup:
    def __init__(self, dev: Device, store: Store, root: Path, serial: str, now=datetime.now):
        self.dev, self.store, self.root, self.serial, self.now = dev, store, Path(root), serial, now

    # ------------------------------------------------------------ where things are
    def latest_path(self, source: str, rel: str) -> Path:
        return self.root / "latest" / source / rel

    def version_path(self, source: str, rel: str, stamp: str) -> Path:
        return self.root / "history" / source / rel / f"{stamp}-{rel.rsplit('/', 1)[-1]}"

    def _new_stamp(self) -> str:
        """A run's name: its start time, always later than every stamp already stored for this device."""
        stamp, last = self.now().strftime(STAMP), self.last_stamp()
        if last and stamp <= last:
            stamp = (datetime.strptime(last, STAMP) + timedelta(seconds=1)).strftime(STAMP)
        self.store.set_meta(self.serial, "save_stamp", stamp)       # runs that copy nothing still use up a name
        return stamp

    def last_stamp(self) -> str:
        """The newest stamp in use for this device, "" before the first run."""
        return max(self.store.max_save_stamp(self.serial), self.store.meta(self.serial, "save_stamp", "") or "")

    def _free_stamp(self, source: str, rel: str, stamp: str) -> str:
        while self.version_path(source, rel, stamp).exists():
            stamp = (datetime.strptime(stamp, STAMP) + timedelta(seconds=1)).strftime(STAMP)
        return stamp

    # ------------------------------------------------------------ a run
    def run(self, scans: list[SourceScan], only: set[tuple[str, str]] | None = None,
            should_stop: Callable[[], bool] = lambda: False, record: bool = True) -> RunResult:
        """Copy what changed. `only` limits the run to (source id, rel) pairs; such a run is never complete."""
        if not self.store.save_files(self.serial) and (self.root / "latest").is_dir():
            self.rebuild_index()                # a new PC or a lost database: adopt what the folder holds
        result = RunResult(self._new_stamp())
        try:
            for scan in scans:
                sid = scan.source.id
                if scan.unreadable:
                    result.unreadable[sid] = len(scan.unreadable)
                if scan.blocked:
                    result.blocked[sid] = len(scan.blocked)
                result.skipped += [(sid, rel, "larger than 256 MB") for rel in scan.too_large
                                   if only is None or (sid, rel) in only]
                index = {row.rel: row for row in self.store.save_files(self.serial, sid)}
                for f in scan.files:
                    if only is not None and (sid, f.rel) not in only:
                        continue
                    if should_stop():
                        result.stopped = "asked"
                        raise _Stop
                    self._one(scan.root, f, index.get(f.rel), result)
                if only is None:
                    present = {f.rel for f in scan.files} | set(scan.too_large) | set(scan.blocked)
                    gone = [rel for rel, row in index.items() if rel not in present and not row.gone]
                    if gone:
                        self.store.mark_saves_gone(self.serial, sid, gone)
        except _Stop:
            pass
        except DeviceGone:
            result.stopped = "device"
        except _LibraryGone:
            result.stopped = "library"
        result.complete = not result.stopped and only is None
        if result.complete:
            try:
                self.prune({scan.source.id: scan.source.kind for scan in scans})
            except OSError:
                pass                            # the next complete run prunes
        if record:
            self.store.add_save_run(self.serial, result.run, result.complete, result.copied, {
                "skipped": [list(s) for s in result.skipped], "unreadable": result.unreadable,
                "blocked": result.blocked, "stopped": result.stopped})
        return result

    def _one(self, root: str, f: SaveFile, row: SaveRow | None, result: RunResult) -> None:
        sid = f.source
        if not _safe(f.rel):
            result.skipped.append((sid, f.rel, "unsafe name"))
            return
        latest = self.latest_path(sid, f.rel)
        if row and not row.gone and row.size == f.size and row.mtime == f.mtime and latest.is_file():
            result.unchanged += 1
            result.current.add((sid, f.rel))
            return
        part = latest.with_name(latest.name + ".part")
        try:
            outcome, md5 = self._fetch(f"{root}/{f.rel}", latest, part, row)
            if outcome == "new":
                self._retire(sid, f.rel, row)
                os.replace(part, latest)
        except DeviceGone:
            part.unlink(missing_ok=True)
            raise
        except DeviceError:
            part.unlink(missing_ok=True)
            result.skipped.append((sid, f.rel, "could not be read"))
            return
        except OSError as e:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            if not self.root.is_dir():
                raise _LibraryGone from None
            result.skipped.append((sid, f.rel, f"could not be stored in the library: {e.strerror or e}"))
            return
        if outcome == "torn":
            result.skipped.append((sid, f.rel, "changed while copying"))
        elif outcome == "same":
            self.store.put_save_file(self.serial, sid, f.rel, f.size, f.mtime, md5, row.run)
            result.unchanged += 1
            result.current.add((sid, f.rel))
        else:
            self.store.put_save_file(self.serial, sid, f.rel, f.size, f.mtime, md5, result.run)
            result.copied += 1
            result.current.add((sid, f.rel))

    def _fetch(self, device_path: str, latest: Path, part: Path, row: SaveRow | None) -> tuple[str, str]:
        """Bring one file to `part`, proven equal to what the device holds. One retry if it changed meanwhile."""
        for _ in range(2):
            on_device = self.dev.md5(device_path)
            if row and row.md5 == on_device and latest.is_file():
                return "same", on_device
            latest.parent.mkdir(parents=True, exist_ok=True)
            if self.dev.pull(device_path, part) == on_device:
                return "new", on_device
            part.unlink(missing_ok=True)
        return "torn", ""

    def _retire(self, source: str, rel: str, row: SaveRow | None) -> None:
        """Move the current newest copy into history before a different one takes its place.

        Every change gets its own dated entry, even when the content is one the file held before:
        "as it was on that date" has to keep meaning what the save held then.
        """
        latest = self.latest_path(source, rel)
        if not latest.exists():
            return
        if row is None:                     # a copy from before this index knew about it
            old_md5 = md5_file(latest)
            old_stamp = datetime.fromtimestamp(latest.stat().st_mtime).strftime(STAMP)
        else:
            old_md5, old_stamp = row.md5, row.run
        stamp = self._free_stamp(source, rel, old_stamp)
        target = self.version_path(source, rel, stamp)
        target.parent.mkdir(parents=True, exist_ok=True)
        size = latest.stat().st_size
        os.replace(latest, target)
        self.store.put_save_version(self.serial, source, rel, stamp, size, old_md5)

    # ------------------------------------------------------------ keeping the history in bounds
    def prune(self, kinds: dict[str, str]) -> int:
        """Remove versions beyond what `keep` allows. Never touches the newest copies."""
        removed, today = 0, self.now().date()
        for (source, rel), versions in self.store.all_save_versions(self.serial).items():
            kept = keep([v.stamp for v in versions], kinds.get(source, "save"), today)
            for v in versions:
                if v.stamp not in kept:
                    self.version_path(source, rel, v.stamp).unlink(missing_ok=True)
                    self.store.drop_save_version(self.serial, source, rel, v.stamp)
                    removed += 1
        return removed

    def rebuild_index(self) -> int:
        """Add index rows for copies found in the folder that the database does not know. Returns rows added."""
        added = 0
        known = {(r.source, r.rel) for r in self.store.save_files(self.serial)}
        latest_root = self.root / "latest"
        for path in sorted(latest_root.rglob("*")) if latest_root.is_dir() else []:
            parts = path.relative_to(latest_root).parts
            if not path.is_file() or len(parts) < 2 or path.name.endswith(".part"):
                continue
            source, rel = parts[0], "/".join(parts[1:])
            if (source, rel) not in known:
                stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime(STAMP)
                self.store.put_save_file(self.serial, source, rel, None, None, md5_file(path), stamp)
                added += 1
        versions = self.store.all_save_versions(self.serial)
        history_root = self.root / "history"
        for path in sorted(history_root.rglob("*")) if history_root.is_dir() else []:
            parts = path.relative_to(history_root).parts
            m = _VERSION_NAME.match(path.name)
            if not path.is_file() or len(parts) < 3 or not m or m.group(2) != parts[-2]:
                continue
            source, rel, stamp = parts[0], "/".join(parts[1:-1]), m.group(1)
            if stamp not in {v.stamp for v in versions.get((source, rel), [])}:
                self.store.put_save_version(self.serial, source, rel, stamp, path.stat().st_size, md5_file(path))
                added += 1
        return added
