"""Save backups as the window and the command line use them: an overview per emulator, back up, restore."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import emulators, gameids, saverestore, savesets
from .catalog import clean_title
from .device import Device, DeviceGone
from .fmt import human_size
from .savebackup import STAMP, RunResult, SaveBackup
from .saverestore import RestoreResult
from .savesets import BackupSet
from .savesources import (Item, Source, card_ids, group_items, load_sources, map_root, resolve_root, scan_all,
                          sfo_title)
from .sync import Busy, job_lock

OFFLINE = "The library disk is not connected, so saves are not being backed up."
NOUNS = {"save": "Save", "state": "Save State", "settings": "Settings"}
STATUS_WORDS = {"protected": "Protected", "partly": "Partly Protected", "not_protected": "Not Protected",
                "nothing": "Nothing to Back Up Yet", "not_reachable": "Not Reachable"}


class SavesError(Exception):
    """Something about saves that could not be done, in words for the user."""


class LibraryOffline(SavesError):
    pass


@dataclass(frozen=True)
class ItemInfo:
    item: Item
    title: str
    kind: str                       # "Save" | "Save State" | "Memory Card" | "Settings"
    changed: datetime | None        # when a backup last found it changed
    versions: int
    size: int
    gone: bool                      # no longer on the handheld


@dataclass(frozen=True)
class EmulatorSaves:
    package: str
    label: str
    status: str                     # protected | partly | not_protected | nothing | not_reachable
    reason: str
    advice: str
    items: tuple[ItemInfo, ...]
    unreadable: int                 # save folders that cannot be entered
    blocked: int = 0                # save files that cannot be opened
    skipped: tuple[tuple[str, str], ...] = ()       # (file, why) the last full backup could not copy

    @property
    def headline(self) -> str:
        return " · ".join(x for x in (self.label, STATUS_WORDS[self.status], self.reason) if x)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _kind_count(n: int, kind: str) -> str:
    """"3 saves", "1 save state", "2 settings files": how many of a kind."""
    return _count(n, "settings file" if kind == "Settings" else kind.lower())


def user_sources(session) -> Path | None:
    return session.config_path.parent / "saves.toml" if session.config_path else None


def sources(session) -> list[Source]:
    return load_sources(user_sources(session))


def _engine(session, dev: Device | None) -> SaveBackup:
    return SaveBackup(dev, session.store, session.saves_root, session.serial)


def _need(session) -> Device:
    dev = session.live_device()
    if dev is None or not session.serial:
        raise SavesError("No device connected.")
    if not session.library.online or session.saves_root is None:
        raise LibraryOffline(OFFLINE)
    return dev


# ---------------------------------------------------------------- what is protected
def last_backup(session):
    """The last run that got through every source, or None."""
    return session.store.last_save_run(session.serial) if session.serial else None


def overview(session, rows=None) -> list[EmulatorSaves]:
    """Every emulator with saves, with unreadable saves, or installed: what is protected and what is not.

    Reads only the database, so it works with the device unplugged and the library offline.
    """
    serial = session.serial
    if not serial:
        return []
    store = session.store
    catalog = emulators.load_catalog(session.user_catalog)
    scanned = store.meta(serial, "save_scan", {}) or {}
    installed = set(store.meta(serial, "save_installed", []) or [])
    titles = store.meta(serial, "save_titles", {}) or {}
    versions = store.all_save_versions(serial)
    by_source: dict[str, dict] = {}
    for row in store.save_files(serial):
        by_source.setdefault(row.source, {})[row.rel] = row
    stems: dict[str, set[str]] = {}
    rows = list(rows if rows is not None else session.rows())
    for r in rows:
        names = stems.setdefault(r.system, set())
        names.add(r.stem)
        names.update(name.rsplit(".", 1)[0] for name in r.sizes)
    by_package: dict[str, list[Source]] = {}
    for src in sources(session):
        by_package.setdefault(src.package, []).append(src)
    ids = gameids.known(session.library, store, rows, _id_systems(session, catalog))  # reads no game file now
    last = store.last_save_run(serial)
    left_out: dict[str, list[tuple[str, str]]] = {}
    for sid, rel, why in (last.detail.get("skipped", []) if last else []):
        left_out.setdefault(sid, []).append((rel, why))

    out = []
    for package, srcs in by_package.items():
        systems = catalog[package].systems if package in catalog else ()
        infos = []
        for src in srcs:
            files = by_source.get(src.id, {})
            for item in group_items(src, files, stems, systems, ids):
                stamps = {files[rel].run for rel in item.files}
                stamps |= {v.stamp for rel in item.files for v in versions.get((src.id, rel), [])}
                title = titles.get(item.key) or (f"{item.title} — {item.linked}" if item.linked
                                                 else clean_title(item.title) if item.game else item.title)
                infos.append(ItemInfo(
                    item, title, src.noun or NOUNS.get(src.kind, "Save"),
                    datetime.strptime(max(files[rel].run for rel in item.files), STAMP), len(stamps),
                    sum(files[rel].size or 0 for rel in item.files), all(files[rel].gone for rel in item.files)))
        def seen(kind_is_settings: bool, key: str) -> int:
            if kind_is_settings and not session.cfg.backup_settings:
                return 0                        # switched off: an older scan of them says nothing now
            return sum(int((scanned.get(s.id) or {}).get(key, 0)) for s in srcs
                       if (s.kind == "settings") == kind_is_settings)
        unreadable, blocked = seen(False, "unreadable"), seen(False, "blocked")     # saves and states only:
        settings_cannot = _cannot(seen(True, "unreadable"), seen(True, "blocked"), "settings")  # these decide the status
        if not infos and not unreadable and not blocked and not settings_cannot and package not in installed:
            continue
        what = _cannot(unreadable, blocked)
        skipped = tuple(x for s in srcs for x in left_out.get(s.id, []))
        problems = [f"{what} cannot be read"] if what else []
        if settings_cannot:
            problems.append(f"{settings_cannot} cannot be read")
        if skipped:
            problems.append(f"{_count(len(skipped), 'file')} could not be copied")
        if infos:
            status = "partly" if problems else "protected"
            counts: dict[str, int] = {}
            for i in infos:
                counts[i.kind] = counts.get(i.kind, 0) + 1
            reason = "; ".join([", ".join(_kind_count(n, kind) for kind, n in counts.items()), *problems])
        elif problems:
            status, reason = "not_protected", "; ".join(problems)
        else:
            status, reason = "nothing", ""
        advice = next((s.advice for s in srcs if s.advice and (scanned.get(s.id) or {}).get("unreadable")), "")
        out.append(EmulatorSaves(package, srcs[0].label, status, reason, advice,
                                 tuple(sorted(infos, key=lambda i: (i.title.lower(), i.kind))), unreadable,
                                 blocked, skipped))
    for package in installed - set(by_package):
        entry = catalog.get(package)
        if entry and entry.kind == "emulator":
            out.append(EmulatorSaves(package, entry.name, "not_reachable",
                                     "Keeps its saves where Jewelcase cannot read them", "", (), 0))
    return sorted(out, key=lambda e: e.label.lower())


def _cannot(folders: int, files: int, what: str = "save") -> str:
    parts = ([_count(folders, f"{what} folder")] if folders else []) + ([_count(files, "file" if what == "save" else f"{what} file")] if files else [])
    return " and ".join(parts)


def cannot_back_up(session, package: str, rows=None) -> str:
    """What of this emulator Jewelcase cannot read at all ("10 save folders"), or ""."""
    return next((_cannot(e.unreadable, e.blocked) for e in overview(session, rows) if e.package == package), "")


def unreadable_now_detail(session, package: str) -> tuple[str, str]:
    """Look at the device now: what of this emulator cannot be read, as ("10 save folders", "1 settings file").

    Either is "" when nothing is wrong. Settings only count while they are backed up (Preferences).
    For the Remove dialog, which must not depend on a backup having run before.
    """
    dev = session.live_device()
    srcs = [s for s in sources(session) if s.package == package]
    if dev is None or not srcs or not session.serial:
        return "", ""
    try:
        found = scan_all(dev, srcs, include_settings=session.cfg.backup_settings)
    except DeviceGone:
        return "", ""
    scanned = session.store.meta(session.serial, "save_scan", {}) or {}
    scanned.update({s.source.id: {"exists": s.exists, "unreadable": len(s.unreadable),
                                  "blocked": len(s.blocked)} for s in found})
    session.store.set_meta(session.serial, "save_scan", scanned)
    of_saves = [s for s in found if s.source.kind != "settings"]
    of_settings = [s for s in found if s.source.kind == "settings"]
    return (_cannot(sum(len(s.unreadable) for s in of_saves), sum(len(s.blocked) for s in of_saves)),
            _cannot(sum(len(s.unreadable) for s in of_settings), sum(len(s.blocked) for s in of_settings), "settings"))


def unreadable_now(session, package: str) -> str:
    """What of this emulator's saves cannot be read right now ("10 save folders"), or ""."""
    return unreadable_now_detail(session, package)[0]


