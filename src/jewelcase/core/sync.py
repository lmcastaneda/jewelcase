"""Runs a sync plan as a journal of small steps that can be paused, resumed and re-checked."""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import catalog, convert, gamelist, systems
from .catalog import GameRow, Profile
from .db import JobStep, Stats, Store
from .device import Device, DeviceError, DeviceGone
from .library import Library, ToolMissing
from .plan import PlanItem, SyncPlan, prove_twin

REFRESH_ACTION = "com.es_de.frontend.intent.REFRESH_GAME_LISTS"
LOW_BATTERY = 10
LIBRARY_HEADROOM = 64 * 1024 * 1024
_TRANSFERS = ("push", "pull")
_SIZED = (*_TRANSFERS, "convert")           # steps whose progress is counted in bytes
_PHASES = {"unpacking": "Unpacking", "compressing": "Compressing", "verifying": "Checking"}
_DESTRUCTIVE = ("gamelist", "delete", "delete_art")
_PER_GAME = ("verify", "capture", "delete", "delete_art", "push", "convert", "cover")


class UnsafePath(Exception):
    """A step named a path outside the folders Jewelcase is allowed to change."""


class JobOpen(Exception):
    """An earlier sync for this device was never finished; resume or discard it first."""


class Busy(Exception):
    """Another Jewelcase window or command holds the library right now (a sync, a saves backup, a conversion)."""


class _Stop(Exception):
    def __init__(self, mode: str):
        self.mode = mode


