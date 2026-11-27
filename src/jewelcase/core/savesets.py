"""The backup sets in a library: one folder per handheld, `<library>/_saves/<model>-<serial>/`.

A set can be read without its handheld being connected, so the saves and settings of one handheld can be
put onto another (a replacement, or the same device after a reflash that changed its serial).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .db import RunRow, Store
from .savebackup import STAMP, SaveBackup
from .savesources import Item, Source, group_items

KINDS = ("save", "state", "settings")


@dataclass(frozen=True)
class BackupSet:
    name: str                       # the folder's name, "<model>-<serial>"
    model: str
    serial: str
    path: Path
    own: bool                       # the connected handheld's own set
    last: datetime | None           # when it was last backed up
    counts: dict                    # kind -> how many items ("save", "state", "settings")

    @property
    def label(self) -> str:
        return f"{self.model} · {self.serial}" + (" (this handheld)" if self.own else "")

    def to_dict(self) -> dict:
        return {"name": self.name, "model": self.model, "serial": self.serial, "own": self.own,
                "last_backup": self.last.isoformat() if self.last else None,
                "saves": self.counts.get("save", 0), "states": self.counts.get("state", 0),
                "settings": self.counts.get("settings", 0)}


def _files(latest: Path):
    """(source id, rel) of every copy kept in a set's latest/ folder."""
    for path in sorted(latest.rglob("*")) if latest.is_dir() else []:
        parts = path.relative_to(latest).parts
        if path.is_file() and len(parts) >= 2 and not path.name.endswith(".part"):
            yield parts[0], "/".join(parts[1:]), path


def _split(name: str, own_serial: str = "") -> tuple[str, str]:
    """(model, serial) of a folder name. The folder is the identity; this is only for showing it.

    The connected handheld's serial is known and may contain dashes; for another set the model ends at the
    first dash (models carry none, serials may).
    """
    if own_serial and name.endswith("-" + own_serial):
        return name[:-len(own_serial) - 1], own_serial
    model, dash, serial = name.partition("-")
    return (model, serial) if dash else ("", name)


def read_sets(library: Path | None, own: Path | None, sources: list[Source], store: Store,
              own_serial: str = "") -> list[BackupSet]:
    """Every backup set in the library, the connected handheld's own first, then the newest first."""
    base = Path(library) / "_saves" if library else None
    try:
        folders = sorted(d for d in base.iterdir() if d.is_dir() and (d / "latest").is_dir()) if base else []
    except OSError:
        return []
    by_id = {s.id: s for s in sources}
    out = []
    for folder in folders:
        is_own = own is not None and folder == own
        model, serial = _split(folder.name, own_serial if is_own else "")
        rels: dict[str, list[str]] = {}
        newest = 0.0
        for sid, rel, path in _files(folder / "latest"):
            rels.setdefault(sid, []).append(rel)
            newest = max(newest, path.stat().st_mtime)
        counts: dict[str, int] = {}
        for sid, found in rels.items():
            if sid in by_id:
                counts[by_id[sid].kind] = counts.get(by_id[sid].kind, 0) + len(group_items(by_id[sid], found, {}, ()))
        run = store.last_save_run(own_serial) if is_own and own_serial else None   # other sets: by their files
        last = datetime.strptime(run.stamp, STAMP) if run else (datetime.fromtimestamp(newest) if newest else None)
        out.append(BackupSet(folder.name, model.replace("_", " ") or "device", serial, folder,
                             is_own, last, counts))
    return sorted(out, key=lambda s: (not s.own, -(s.last.timestamp() if s.last else 0), s.name))


def matching_sets(sets: list[BackupSet], wanted: str) -> list[BackupSet]:
    """The sets a name can mean: one folder name (`RG477V-ABC123`) exactly, else every set with that serial."""
    wanted = wanted.strip().lower()
    exact = [s for s in sets if s.name.lower() == wanted]
    return exact or [s for s in sets if s.serial.lower() == wanted]


def find_set(sets: list[BackupSet], wanted: str) -> BackupSet | None:
    """The one set a name means, or None when there is none or the name fits more than one."""
    found = matching_sets(sets, wanted)
    return found[0] if len(found) == 1 else None


def engine(bset: BackupSet, dev, store: Store) -> SaveBackup:
    """A reader for another handheld's set, its index keyed by the set's folder name (two folders may
    share a serial). The index is built from the folder when this PC never saw it: that hashes every file
    of the set, so call this only for a set that is actually used, and away from the UI thread."""
    backup = SaveBackup(dev, store, bset.path, bset.name)
    if not store.save_files(bset.name) and (bset.path / "latest").is_dir():
        backup.rebuild_index()
    return backup


def items_in(bset: BackupSet, sources: list[Source]) -> list[Item]:
    """Every save, state and settings file the set holds, as restorable items. Only lists the folder:
    nothing is hashed and the database is not touched."""
    by_id = {s.id: s for s in sources}
    rels: dict[str, list[str]] = {}
    for sid, rel, _ in _files(bset.path / "latest"):
        rels.setdefault(sid, []).append(rel)
    return [item for sid, found in rels.items() if sid in by_id for item in group_items(by_id[sid], found, {}, ())]


def runs_of(backup: SaveBackup) -> list[RunRow]:
    """The backups of a set that copied something, newest first (for a set the database has no runs of,
    the stamps its files and versions carry)."""
    runs = backup.store.save_runs(backup.serial)
    if runs:
        return runs
    stamps = {row.run for row in backup.store.save_files(backup.serial)}
    stamps |= {v.stamp for versions in backup.store.all_save_versions(backup.serial).values() for v in versions}
    return [RunRow(stamp, True, 0, {}) for stamp in sorted(stamps, reverse=True)[:100] if re.fullmatch(r"\d{8}-\d{6}", stamp)]