def check_run(at: str | None) -> None:
    if at is not None and not re.fullmatch(r"\d{8}-\d{6}", at):
        raise SavesError(f"{at} is not a backup run. Runs look like 20261126-140500; "
                         "`jewelcase saves versions KEY` lists them.")


def _id_systems(session, catalog=None, held: set[str] | None = None) -> set[str]:
    """The systems whose games' IDs could name a save: those of emulators with saves not named after the game.

    Settings never carry a game's ID. `held`: only sources with backed-up files count.
    """
    catalog = catalog if catalog is not None else emulators.load_catalog(session.user_catalog)
    return {s for src in sources(session)
            if src.items != "name" and src.kind != "settings" and src.package in catalog
            and (held is None or src.id in held)
            for s in catalog[src.package].systems if s in gameids.ID_SYSTEMS}


def read_game_ids(session, rows=None, keys=None, should_stop=lambda: False) -> int:
    """Read (once per file) the IDs of library games that saves named by an ID could belong to.

    Only when such saves are known, and only for the games of those emulators' systems. `keys`: just
    these (system, stem) games. Slow on a slow disk: call it away from the UI thread. Returns how many
    files were read; 0 when everything asked for is already remembered.
    """
    if not session.serial:
        return 0
    held = {r.source for r in session.store.save_files(session.serial)}
    systems = _id_systems(session, held=held)       # per system: only emulators that hold such saves count
    if not systems:
        return 0
    return gameids.read_missing(session.library, session.store,
                                list(rows if rows is not None else session.rows()), systems, keys, should_stop)