@contextmanager
def job_lock(data_dir: Path):
    """One sync at a time per data folder, across windows and command-line runs."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / "sync.lock", "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy("Another Jewelcase window or command is busy with the library (syncing, backing up saves or converting). "
                       "Try again when it is done.") from None
        yield
    finally:
        handle.close()


@dataclass(frozen=True)
class Progress:
    job_id: int
    step_idx: int
    step_count: int
    kind: str
    label: str
    bytes_done: int
    bytes_total: int
    state: str      # running | waiting_device | waiting_emulator | low_battery | done | paused | cancelled
    message: str = ""


def steps_for(plan: SyncPlan, profile: Profile) -> list[dict]:
    """Copies to the library first, then removals system by system, then additions (those that are converted
    from an archive last, each made and then copied), then one refresh."""
    steps: list[dict] = []
    for item in plan.pulls:
        for name in item.files:
            steps.append({"kind": "pull", "system": item.row.system, "stem": item.row.stem,
                          "name": name, "size": item.row.sizes.get(name, 0)})
    by_system: dict[str, list[PlanItem]] = {}
    for item in plan.removes:
        by_system.setdefault(item.row.system, []).append(item)
    for system in sorted(by_system):
        items = by_system[system]
        for item in items:
            for name in item.files:
                steps.append({"kind": "verify", "system": system, "stem": item.row.stem,
                              "name": name, "size": item.row.sizes.get(name, 0)})
            steps.append({"kind": "capture", "system": system, "stem": item.row.stem,
                          "files": list(item.row.dev_unit.files)})
        games = {item.row.stem: list(item.files) for item in items}
        steps.append({"kind": "gamelist", "system": system, "games": games})
        for item in items:
            for name in item.files:
                steps.append({"kind": "delete", "system": system, "stem": item.row.stem, "name": name})
            steps.append({"kind": "delete_art", "system": system, "stem": item.row.stem})
        steps.append({"kind": "check", "system": system, "games": games})
    for item in sorted(plan.adds, key=lambda i: i.convert is not None):
        made = {}
        if item.convert:
            made = {"made": True}
            steps.append({"kind": "convert", "system": item.row.system, "stem": item.row.stem,
                          "files": list(item.files), "size": item.convert.work})
        for name in item.files:
            steps.append({"kind": "push", "system": item.row.system, "stem": item.row.stem,
                          "name": name, "size": item.row.sizes.get(name, 0), **made})
        if item.row.key in plan.covers:
            steps.append({"kind": "cover", "system": item.row.system, "stem": item.row.stem,
                          "files": list(item.files)})
    if plan.adds or plan.removes:
        steps.append({"kind": "refresh"})
    return steps


def remaining(steps: list[JobStep]) -> dict[str, list[str]]:
    """What an unfinished job still means to do, for showing before it is resumed."""
    out: dict[str, list[str]] = {"remove": [], "add": [], "copy": [], "cover": []}
    for s in steps:
        if s.state != "pending":
            continue
        label = f"{s.payload.get('system')}/{s.payload.get('stem')}"
        key = {"delete": "remove", "push": "add", "pull": "copy", "cover": "cover"}.get(s.kind)
        if key and label not in out[key]:
            out[key].append(label)
    return out


class Runner:
    def __init__(self, dev_provider: Callable[[], Device | None], library: Library, store: Store,
                 profile: Profile, serial: str, log_path: Path, data_dir: Path,
                 emulator_packages: set[str] = frozenset(),
                 on_progress: Callable[[Progress], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep, cache_dir: Path | None = None, art=None):
        self.art = art                  # core.art.Art: covers a sync may copy to ES-DE; None: none are copied
        self.dev_provider, self.library, self.store = dev_provider, library, store
        self.profile, self.serial = profile, serial
        self.log_path, self.data_dir = Path(log_path), Path(data_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else self.data_dir / "cache"
        self.emulator_packages = set(emulator_packages)
        self.on_progress = on_progress
        self._sleep = sleep
        self._stop: str | None = None
        self._root = profile.rom_root.rstrip("/")
        self._job = 0
        self._count = 0
        self._bytes_total = 0
        self._bytes_base = 0
        self._blocked_games: dict[tuple[str, str], str] = {}     # game -> why its removal stopped
        self._blocked_systems: set[str] = set()
        self._unconverted: set[tuple[str, str]] = set()
        self._game_files: dict[tuple[str, str], list[str]] = {}
        self._battery_checked = None
        self._esde_cache: dict[str, dict] = {}
        self._esde_stopped: str | None = None       # set once this run stopped ES-DE: its activity if in front

    # ------------------------------------------------------------ public
    def start(self, plan: SyncPlan) -> int:
        if self.store.open_job(self.serial) is not None:
            raise JobOpen("An earlier sync was not finished. Resume it or discard it first.")
        if self.art is not None:
            from . import covers
            plan = replace(plan, covers=covers.for_adds(plan, self.art, self.profile))
        return self.store.create_job(self.serial, steps_for(plan, self.profile))

    def copy_to_library(self, rows: list[GameRow]) -> int:
        pulls = [PlanItem(r, r.dev_unit.files, r.size) for r in rows if r.dev_unit]
        return self.start(SyncPlan(pulls=pulls))

    def stop(self, mode: str = "pause") -> None:
        """'pause' leaves the job open to resume later; 'cancel' closes it."""
        self._stop = mode

    def run(self, job_id: int, resumed: bool | None = None) -> str:
        """Run or resume a job. Raises Busy if another sync holds the lock."""
        with job_lock(self.data_dir):
            return self._run(job_id, resumed)

    def _run(self, job_id: int, resumed: bool | None) -> str:
        self._stop = None
        steps = self.store.job_steps(job_id)
        self._job, self._count = job_id, len(steps)
        if resumed is None:
            resumed = any(s.state != "pending" for s in steps)
        self._blocked_games, self._blocked_systems = {}, set()
        self._cover_listing: dict[str, set[str]] = {}       # ES-DE covers folder -> stems, listed once per run
        self._unconverted: set[tuple[str, str]] = set()      # games whose conversion failed: nothing of them is copied
        self._game_files = {(s.payload["system"], s.payload["stem"]): s.payload["files"]
                            for s in steps if s.kind == "capture"}
        for s in steps:
            if s.state == "failed":
                self._note_failure(s, s.message)
        self._log(f"--- job {job_id}: {sum(1 for s in steps if s.state == 'pending')} of {len(steps)} steps to do"
                  + (" (resumed)" if resumed else ""))
        if resumed:
            steps = self._prepare_resume(steps)
        self._bytes_total = sum(s.payload.get("size", 0) for s in steps if s.kind in _SIZED)
        self._bytes_base = sum(s.payload.get("size", 0) for s in steps
                               if s.kind in _SIZED and s.state != "pending")
        last = steps[-1] if steps else None
        try:
            for s in steps:
                if s.state == "pending":
                    last = s
                    self._run_step(s)
        except _Stop as stop:
            state = "cancelled" if stop.mode == "cancel" else "paused"
            if state == "cancelled":
                self._drop_partial(last)
                self.store.close_job(job_id, "cancelled")
            self._log(f"--- job {job_id} {state}")
            self._emit(last, state)
            return state
        self.store.close_job(job_id, "done")
        self._log(f"--- job {job_id} done")
        self._emit(last, "done")
        return "done"

    def _prepare_resume(self, steps: list[JobStep]) -> list[JobStep]:
        """A resumed job is an old decision: honour ticks changed since, and prove every twin again."""
        wanted = self.store.wanted(self.serial)
        pending = [s for s in steps if s.state == "pending"]
        removing = {(s.payload["system"], s.payload["stem"]) for s in pending if s.kind in ("delete", "delete_art")}
        adding = {(s.payload["system"], s.payload["stem"]) for s in pending if s.kind == "push"}
        dropped = {k: "you ticked this game again, so it stays" for k in removing if wanted.get(k) is not False}
        dropped.update({k: "you unticked this game again, so it was not added"
                        for k in adding if wanted.get(k) is not True})
        for s in steps:
            key = (s.payload.get("system"), s.payload.get("stem"))
            if s.kind in _PER_GAME and s.state == "pending" and key in dropped:
                self.store.set_step(self._job, s.idx, "skipped", dropped[key])
                self._log(f"{s.kind:<10} {self._label(s)}  -> skipped  {dropped[key]}")
            elif s.kind == "verify" and s.state == "done" and key in removing and key not in dropped:
                self.store.set_step(self._job, s.idx, "pending", "")       # check the twin again
        for key, why in dropped.items():
            if key in removing:
                self._blocked_games[key] = why
        return self.store.job_steps(self._job)

    # ------------------------------------------------------------ plumbing
    def _emit(self, step: JobStep | None, state: str, done: int = 0, message: str = "") -> None:
        if self.on_progress:
            self.on_progress(Progress(self._job, step.idx if step else 0, self._count,
                                      step.kind if step else "", self._label(step),
                                      self._bytes_base + done, self._bytes_total, state, message))

    @staticmethod
    def _label(step: JobStep | None) -> str:
        if not step:
            return ""
        p = step.payload
        return p.get("name") or p.get("stem") or p.get("system") or "Refreshing"

    def _log(self, line: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")

    def _pause_point(self) -> None:
        if self._stop:
            raise _Stop(self._stop)

    def _wait(self, seconds: float) -> None:
        self._sleep(seconds)
        self._pause_point()

    def _device(self, step: JobStep) -> Device:
        while True:
            dev = self.dev_provider()
            if dev is not None and dev.serial == self.serial:
                return dev
            self._emit(step, "waiting_device")
            self._wait(2)

    def _gates(self, dev: Device, step: JobStep) -> None:
        now = time.monotonic()
        if self._battery_checked is None or now - self._battery_checked > 60:
            while True:
                info = dev.info()
                if not (0 <= info.battery < LOW_BATTERY and info.power != "ac"):
                    break
                self._emit(step, "low_battery", message=f"Battery at {info.battery} %")
                self._wait(30)
            self._battery_checked = now
        if step.kind in _DESTRUCTIVE and self.emulator_packages:
            while dev.foreground_package() in self.emulator_packages:
                self._emit(step, "waiting_emulator")
                self._wait(5)

    def _note_failure(self, step: JobStep, message: str = "") -> None:
        p = step.payload
        if step.kind in ("verify", "pull", "capture", "delete"):
            self._blocked_games.setdefault((p.get("system"), p.get("stem")),
                                           "an earlier check for this game failed")
        elif step.kind == "gamelist":
            self._blocked_systems.add(p.get("system"))
        elif step.kind == "convert":
            self._unconverted.add((p.get("system"), p.get("stem")))

    def _run_step(self, step: JobStep) -> None:
        while True:
            self._emit(step, "running")
            self._pause_point()
            try:
                dev = self._device(step)
                self._gates(dev, step)
                state, message = getattr(self, "_do_" + step.kind)(dev, step.payload, step)
                break
            except DeviceGone:
                self._emit(step, "waiting_device")
                self._wait(2)
            except (DeviceError, UnsafePath, ToolMissing) as e:
                state, message = "failed", str(e)
                break
            except OSError as e:
                state, message = "failed", f"read error: {e.strerror or e}"
                break
        self.store.set_step(self._job, step.idx, state, message)
        if state == "failed":
            self._note_failure(step, message)
        if step.kind in _SIZED:
            self._bytes_base += step.payload.get("size", 0)
        self._log(f"{step.kind:<10} {self._label(step)}  -> {state}{'  ' + message if message else ''}")

    def _path(self, system: str, name: str) -> str:
        for part in (system, name):
            if not part or part in (".", "..") or "/" in part or "\x00" in part:
                raise UnsafePath(f"refusing to touch {system!r}/{name!r}: outside the ROM folders")
        return f"{self._root}/{system}/{name}"

    def _blocked(self, p: dict) -> tuple[str, str] | None:
        why = self._blocked_games.get((p.get("system"), p.get("stem")))
        if why:
            return "skipped", why
        if p.get("system") in self._blocked_systems:
            return "skipped", "the game list for this system could not be updated"
        return None

    def _protected(self, system: str) -> set[str]:
        """Files of games in this job that are staying after all: never delete them through another game."""
        return {n for (sys_, stem), files in self._game_files.items()
                if sys_ == system and (sys_, stem) in self._blocked_games for n in files}

    def _drop_partial(self, step: JobStep | None) -> None:
        if step and step.kind in ("push", "cover"):
            try:
                dev = self.dev_provider()
                if dev is not None:
                    if step.kind == "cover":
                        from . import covers
                        part = self.art and covers.part_path(self.profile, self.art, step.payload["system"],
                                                             step.payload["stem"])
                        if part:
                            dev.remove(part)
                    else:
                        dev.remove(self._path(step.payload["system"], step.payload["name"]) + ".part")
            except (DeviceGone, DeviceError, UnsafePath):
                pass

    def _transfer_progress(self, step: JobStep):
        def report(done: int) -> None:
            self._emit(step, "running", done)
            self._pause_point()
        return report

    def _remember_device_hash(self, dev: Device, system: str, name: str, md5: str) -> None:
        st = dev.stat(self._path(system, name))
        if st is not None:
            self.store.set_dev_hash(self.serial, system, name, st.size, st.mtime, md5)

    # ------------------------------------------------------------ adding
    def _do_convert(self, dev: Device, p: dict, step: JobStep):
        """Make a game's files from its archive, in the library beside the archive."""
        system, stem, files = p["system"], p["stem"], list(p["files"])
        for name in files:
            self._path(system, name)
        if not self.library.online:
            return "failed", "library offline"
        there = [n for n in files if self.library.has(system, n) or self.library.file_path(system, n).exists()]
        if len(there) == len(files):
            for name in files:
                if not self.library.has(system, name):
                    self.library.add_file(system, name)
            return "done", "already converted"
        if there:
            return "failed", f"The library already has a file named {there[0]}."
        conv = convert.look(self.library, system, stem)
        if conv is None or conv.problem or list(conv.files) != files:
            return "failed", "The archive is not what it was when the sync was reviewed."

        def report(phase: str, done: int) -> None:
            self._emit(step, "running", done, _PHASES.get(phase, ""))
            self._pause_point()

        try:
            convert.run(self.library, conv, self.cache_dir, report)
        except convert.ConvertError as e:
            return "failed", str(e)
        return "done", ""

    def _do_push(self, dev: Device, p: dict, step: JobStep):
        system, name = p["system"], p["name"]
        remote = self._path(system, name)
        if p.get("made") and ((system, p.get("stem")) in self._unconverted or not self.library.has(system, name)):
            return "skipped", "it could not be converted"
        want = self.library.md5(system, name)
        if dev.exists(remote):
            if dev.md5(remote) == want:
                self._remember_device_hash(dev, system, name, want)
                return "done", "already on the device"
            return "failed", "a different file with this name is already on the device"
        dev.mkdir(f"{self._root}/{system}")
        part = remote + ".part"
        for _ in range(2):
            try:
                dev.push(self.library.file_path(system, name), part, self._transfer_progress(step))
            except DeviceError:
                dev.remove(part)        # the device is still there: do not leave half a file behind
                raise
            dev.chmod(part, "666")
            if dev.md5(part) == want:
                dev.rename(part, remote)
                self._remember_device_hash(dev, system, name, want)
                return "done", ""
            dev.remove(part)
        return "failed", "the copy on the device did not match the library file (tried twice)"

    def _do_cover(self, dev: Device, p: dict, step: JobStep):
        """A game just added gets its cover in ES-DE's folder, so it is not bare there. Never fails the sync."""
        from . import covers
        system = p["system"]
        if self.art is None:
            return "skipped", "no covers are kept here"
        if not all(dev.exists(self._path(system, n)) for n in p["files"]):
            return "skipped", "the game was not added"
        if any(o.state != "done" for o in self.store.job_steps(self._job)
               if o.kind == "push" and (o.payload.get("system"), o.payload.get("stem")) == (system, p["stem"])):
            return "skipped", "the game was not copied completely"
        return covers.copy_cover(dev, self.profile, self.art, system, p["stem"], known=self._cover_listing)

    # ------------------------------------------------------------ copying to the library
    def _do_pull(self, dev: Device, p: dict, step: JobStep):
        system, name, size = p["system"], p["name"], p.get("size", 0)
        remote = self._path(system, name)
        if not self.library.online:
            return "failed", "library offline"
        dest = self.library.file_path(system, name)
        if self.library.has(system, name) or dest.exists():
            if not self.library.has(system, name):
                self.library.add_file(system, name)     # on disk but not scanned yet: never overwrite it
            twin = prove_twin(dev, self.library, self.store, self.profile, system, name)
            if twin.ok:
                return "done", "already in the library"
            return "failed", "the library already has a different file with this name"
        if self.library.free_bytes() < size + LIBRARY_HEADROOM:
            return "failed", "the library disk is full"
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            dev.pull(remote, part, self._transfer_progress(step))
            with open(part, "rb") as f:
                os.fsync(f.fileno())            # a failing disk must show up before the copy is trusted
            from .device import md5_file
            got = md5_file(part)
            if got != dev.md5(remote):
                return "failed", "the copy in the library did not match the device file"
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)
        self.library.add_file(system, name, got)
        self._remember_device_hash(dev, system, name, got)
        return "done", ""

    # ------------------------------------------------------------ removing
    def _do_verify(self, dev: Device, p: dict, step: JobStep):
        blocked = self._blocked(p)
        if blocked:
            return blocked
        self._path(p["system"], p["name"])
        twin = prove_twin(dev, self.library, self.store, self.profile, p["system"], p["name"], stem=p.get("stem"))
        return ("done", twin.how) if twin.ok else ("failed", twin.reason)

    def _gamelist_paths(self, system: str) -> list[str]:
        paths = []
        if self.profile.gamelists_dir:
            for esde in dict.fromkeys((systems.get(system).esde, system)):
                paths.append(f"{self.profile.gamelists_dir.rstrip('/')}/{esde}/gamelist.xml")
        paths.append(self._path(system, "gamelist.xml"))
        return paths

    def _do_capture(self, dev: Device, p: dict, step: JobStep):
        blocked = self._blocked(p)
        if blocked:
            return blocked
        system, stem = p["system"], p["stem"]
        if system not in self._esde_cache:
            entries = {}
            for path in self._gamelist_paths(system)[:-1]:
                if dev.exists(path):
                    entries.update(gamelist.parse(dev.read_bytes(path).decode("utf-8", "replace")))
            self._esde_cache[system] = entries
        mine = [e for f, e in self._esde_cache[system].items() if f in p["files"]]
        if mine:
            last = max((e.last_played for e in mine if e.last_played), default=None)
            self.store.upsert_stats(self.serial, system, stem, Stats(
                sum(e.plays for e in mine), sum(e.seconds for e in mine), last, mine[0].name))
        art = self._path(system, "images") + f"/{stem}.png"
        if "/" not in stem and self.library.online and dev.exists(art):
            dest = self.library.file_path(system, "images") / f"{stem}.png"
            if not dest.exists():
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    part = dest.with_name(dest.name + ".part")
                    dev.pull(art, part)
                    part.replace(dest)
                except (OSError, DeviceError) as e:
                    self._log(f"           box art for {stem} was not saved: {e}")
        return "done", ""

    def _strip(self, dev: Device, system: str, names: set[str], tag: str) -> int:
        removed_total = 0
        for path in self._gamelist_paths(system):
            if not dev.exists(path):
                continue
            raw = dev.read_bytes(path)
            text = raw.decode("utf-8", "surrogateescape")
            new, removed = gamelist.drop(text, names)
            if not removed:
                continue
            backup = self.data_dir / "backups" / self.serial / f"job-{self._job}{tag}" / \
                path.strip("/").replace("/", "__")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(raw)
            dev.write_bytes(path, new.encode("utf-8", "surrogateescape"))
            removed_total += len(removed)
        return removed_total

    def _live_names(self, p: dict) -> set[str]:
        system = p["system"]
        return {n for stem, files in p["games"].items()
                if (system, stem) not in self._blocked_games for n in files} - self._protected(system)

    def _do_gamelist(self, dev: Device, p: dict, step: JobStep):
        names = self._live_names(p)
        if not names:
            return "skipped", "nothing to remove in this system"
        n = self._strip(dev, p["system"], names, "")
        return "done", f"removed {n} entries"

    def _do_delete(self, dev: Device, p: dict, step: JobStep):
        path = self._path(p["system"], p["name"])
        blocked = self._blocked(p)
        if blocked:
            return blocked
        if p["name"] in self._protected(p["system"]):
            return "skipped", "a game that is staying still uses this file"
        if not self.library.online:
            return "failed", "the library is not available any more, so nothing else was removed"
        dev.remove(path)
        self.store.forget_dev_hash(self.serial, p["system"], p["name"])
        return "done", ""

    def _do_delete_art(self, dev: Device, p: dict, step: JobStep):
        blocked = self._blocked(p)
        if blocked:
            return blocked
        if "/" in p["stem"]:
            raise UnsafePath(f"refusing to touch art for {p['stem']!r}")
        dev.remove(self._path(p["system"], "images") + f"/{p['stem']}.png")
        return "done", ""

    def _do_check(self, dev: Device, p: dict, step: JobStep):
        if p["system"] in self._blocked_systems:
            return "skipped", "the game list for this system could not be updated"
        left = [n for n in sorted(self._live_names(p)) if dev.exists(self._path(p["system"], n))]
        if left:
            return "failed", "still on the device: " + ", ".join(left)
        return "done", ""

    # ------------------------------------------------------------ finishing
    def _do_refresh(self, dev: Device, p: dict, step: JobStep):
        from . import esde              # here, not at the top: esde uses this module's lock
        if dev.has_apps:                # a card has no ES-DE running on it: nothing to tell, nothing to restart
            dev.broadcast(REFRESH_ACTION)
            self._sleep(2)
        done = [s for s in self.store.job_steps(self._job) if s.state == "done"]
        notes: list[str] = []
        present = bool(self.profile.gamelists_dir) and dev.has_apps
        try:
            defs = esde.load(dev, self.cache_dir) if present else None
        except DeviceError:
            defs = None
        # ES-DE reads its game lists and folders only when it starts, so it is restarted: stopped here,
        # started again at the end. Not while a game is running: that would cut the play session short.
        # This step is run again from the top when the device drops, so what was stopped is remembered.
        if present and self._esde_stopped is None and any(s.kind in ("push", "gamelist", "delete") for s in done):
            if esde.game_running(dev, defs, self.emulator_packages):
                notes.append("A game is running, so ES-DE was not restarted. "
                             "It shows the changes the next time it starts.")
            else:
                self._esde_stopped = dev.stop_app(esde.PACKAGE)
        try:
            self._after_esde_stopped(dev, done, defs, notes)
        except DeviceGone:
            raise                       # the step is retried; ES-DE is started at the end of that
        except Exception:
            self._start_esde(dev)
            raise
        if self._esde_stopped is not None:
            notes.append("ES-DE was restarted" if self._esde_stopped
                         else "ES-DE shows the changes when it is next opened")
            self._start_esde(dev)
        catalog.refresh_device(dev, self.profile, self.store, self.library.systems())
        rows = {r.key: r for r in catalog.build_rows(self.library, self.store, self.serial)}
        for key, want in self.store.wanted(self.serial).items():
            row = rows.get(key)
            if row is None or row.on_device == want:
                self.store.set_wanted(self.serial, key[0], key[1], None)
        return "done", ". ".join(notes)

    def _start_esde(self, dev: Device) -> None:
        if self._esde_stopped:
            dev.start_activity(self._esde_stopped)
        self._esde_stopped = None       # only once it is started: a drop before that is retried

    def _after_esde_stopped(self, dev: Device, done: list, defs, notes: list[str]) -> None:
        from . import esde
        for s in done:
            if s.kind == "gamelist":
                again = self._strip(dev, s.payload["system"], self._live_names(s.payload), "-again")
                if again:
                    self._log(f"           {again} removed entries reappeared in the {s.payload['system']} "
                              "game list after the refresh; removed them again")
        arrived = sorted({s.payload["system"] for s in done if s.kind == "push"})
        if arrived and defs is not None:
            try:
                listed = esde.ensure_listed(dev, self.profile, defs, arrived,
                                            self.data_dir / "backups" / self.serial)
            except (DeviceError, OSError) as e:
                listed = []
                self._log(f"           ES-DE's systems file could not be updated: {e}")
            if listed:
                notes.insert(0, f"ES-DE now also looks for {', '.join(listed)} games")
