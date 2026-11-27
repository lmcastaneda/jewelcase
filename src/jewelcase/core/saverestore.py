"""Putting saves back on the device.

The save on the device is always backed up first (into the connected handheld's own set), so a restore
can be undone by restoring the run that backup made. Nothing on the device is deleted: restore only
writes the files it restores. The copies may come from another handheld's set (`from_backup`).
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .device import Device, DeviceError, DeviceGone, md5_file
from .savebackup import STAMP, SaveBackup
from .savesources import Item, Source, item_name, map_text, scan_source

_APP_FOLDER = re.compile(r"^(/sdcard|/storage/emulated/\d+)/Android/data/([^/]+)")
TMP = ".jewelcase-tmp"
GONE = "The handheld was disconnected."


@dataclass(frozen=True)
class ItemVersion:
    run: str
    when: datetime
    size: int
    current: bool                   # this is what the newest copy holds


@dataclass
class RestoreResult:
    restored: list[str] = field(default_factory=list)                 # item keys
    failed: list[tuple[str, str]] = field(default_factory=list)       # (item key, reason)
    undo_run: str = ""              # restoring this run puts back what was on the device before
    undoable: list[str] = field(default_factory=list)    # restored keys whose files the safety backup captured


def _timeline(backup: SaveBackup, source: str, rel: str) -> list[tuple[str, Path, int]]:
    """(stamp, file, size) of every content this file has had, oldest first."""
    out = [(v.stamp, backup.version_path(source, rel, v.stamp), v.size)
           for v in backup.store.save_versions(backup.serial, source, rel)]
    row = next((r for r in backup.store.save_files(backup.serial, source) if r.rel == rel), None)
    latest = backup.latest_path(source, rel)
    if row is not None and latest.is_file():
        out.append((row.run, latest, latest.stat().st_size))
    return sorted(out)


def _content_at(backup: SaveBackup, source: str, rel: str, at: str | None) -> Path | None:
    """The copy this file had at a run: the last one made at or before it. None when it did not exist yet."""
    timeline = [t for t in _timeline(backup, source, rel) if at is None or t[0] <= at]
    return timeline[-1][1] if timeline else None


def versions(backup: SaveBackup, item: Item) -> list[ItemVersion]:
    """The runs in which any file of the item changed, newest first."""
    timelines = [_timeline(backup, item.source, rel) for rel in item.files]
    stamps = sorted({stamp for t in timelines for stamp, _, _ in t}, reverse=True)
    out = []
    for i, stamp in enumerate(stamps):
        size = sum(next((s for st, _, s in reversed(t) if st <= stamp), 0) for t in timelines)
        out.append(ItemVersion(stamp, datetime.strptime(stamp, STAMP), size, i == 0))
    return out


def blocked(dev: Device, source: Source, root: str, installed: set[str]) -> str:
    """Why saves of this source cannot be restored right now, or "" when they can."""
    if not source.restore:
        return f"Jewelcase cannot restore saves for {source.label} on this device."
    if dev.foreground_package() == source.package:
        return f"Close {source.label} on the handheld first."
    if source.package not in installed:         # also settings in shared storage, which Jewelcase would create
        return f"Install {source.label} first."
    m = _APP_FOLDER.match(root)
    if m and not dev.exists(m.group(0)):        # Jewelcase does not create an app's own folder
        return f"Open {source.label} once on the handheld, then restore."
    return ""


def restore(dev: Device, backup: SaveBackup, sources: dict[str, Source], roots: dict[str, str],
            items: list[Item], at: str | None, installed: set[str], from_backup: SaveBackup | None = None,
            cards: list[str] | None = None) -> RestoreResult:
    """Restore items to the run `at` (None: the newest copies made so far). An item that fails is skipped.

    `backup` is this handheld's own set: what is on the device now is saved there first. The copies put
    back come from `from_backup` (another handheld's set) when given, else from `backup`. `cards` are the
    handheld's memory card ids; with them, settings files naming another card are pointed at this one.
    """
    result = RestoreResult()
    content = from_backup or backup
    if at is None:
        at = content.last_stamp()       # the newest copies as they are now, before the safety backup below
    try:
        ready = []
        for item in items:
            reason = blocked(dev, sources[item.source], roots[item.source], installed)
            if reason:
                result.failed.append((item.key, reason))
            else:
                ready.append(item)
        captured: set[str] = set()
        ready = _backup_first(dev, backup, sources, roots, ready, result, captured)
        for item in ready:
            reason = blocked(dev, sources[item.source], roots[item.source], installed) or \
                _put_back(dev, content, roots[item.source], item, at, cards)
            if reason:
                result.failed.append((item.key, reason))
            else:
                result.restored.append(item.key)
                if item.key in captured:
                    result.undoable.append(item.key)
    except DeviceGone:
        done = set(result.restored) | {key for key, _ in result.failed}
        result.failed += [(item.key, GONE) for item in items if item.key not in done]
    return result


def _backup_first(dev: Device, backup: SaveBackup, sources: dict[str, Source], roots: dict[str, str],
                  items: list[Item], result: RestoreResult, captured: set[str] | None = None) -> list[Item]:
    """Back up the items as they are on the device now. Returns the items that are safe to replace.

    `captured` gets the keys of items whose every file the restore will write was on the device and is now
    in this run's backup: only for those does restoring the run bring back what was there before.
    """
    if not items:
        return []
    names = {(item.source, item.name) for item in items}
    scans = [scan_source(dev, sources[sid], root=roots[sid]) for sid in dict.fromkeys(item.source for item in items)]

    def of_item(scan, rel):
        return (scan.source.id, item_name(scan.source, rel)[0])

    only = {(scan.source.id, f.rel) for scan in scans for f in scan.files if of_item(scan, f.rel) in names}
    pre = backup.run(scans, only=only, record=False)
    result.undo_run = pre.run
    if pre.stopped == "device":
        raise DeviceGone(GONE)
    if pre.stopped:
        result.failed += [(item.key, "The library disk is not connected.") for item in items]
        return []
    # An item is safe to replace only when every file it has on the device is proven to be in the
    # library now. Files that cannot be opened, or are too large to keep, are never proven.
    not_saved: dict[tuple[str, str], str] = {}
    for scan in scans:
        for rel in scan.blocked:
            not_saved.setdefault(of_item(scan, rel), "it cannot be read")
        for rel in scan.too_large:
            not_saved.setdefault(of_item(scan, rel), "it is larger than 256 MB")
        for f in scan.files:
            if of_item(scan, f.rel) in names and (scan.source.id, f.rel) not in pre.current:
                why = next((reason for sid, rel, reason in pre.skipped if (sid, rel) == (scan.source.id, f.rel)),
                           "it was not copied")
                not_saved.setdefault(of_item(scan, f.rel), why)
    safe = []
    for item in items:
        reason = not_saved.get((item.source, item.name))
        if reason:
            result.failed.append((item.key, f"Its save on the handheld could not be backed up first ({reason})."))
        else:
            safe.append(item)
            if captured is not None and all((item.source, rel) in pre.current for rel in item.files):
                captured.add(item.key)
    return safe


def _put_back(dev: Device, backup: SaveBackup, root: str, item: Item, at: str | None,
              cards: list[str] | None = None) -> str:
    """Write one item's files. Every file is sent and checked before any save is replaced, then
    checked again in its place. Returns "" or why not."""
    copies = [(rel, _content_at(backup, item.source, rel, at)) for rel in item.files]
    copies = [(rel, path) for rel, path in copies if path is not None]
    if not copies:
        return "There is no copy of this save from that time."
    with tempfile.TemporaryDirectory(prefix="jewelcase-restore-") as scratch:
        if item.kind == "settings" and cards is not None:
            mapped = []
            for rel, local in copies:           # a settings file may name another handheld's memory card
                data, why = map_text(local.read_bytes(), cards)
                if data is None:
                    return why
                if data != local.read_bytes():
                    local = Path(scratch) / f"{len(mapped)}-{rel.rsplit('/', 1)[-1]}"
                    local.write_bytes(data)
                mapped.append((rel, local))
            copies = mapped
        return _send(dev, root, copies)


def _send(dev: Device, root: str, copies: list[tuple[str, Path]]) -> str:
    sent: list[tuple[str, str, str]] = []       # (temporary path, final path, md5)
    try:
        for rel, local in copies:
            final = f"{root}/{rel}"
            dev.mkdir(final.rsplit("/", 1)[0])
            want = md5_file(local)
            sent.append((final + TMP, final, want))
            dev.push(local, final + TMP)
            if dev.md5(final + TMP) != want:
                _discard(dev, sent)
                return "The copy on the handheld did not match and was not used."
        for tmp, final, want in sent:
            # Not a rename: the emulator must still be able to write to its save, so an existing file is
            # written where it is, and checked again because that write can be cut short.
            dev.put_in_place(tmp, final)
            if dev.md5(final) != want:
                _discard(dev, sent)
                return "It did not read back the same after it was written. Restore it again."
    except DeviceGone:
        raise
    except (DeviceError, OSError) as e:
        _discard(dev, sent)
        return f"It could not be written to the handheld: {str(e)[-200:]}"
    return ""


def _discard(dev: Device, sent: list[tuple[str, str, str]]) -> None:
    for tmp, *_ in sent:
        try:
            if dev.exists(tmp):
                dev.remove(tmp)
        except DeviceError:
            pass