def find_items(session, system: str, stem: str, rows=None, read: bool = True) -> list[Item]:
    """The backed-up saves that belong to one game, among them those named by its game ID.

    `read`: look into the game's own file for its ID if that has not been done (once; then remembered).
    """
    rows = list(rows if rows is not None else session.rows())
    if read:
        read_game_ids(session, rows, keys=[(system, stem)])
    return [i.item for e in overview(session, rows) for i in e.items
            if i.item.game == (system, stem) or (system, stem) in i.item.games]


def versions(session, key: str):
    return saverestore.versions(_engine(session, None), _items(session, [key])[0])


def backup_runs(session, from_set: BackupSet | None = None) -> list:
    """Runs in which something was copied, newest first: the moments "Restore All" can go back to.

    `from_set`: the runs of another handheld's set instead.
    """
    if from_set is not None and not from_set.own:
        try:
            return savesets.runs_of(savesets.engine(from_set, None, session.store))
        except OSError as e:
            raise _set_unreadable(from_set, e) from None
    return session.store.save_runs(session.serial) if session.serial else []


# ---------------------------------------------------------------- other handhelds' backups
def backup_sets(session) -> list[BackupSet]:
    """The backup sets in the library, the connected handheld's own first. Empty with the library offline."""
    if not session.library.online or not session.library.root:
        return []
    try:
        return savesets.read_sets(session.library.root, session.saves_root, sources(session), session.store,
                                  session.serial or "")
    except OSError:                             # the library went away while its folders were read
        return []


def has_other_sets(session) -> bool:
    """Is there a backup set of another handheld in the library? Only looks at folder names."""
    own = session.saves_root
    try:
        base = Path(session.library.root) / "_saves"
        return session.library.online and any(d.is_dir() and (d / "latest").is_dir() and d != own
                                              for d in base.iterdir())
    except (OSError, TypeError):
        return False


def other_set(session, name: str) -> BackupSet | None:
    """The set a `--from NAME` names, None for this handheld's own. Raises SavesError for an unknown name."""
    found = backup_sets(session)
    matches = savesets.matching_sets(found, name)
    if len(matches) > 1:
        raise SavesError(f"More than one backup set matches {name}: {', '.join(s.name for s in matches)}. "
                         "Use the full name.")
    if not matches:
        known = ", ".join(s.name for s in found) or "none"
        raise SavesError(f"No backup set is called {name}. `jewelcase saves devices` lists them (in this library: {known}).")
    return None if matches[0].own else matches[0]


def _set_unreadable(bset: BackupSet, error: OSError) -> SavesError:
    return SavesError(f"The backups of {bset.label} could not be read: {error.strerror or error}. "
                      "Is the library disk still connected?")


def set_items(session, bset: BackupSet) -> list[Item]:
    """What another handheld's set holds, restorable here. Only lists the set's folder: nothing is hashed."""
    try:
        return savesets.items_in(bset, sources(session))
    except OSError as e:
        raise _set_unreadable(bset, e) from None


@dataclass(frozen=True)
class SetContents:
    items: list[Item]
    runs: list


def read_set(session, bset: BackupSet) -> SetContents:
    """What a set holds and the backups it can go back to. For another handheld's set this builds its index
    (hashing every file when this PC never saw it): call it away from the UI thread, for a set that was
    chosen. Raises SavesError with a sentence when the set cannot be read."""
    return SetContents(set_items(session, bset), backup_runs(session, bset))


# ---------------------------------------------------------------- backing up
def backup(session, packages: set[str] | None = None, should_stop=lambda: False) -> RunResult:
    """Copy what changed. With `packages`, only those emulators' saves (not counted as a full backup)."""
    dev = _need(session)
    with job_lock(session.data_dir):
        srcs = [s for s in sources(session) if packages is None or s.package in packages]
        engine = _engine(session, dev)
        try:
            scans = scan_all(dev, srcs, include_states=session.cfg.backup_save_states,
                             include_settings=session.cfg.backup_settings)
            catalog = emulators.load_catalog(session.user_catalog)
            installed = sorted(p.package for p in dev.packages(sorted(catalog)))
        except DeviceGone:
            return RunResult(engine.last_stamp(), stopped="device")
        result = engine.run(scans, should_stop=should_stop, record=packages is None)
        store, serial = session.store, session.serial
        scanned = store.meta(serial, "save_scan", {}) or {}
        scanned.update({s.source.id: {"exists": s.exists, "unreadable": len(s.unreadable),
                                      "blocked": len(s.blocked)} for s in scans})
        store.set_meta(serial, "save_scan", scanned)
        store.set_meta(serial, "save_installed", installed)
        _remember_titles(session, engine, scans)
        return result


def backup_before_removal(session, package: str, wait: float = 20.0) -> tuple[bool, str]:
    """Back up one emulator's saves before its data may be deleted. (True, "") when nothing is at risk.

    Saves that cannot be read at all do not count: the Remove dialog has already said so.
    """
    dev = session.live_device()
    if dev is None:
        return False, "no device is connected"
    srcs = [s for s in sources(session) if s.package == package]
    try:
        found = scan_all(dev, srcs, include_states=session.cfg.backup_save_states,
                         include_settings=session.cfg.backup_settings)
    except DeviceGone:
        return False, "the handheld was disconnected"
    if not any(scan.files or scan.too_large for scan in found):     # files that cannot be opened do not count
        return True, ""                         # nothing of this emulator to lose
    if not session.library.online or session.saves_root is None:
        return False, "the library disk is not connected"
    end = time.monotonic() + wait
    while True:
        try:
            result = backup(session, {package})
            break
        except Busy:                            # an automatic backup is still letting go
            if time.monotonic() > end:
                return False, "another backup or sync is still running"
            time.sleep(0.25)
        except SavesError as e:
            return False, str(e)
    if result.stopped:
        return False, {"device": "the handheld was disconnected", "library": "the library disk went away",
                       "asked": "it was stopped"}.get(result.stopped, result.stopped)
    if result.skipped:
        return False, f"{_count(len(result.skipped), 'file')} could not be copied"
    return True, ""


def _remember_titles(session, engine: SaveBackup, scans) -> None:
    """PlayStation save folders carry the game's name in PARAM.SFO; keep it so the list can show it."""
    titles = session.store.meta(session.serial, "save_titles", {}) or {}
    for scan in scans:
        if scan.source.items != "folder":
            continue
        for item in group_items(scan.source, [f.rel for f in scan.files], {}, ()):
            sfo = next((rel for rel in item.files if rel.rsplit("/", 1)[-1].lower() == "param.sfo"), None)
            if sfo and item.key not in titles:
                try:
                    title = sfo_title(engine.latest_path(scan.source.id, sfo).read_bytes())
                except OSError:
                    title = None
                if title:
                    titles[item.key] = title
    session.store.set_meta(session.serial, "save_titles", titles)


# ---------------------------------------------------------------- restoring
def _items(session, keys: list[str], from_set: BackupSet | None = None) -> list[Item]:
    if from_set is not None:
        known = {i.key: i for i in set_items(session, from_set)}
    else:
        known = {i.item.key: i.item for e in overview(session) for i in e.items}
    missing = [k for k in keys if k not in known]
    if missing:
        raise SavesError("No such save: " + ", ".join(missing))
    return [known[k] for k in keys]


def _restore(session, items: list[Item], at: str | None, from_set: BackupSet | None = None) -> RestoreResult:
    """Put items back. With `from_set` the copies come from that handheld's set; what is on this handheld
    is always backed up into its own set first."""
    check_run(at)
    dev = _need(session)
    with job_lock(session.data_dir):
        by_id = {s.id: s for s in sources(session)}
        try:
            cards = card_ids(dev)
            roots, unmapped = {}, {}
            for sid in {i.source for i in items}:
                root, why = map_root(resolve_root(dev, by_id[sid]), cards)    # a folder on another handheld's card
                if root is None:
                    unmapped[sid] = why
                else:
                    roots[sid] = root
            installed = {p.package for p in dev.packages(sorted({by_id[i.source].package for i in items}))}
        except DeviceGone:
            return RestoreResult(failed=[(i.key, saverestore.GONE) for i in items])
        ready = [i for i in items if i.source in roots]
        failed = [(i.key, unmapped[i.source]) for i in items if i.source in unmapped]
        if not ready:
            return RestoreResult(failed=failed)
        try:
            content = savesets.engine(from_set, dev, session.store) if from_set is not None else None
        except OSError as e:
            raise _set_unreadable(from_set, e) from None
        # Another handheld's settings may name its memory card: only those are pointed at this one's card.
        result = saverestore.restore(dev, _engine(session, dev), by_id, roots, ready, at, installed,
                                     from_backup=content, cards=cards if from_set is not None else None)
        result.failed = failed + result.failed
        return result


def restore(session, keys: list[str], at: str | None = None, from_set: BackupSet | None = None) -> RestoreResult:
    return _restore(session, _items(session, keys, from_set), at, from_set)


def restore_all(session, package: str | None = None, at: str | None = None,
                from_set: BackupSet | None = None) -> RestoreResult:
    """Everything, or one emulator's saves, as of a run (None: the newest copies)."""
    if from_set is not None:
        by_id = {s.id: s for s in sources(session)}
        items = [i for i in set_items(session, from_set) if package is None or by_id[i.source].package == package]
    else:
        items = [i.item for e in overview(session) for i in e.items if package is None or e.package == package]
    return _restore(session, items, at, from_set)


# ---------------------------------------------------------------- command line
def _run_dict(r: RunResult) -> dict:
    return {"run": r.run, "complete": r.complete, "copied": r.copied, "unchanged": r.unchanged,
            "skipped": [list(s) for s in r.skipped], "unreadable": r.unreadable, "blocked": r.blocked,
            "stopped": r.stopped}


def run_text(r: RunResult) -> str:
    """One line for a backup's outcome."""
    if r.stopped:
        why = {"device": "the handheld was disconnected", "library": "the library disk went away",
               "asked": "it was stopped"}.get(r.stopped, r.stopped)
        return f"Saves were not backed up: {why}."
    text = f"Saves backed up: {r.copied} copied, {r.unchanged} unchanged."
    if r.skipped:
        text += f" {_count(len(r.skipped), 'file')} could not be copied."
    return text


def backup_after_sync(session) -> tuple[dict, str]:
    """The backup that follows a sync. Never raises: a sync's result stands on its own."""
    try:
        result = backup(session)
    except (SavesError, Busy) as e:
        return {"complete": False, "copied": 0, "error": str(e)}, f"Saves were not backed up: {e}"
    return _run_dict(result), run_text(result)


def restore_noun(session, keys) -> str:
    """"item" when settings are among these item keys (a word true for both), else "save"."""
    kinds = {s.id: s.kind for s in sources(session)}
    return "item" if any(kinds.get(k.split("/", 1)[0]) == "settings" for k in keys) else "save"


def kind_word(session, key: str) -> str:
    """What one item is, in a sentence: "Save", "Save state", "Memory card", "Settings"."""
    src = next((s for s in sources(session) if s.id == key.split("/", 1)[0]), None)
    return (src.noun or NOUNS.get(src.kind, "Save")).capitalize() if src else "Save"


def _restore_out(session, args, out, result: RestoreResult) -> int:
    data = {"restored": result.restored, "failed": [list(f) for f in result.failed], "undo_run": result.undo_run}
    lines = [f"Restored {_count(len(result.restored), restore_noun(session, result.restored))}."]
    lines += [f"  not restored: {key} — {why}" for key, why in result.failed]
    if result.restored:
        lines.append(f"To undo: jewelcase saves restore … --at {result.undo_run} --yes")
    out(args, data, "\n".join(lines))
    return 0 if not result.failed else 1


def cmd_saves(session, args, out) -> int:
    sub = getattr(args, "saves_cmd", None)
    try:
        if sub == "backup":
            result = backup(session)
            text = run_text(result)
            for sid, rel, why in result.skipped:
                text += f"\n  skipped: {sid}/{rel} — {why}"
            out(args, _run_dict(result), text)
            return 0 if result.complete else 1
        if sub == "versions":
            found = versions(session, args.key)
            data = [{"run": v.run, "when": v.when.isoformat(), "size": v.size, "current": v.current} for v in found]
            text = "\n".join(f"{v.run}  {v.when:%Y-%m-%d %H:%M}  {human_size(v.size):>9}"
                             f"{'  (newest)' if v.current else ''}" for v in found)
            out(args, data, text or "No versions.")
            return 0
        if sub == "restore":
            return _cmd_restore(session, args, out)
        if sub == "devices":
            return _cmd_devices(session, args, out)
    except (SavesError, Busy) as e:
        print(e)
        return 2
    return _status(session, args, out)


def _cmd_devices(session, args, out) -> int:
    """The backup sets in the library: this handheld's and every other one's."""
    found = backup_sets(session)
    if not session.library.online:
        raise LibraryOffline(OFFLINE)
    lines = [f"{s.name:<32} {('last backed up ' + format(s.last, '%Y-%m-%d %H:%M')) if s.last else 'never backed up':<34} "
             f"{_count(s.counts.get('save', 0), 'save'):<10}{'  (this handheld)' if s.own else ''}" for s in found]
    out(args, [s.to_dict() for s in found], "\n".join(lines) or "No backups in the library yet.")
    return 0


def _cmd_restore(session, args, out) -> int:
    from_set = other_set(session, args.source) if args.source else None
    package = None
    if args.emulator:
        if from_set is None:
            package = next((e.package for e in overview(session) if e.label.lower() == args.emulator.lower()), None)
        else:
            held = {i.source for i in set_items(session, from_set)}
            package = next((s.package for s in sources(session)
                            if s.label.lower() == args.emulator.lower() and s.id in held), None)
        if package is None:
            raise SavesError(f"No emulator with saves is called {args.emulator}.")
    if not (args.keys or args.emulator or args.all):
        raise SavesError("Name saves, or use --emulator NAME or --all.")
    check_run(args.at)
    if args.keys:
        _items(session, args.keys, from_set)
    if not args.yes:
        what = ", ".join(args.keys) if args.keys else (f"all saves of {args.emulator}" if package else "all saves")
        when = f"as of {args.at}" if args.at else "from the newest copies"
        where = f" of {from_set.name}" if from_set else ""
        print(f"This would restore {what}{where} {when}. The saves on the handheld now are backed up first.\n"
              "Run again with --yes to do this.")
        return 2
    if args.keys:
        return _restore_out(session, args, out, restore(session, args.keys, args.at, from_set))
    return _restore_out(session, args, out, restore_all(session, package, args.at, from_set))


def _status(session, args, out) -> int:
    last = last_backup(session)
    read_game_ids(session)                  # asked for: links saves named by a game ID to their game
    found = overview(session)
    data = {"last_backup": last.stamp if last else None,
            "skipped": len(last.detail.get("skipped", [])) if last else 0,
            "library_online": bool(session.library.online),
            "emulators": [{
                "package": e.package, "label": e.label, "status": e.status, "reason": e.reason,
                "advice": e.advice, "unreadable": e.unreadable, "unreadable_files": e.blocked,
                "skipped": [list(x) for x in e.skipped],
                "items": [{"key": i.item.key, "title": i.title, "kind": i.kind, "shape": i.item.shape,
                           "game": "/".join(i.item.game) if i.item.game else None,
                           "changed": i.changed.isoformat() if i.changed else None,
                           "versions": i.versions, "size": i.size, "gone": i.gone} for i in e.items]}
                for e in found]}
    lines = [f"Last backed up: {datetime.strptime(last.stamp, STAMP):%Y-%m-%d %H:%M}" if last
             else "Saves have not been backed up yet."]
    if not session.library.online:
        lines.append(OFFLINE)
    for e in found:
        lines.append(e.headline)
        if e.advice:
            lines.append(f"    {e.advice}")
        for rel, why in e.skipped:
            lines.append(f"    not backed up: {rel} — {why}")
        for i in e.items:
            gone = "  (no longer on the handheld)" if i.gone else ""
            lines.append(f"    {i.title:<44} {i.kind:<12} {_count(i.versions, 'version'):<12} "
                         f"{human_size(i.size):>9}  {i.item.key}{gone}")
    out(args, data, "\n".join(lines))
    return 0
