"""Owns the session for the window: loads state, watches for the device, runs jobs off the UI thread."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.catalog import GameRow
from ..core.device import DeviceInfo, Storage
from ..core.inbox import Inbox, InboxError, import_items, undo_import
from ..core.plan import SyncPlan
from ..core.session import Session
from .workers import run_async

COVERS_RETRY_AFTER = 3600      # seconds a failed automatic download run waits before asking again


class Controller(QObject):
    rows_changed = Signal()         # rows, plan, device info or storage changed
    job_progress = Signal(object)   # core.sync.Progress
    import_progress = Signal(object)    # core.inbox.ImportProgress, or None when no import is running
    job_finished = Signal(str)      # done | paused | cancelled | error text
    message = Signal(str)
    toast = Signal(str, object, object)     # text, action label or None, callback or None
    quit_requested = Signal()               # the window is closed and nothing is left to do
    inbox_changed = Signal()
    emulators_changed = Signal()
    saves_changed = Signal()        # a backup or restore started or ended
    art_changed = Signal()          # covers or system images arrived from the handheld
    busy_changed = Signal()         # an import, an undo or a conversion started or ended

    BACKUP_EVERY_S = 3600           # saves are backed up again this long after the last full backup
    BACKUP_RETRY_S = 300            # and not sooner than this after an automatic backup that failed

    @property
    def inbox_busy(self) -> bool:
        """An import, an undo of one, or a conversion of games the library has is running."""
        return self._inbox_busy

    @inbox_busy.setter
    def inbox_busy(self, busy: bool) -> None:
        changed = bool(busy) != getattr(self, "_inbox_busy", None)
        self._inbox_busy = bool(busy)
        if changed:
            self.busy_changed.emit()

    def __init__(self, session: Session, poll: bool = True):
        super().__init__()
        self.session = session
        self.rows: list[GameRow] = []
        self._ids_busy = False
        self.plan: SyncPlan = SyncPlan()
        self.loaded = False
        self.job_running = False
        self.runner = None
        self.last_result: dict | None = None
        self.last_error = ""
        self.desktop = False                # True in the real app: notifications and launcher progress
        self.finish_in_background = False   # the window was closed while a sync, an import or a conversion was running
        self._launcher_at = 0.0
        self.job_progress.connect(self._watch_progress)
        self.inbox = Inbox(session.cfg.inbox)
        self.inbox_items: list = []
        self.art = session.art      # pictures ES-DE has, copied here to show them
        self._art_busy = False
        self._covers_busy = False
        self._covers_stop = False                       # set when the window closes: a download run ends
        self._covers_failed_at: float | None = None     # when a download run last ended on a network failure
        from ..core import covers
        self.cover_get = covers.net_get                 # where covers are downloaded from; tests swap it
        self.inbox_busy = False
        self.emulators: list = []
        self.emu_busy = False
        self.emu_message = ""
        self.esde_states: list | None = None        # core.esde.SystemState per system; None without ES-DE
        self.bios_cache: tuple | None = None        # (device key, results) of the last BIOS check: once per connection
        self._emu_serial = None
        self._refreshing = False
        self._poll_seen = None              # what the last poll saw plugged in or mounted
        self._folder_busy = False           # a games-folder change is reading the device
        self._refresh_pending = False
        self._result_restored = False
        self.auto_backup = False            # True in the real app: saves are backed up without being asked
        self.saves_busy = False             # a backup or a restore is running
        self.saves_auto = False             # ... and it was started automatically
        self.saves_restoring = False
        self.saves_results: list = []       # outcomes of this session's backups and restores, oldest first
        self.restore_failed: list = []      # (save key, reason) from the last restore
        self._saves_stop = False
        self._after_saves = None            # what waits for the running backup to let go
        self._saves_failed_at = 0.0
        self._saves_checked_at = 0.0
        self._seen_connected = False        # a device appearing is a reason to back up, whatever the clock says
        self.quit_when_restored = False     # the window was closed during a restore
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)
        if poll:
            self._timer.start()

    # ------------------------------------------------------------ state
    @property
    def connected(self) -> bool:
        return self.session.device is not None

    @property
    def can_tick(self) -> bool:
        return self.session.serial is not None and not self.job_running

    @property
    def info(self) -> DeviceInfo | None:
        return self.session.info()

    def storage(self, which: str = "card") -> Storage | None:
        return self.session.storage(which)

    @property
    def problem(self) -> str:
        return self.session.problem

    def _reload(self) -> None:
        self._restore_last_result()
        self.rows = self.session.rows()
        self.plan = self.session.plan(self.rows)
        self.loaded = True
        if not self.connected and self.emulators:
            self.emulators = []
            self.esde_states = None
            self.emulators_changed.emit()
        elif self.connected and self.session.device.has_apps and self._emu_serial != self.session.serial \
                and not self.emu_busy:
            self.load_emulators()
        self.rows_changed.emit()
        self.inbox_changed.emit()

    def reload(self) -> None:
        """Rebuild rows and the plan from what is already known (no device or disk scan)."""
        self._reload()

    def restore_wants(self, previous: dict) -> None:
        """Undo for ticks: put each game back to the tick it had."""
        if self.job_running:
            self.toast.emit("Ticks cannot change while a sync is running", None, None)
            return
        self.set_want([k for k, want in previous.items() if want], True)
        self.set_want([k for k, want in previous.items() if not want], False)

    def _restore_last_result(self) -> None:
        """After a restart, the last sync's failures are still worth showing until they have been seen."""
        s = self.session
        if self._result_restored or not s.serial:
            return
        self._result_restored = True
        last = s.store.last_job(s.serial)
        if self.last_result is None and last and last["state"] == "done" \
                and s.store.meta(s.serial, "result_seen") != last["id"]:
            failed = [{"label": st.payload.get("name") or st.payload.get("stem") or st.payload.get("system", ""),
                       "kind": st.kind, "message": st.message}
                      for st in s.store.job_steps(last["id"]) if st.state == "failed"]
            if failed:
                self.last_result = {"state": "done", "job": last["id"], "failed": failed, "added": 0, "removed": 0}

    def acknowledge_result(self) -> None:
        if self.last_result is not None:
            self.last_result["seen"] = True
            if self.session.serial and self.last_result.get("job"):
                self.session.store.set_meta(self.session.serial, "result_seen", self.last_result["job"])
        self.last_error = ""
        self._reload()

    def _load(self) -> None:
        self.session.connect()
        self.session.refresh()
        if self.inbox.folder != self.session.cfg.inbox:
            self.inbox = Inbox(self.session.cfg.inbox)
        self.inbox_items = self.inbox.scan()

    def refresh_blocking(self) -> None:
        self._load()
        self._reload()
        if self._fetch_art():
            self.art_changed.emit()

    # ------------------------------------------------------------ pictures
    def _fetch_art(self) -> int:
        """Copy the covers and system images ES-DE has for the listed systems. Returns how many changed."""
        s = self.session
        if s.device is None or s.profile is None or not s.device.has_apps:
            return 0
        return self.art.refresh(s.device, s.profile, sorted({r.system for r in self.rows}), s.cache_dir,
                                changed=self.art_changed.emit)

    def _download_covers(self):
        """Download covers for games that have none. Returns a core.covers.Result."""
        from ..core import covers
        result = covers.fetch_missing(self.art, covers.games_of(self.rows), self.cover_get,
                                      changed=self.art_changed.emit, stop=lambda: self._covers_stop)
        self._covers_failed_at = time.time() if result.failed else None
        return result

    def stop_covers(self) -> None:
        """The window is closing: a running download run stops after the game it is on."""
        self._covers_stop = True

    def refresh_art(self) -> None:
        """Look for new pictures on the handheld, and download missing covers when that is switched on,
        off the UI thread."""
        recently_failed = (self._covers_failed_at is not None
                           and time.time() - self._covers_failed_at < COVERS_RETRY_AFTER)
        auto = self.session.cfg.download_covers and not self._covers_busy and not recently_failed
        if self._art_busy or not (self.connected or auto):
            return
        self._art_busy = True
        self._covers_busy = self._covers_busy or auto
        if auto:
            self._covers_stop = False

        def work() -> int:
            count = self._fetch_art()
            if auto:
                try:
                    count += self._download_covers().arrived
                finally:
                    self._covers_busy = False
            return count

        def done(count) -> None:
            self._art_busy = False
            if count:
                self.art_changed.emit()

        def failed(_error) -> None:
            self._art_busy = False
            if auto:
                self._covers_busy = False
        run_async(work, done, failed)

    def set_download_covers(self, on: bool) -> None:
        """The preference: applies at once, and fetches right away when switched on."""
        self.session.cfg.download_covers = bool(on)
        self.session.save_config()
        if on:
            self.refresh_art()

    def download_missing_covers(self) -> None:
        """One-off: download covers for games that have none, whatever the preference says."""
        if self._covers_busy:
            self.toast.emit("Covers are already being downloaded", None, None)
            return
        from ..core import covers
        from ..core.fmt import plural
        self._covers_busy = True
        self._covers_stop = False

        def done(result) -> None:
            self._covers_busy = False
            if result.failed:
                text = f"Could not reach the thumbnail collection ({result.failed})"
            else:
                text = f"{plural(result.arrived, 'cover')} downloaded, {plural(result.none, 'game')} without one"
            self.toast.emit(text, None, None)
            if result.arrived:
                self.art_changed.emit()

        def failed(error) -> None:
            self._covers_busy = False
            self.toast.emit(f"Could not download covers ({error})", None, None)
        run_async(self._download_covers, done, failed)

    def refresh(self) -> None:
        if self._refreshing or self.job_running or self._folder_busy:
            self._refresh_pending = True        # asked for while busy: done as soon as that ends
            return
        self._refreshing = True
        self._refresh_pending = False

        def done(_):
            self._reload()
            self._refresh_finished()
            appeared, self._seen_connected = self.connected and not self._seen_connected, self.connected
            self._maybe_backup(force=appeared)
            self.refresh_art()

        def failed(e):
            self.message.emit(str(e))
            self._reload()
            self._refresh_finished()
        run_async(self._load, done, failed)

    def _refresh_finished(self) -> None:
        self._refreshing = False
        if self._refresh_pending and not self.job_running:
            self.refresh()

    def _poll(self) -> None:
        """Notice a device being plugged in or pulled out."""
        if self._refreshing or self.job_running or self._folder_busy:
            return
        import time
        if time.monotonic() - self._saves_checked_at > 30:      # the hourly backup, checked now and then
            self._saves_checked_at = time.monotonic()
            self._maybe_backup()
        s = self.session
        was = s.device.serial if s.device else None
        problem = s.problem

        def check() -> bool:
            if s.demo_device is not None:
                present = getattr(s.demo_device, "online", True)
            else:
                from ..core import adb
                exe = s.adb_path if s.adb_enabled else None
                targets = adb.pick_targets(adb.list_targets(exe)) if exe else []
                on_card = s.device is not None and not s.device.has_apps
                transport = s.device.target.serial if s.device is not None and not on_card else None
                present = any(t.state == "device" and (transport is None or t.serial == transport)
                              for t in targets)
                # What is plugged in or mounted now. A change in it is a reason to look again; the same
                # state again is not, even when the last look found nothing usable in it (so the poll settles).
                seen = (frozenset(t.serial for t in targets if t.state == "device"), s.card_ready())
                before, self._poll_seen = self._poll_seen, seen
                if on_card:             # the card was taken out, or a handheld was plugged in
                    return not seen[1] or (before is not None and seen[0] != before[0]) \
                        or (before is None and bool(seen[0]))
                if was is None:
                    if seen != before:
                        return True     # a card was put in or a handheld appeared (handhelds are looked at first)
                    if not present:
                        s.connect()     # updates the "problem" text (unauthorised, no adb, ...)
                        return s.problem != problem or s.device is not None
                    return False
            return present != (was is not None)

        def done(changed: bool):
            if changed:
                self._refresh_pending = True
            self._refresh_finished()
        self._refreshing = True
        run_async(check, done, lambda e: done(False))

    # ------------------------------------------------------------ card or folder as the device
    def use_card(self, path) -> None:
        """Use a mounted card or folder as the device (None: stop using it)."""
        if self.job_running:
            self.toast.emit("The device cannot be changed while a sync is running", None, None)
            return
        self.session.set_card(path)
        self.refresh()

    # ------------------------------------------------------------ the games folder
    def games_folder_blocked(self) -> str:
        """Why the games folder cannot be changed right now, or ""."""
        if self.job_running:
            return "A sync is running, so the games folder cannot be changed."
        if self.open_job() is not None:
            return "A sync has not finished yet. Resume or discard it before changing the games folder."
        return ""

    def _changed_folder(self, before: tuple[str, str], text: str) -> None:
        """Show what changed, with Undo, and read the device again."""
        def undo() -> None:
            self._folder_work(lambda: self._set_back(before), lambda _p: self._after_folder_change(),
                              lambda e: self.toast.emit(str(e), None, None))
        self.toast.emit(text, "Undo", undo)
        self._after_folder_change()

    def _after_folder_change(self) -> None:
        self._emu_serial = None             # what the Emulators page worked out was for the old folder
        self.refresh()

    def _set_back(self, before: tuple[str, str]):
        from ..core import gamesfolder
        return (gamesfolder.set_folder(self.session, before[1]) if before[0] == "set"
                else gamesfolder.detect_again(self.session))

    def _folder_work(self, work, done, failed) -> None:
        """Run a games-folder change (it reads the device) off the UI thread. No refresh runs meanwhile."""
        self._folder_busy = True

        def finish(callback, value) -> None:
            self._folder_busy = False
            callback(value)
            if self._refresh_pending and not self._refreshing and not self.job_running:
                self.refresh()
        run_async(work, lambda r: finish(done, r), lambda e: finish(failed, e))

    def _folder_now(self) -> tuple[str, str]:
        from ..core import gamesfolder
        s = self.session
        return gamesfolder.source(s), s.profile.rom_root if s.profile else ""

    def _change_folder(self, work, text, done, failed) -> None:
        from ..core import gamesfolder
        if self.games_folder_blocked():
            raise gamesfolder.FolderError(self.games_folder_blocked())
        before = self._folder_now()

        def ok(profile) -> None:
            self._changed_folder(before, text(profile))
            if done:
                done()

        def bad(error) -> None:
            if failed:
                failed(error)
            else:
                self.toast.emit(str(error), None, None)
        self._folder_work(work, ok, bad)

    def set_games_folder(self, path: str, done=None, failed=None) -> None:
        """Use this folder on the device as the games folder, checked off the UI thread. `failed` gets the
        core's FolderError, with the reason in words (without it, that is shown as a toast). Raises FolderError
        at once when it cannot be changed right now."""
        from ..core import gamesfolder
        self._change_folder(lambda: gamesfolder.set_folder(self.session, path),
                            lambda p: f"The games folder is now {p.rom_root}", done, failed)

    def detect_games_folder(self, done=None, failed=None) -> None:
        """Forget the folder that was set and detect it again (see set_games_folder)."""
        from ..core import gamesfolder
        self._change_folder(lambda: gamesfolder.detect_again(self.session),
                            lambda p: f"Detected the games folder again: {p.rom_root}", done, failed)

    # ------------------------------------------------------------ ticks
    def set_want(self, keys: list[tuple[str, str]], want: bool) -> int:
        if not self.can_tick:
            return 0
        by_key = {r.key: r for r in self.rows}
        changed = 0
        for key in keys:
            r = by_key.get(key)
            if r is None or r.want == want:
                continue
            if want and not r.on_device and r.lib_unit is None and not r.archive:
                continue                # nothing that could be copied, and no archive to make it from
            self.session.store.set_wanted(self.session.serial, r.system, r.stem,
                                          None if want == r.on_device else want)
            changed += 1
        if changed:
            self._reload()
        return changed

    # ------------------------------------------------------------ emulators
    def _emu_work(self, work, message: str = "", busy_message: str = "", toast: str = "", undo=None) -> None:
        from ..core import emulators as emu
        dev = self.session.device
        if self.emu_busy or dev is None:
            return
        self.emu_busy = True
        self.emu_message = busy_message
        self.emulators_changed.emit()
        catalog = emu.load_catalog(self.session.user_catalog)
        previous = {r.entry.package: r for r in self.emulators}

        def run():
            note, ok, filled = message, True, []
            try:
                rows = work(dev, emu)
                if callable(message):
                    note = message()
            except emu.EmulatorError as e:
                rows, note, ok = None, str(e), False
            if rows is None:            # an action: re-list, keeping what the last update check found
                from dataclasses import replace
                rows = [replace(r, latest=previous[r.entry.package].latest,
                                apk_url=previous[r.entry.package].apk_url) if r.entry.package in previous else r
                        for r in emu.list_emulators(dev, catalog)]
                if ok and previous:
                    filled = self._fill_esde_gaps(previous, rows)
            return rows, note, ok, self._read_esde(), filled

        def done(result):
            self.emu_busy = False
            self.emulators, self.emu_message, ok, self.esde_states, filled = result
            self._emu_serial = dev.serial
            self.emulators_changed.emit()
            text, action = (toast if isinstance(toast, str) else self.emu_message), undo
            if filled:                  # an emulator just installed took over systems that had none
                text += ". " + "; ".join(f"{s.name} games now open with {label}" for s, label, _ in filled)
                action = lambda: self._undo_esde(filled)        # noqa: E731
            if ok and (toast or filled):
                self.toast.emit(text, "Undo" if action else None, action)

        def failed(e):
            self.emu_busy = False
            self.emu_message = f"That did not work: {e}"
            self.emulators_changed.emit()
        run_async(run, done, failed)

    # ------------------------------------------------------------ what ES-DE opens games with
    def _read_esde(self):
        """Per system, the emulator ES-DE launches (worker thread). None without a device or ES-DE."""
        from ..core import esde
        try:
            return esde.for_session(self.session)
        except Exception:               # whatever goes wrong here must not make the action before it look failed
            return None

    def _fill_esde_gaps(self, previous: dict, rows: list) -> list:
        """After an install: systems whose ES-DE emulator is missing go to the emulator that just arrived.
        A system that already works is left alone."""
        from ..core import esde
        arrived = [r.entry.package for r in rows if r.installed and r.entry.kind == "emulator"
                   and (r.entry.package not in previous or previous[r.entry.package].installed is None)]
        filled = []
        for package in arrived:
            try:
                filled += esde.apply_fixes(self.session, only=package)
            except Exception:           # the install itself worked; the page still shows the gap and the fix
                pass
        return filled

    def _esde_work(self, change, message, undo=None, toast: bool = True) -> None:
        from ..core import esde
        from ..core.device import DeviceError, DeviceGone
        from ..core.sync import Busy

        def work(dev, emu):
            try:
                change()
            except (esde.EsdeError, Busy) as e:
                raise emu.EmulatorError(str(e)) from None
            except (DeviceError, DeviceGone) as e:      # the page is read again after this, so it shows what is true
                raise emu.EmulatorError(f"The change stopped part-way ({e}). Check the table to see what ES-DE does now.") from None
            return None
        self._emu_work(work, message, "Changing what ES-DE opens games with…", toast=toast, undo=undo)

    def set_esde_emulator(self, system: str, label: str) -> None:
        """Have ES-DE open a system's games with the command `label`. ES-DE is restarted; Undo puts it back."""
        from ..core import esde
        state = next((s for s in self.esde_states or [] if s.system == system), None)
        if state is None or self.emu_busy:
            return
        before: dict = {}

        def change():
            before["label"] = esde.choose(self.session, system, label)[1] or state.launch.default

        self._esde_work(change, f"{state.name} games now open with {label}",
                        undo=lambda: self._esde_work(
                            lambda: esde.choose(self.session, system, before["label"]),
                            f"{state.name} games open with {before['label']} again", toast=False))

    def fix_esde(self) -> None:
        """Switch every system whose ES-DE emulator is not installed to one that is."""
        from ..core import esde
        done: list = []

        problems: list[str] = []

        def message() -> str:
            if len(done) == 1:
                text = f"{done[0][0].name} games now open with {done[0][1]}"
            elif done:
                text = f"ES-DE now opens the games of {len(done)} systems with installed emulators"
            else:
                text = "Nothing was changed"
            return " ".join([text + ("." if problems else ""), *problems]).strip()

        self._esde_work(lambda: done.extend(esde.apply_fixes(self.session, problems=problems)), message,
                        undo=lambda: self._undo_esde(done))

    def _undo_esde(self, changes: list) -> None:
        from ..core import esde

        def change():
            esde.choose_many(self.session, {state.system: before or state.launch.default
                                            for state, _, before in changes})
        self._esde_work(change, "ES-DE opens those games with what it had before", toast=False)

    def load_emulators(self) -> None:
        catalog_path = self.session.user_catalog
        self._emu_work(lambda dev, emu: emu.list_emulators(dev, emu.load_catalog(catalog_path)))

    def check_emulator_updates(self) -> None:
        info = self.info
        catalog_path = self.session.user_catalog

        def work(dev, emu):
            return emu.check_updates(emu.list_emulators(dev, emu.load_catalog(catalog_path)),
                                     info.sdk if info else None)
        self._emu_work(work, "Checked each project's latest release.", "Checking for updates…")

    def emulator_action(self, action, message: str, busy_message: str = "", undo=None,
                        backup_first: tuple[str, str, bool] | None = None) -> None:
        """Run one change on the device, then say what happened (a toast, with Undo when given).

        backup_first = (package, name, must): back up that emulator's saves before the change. With
        `must`, the change does not happen when the backup fails; without, the failure is only reported.
        """
        notes: list[str] = []
        if backup_first and self.saves_busy and not self.saves_restoring:
            self._saves_stop = True             # a running backup lets go so this one can start

        def work(dev, emu):
            if backup_first:
                from ..core import saves
                package, name, must = backup_first
                ok, reason = saves.backup_before_removal(self.session, package)
                if not ok and must:
                    raise emu.EmulatorError(
                        f"{name} was not removed: its saves could not be backed up first ({reason}).")
                if not ok:
                    notes.append(f"Its saves could not be backed up first ({reason}).")
            action(dev)
            return None
        self._emu_work(work, lambda: " ".join([message, *notes]), busy_message, toast=True, undo=undo)

    # ------------------------------------------------------------ saves
    @property
    def saves_runs(self) -> int:
        return len(self.saves_results)

    def saves_overview(self) -> list:
        from ..core import saves
        return saves.overview(self.session, self.rows)

    def read_game_ids(self, keys=None) -> None:
        """Read, away from the UI thread, the IDs of games that saves named by an ID may belong to.

        Each game file is read once; when something new was found the lists that show saves refresh.
        """
        from ..core import saves
        if self._ids_busy:
            return
        self._ids_busy = True
        rows = list(self.rows)

        def done(count):
            self._ids_busy = False
            if count:
                self.saves_changed.emit()
        run_async(lambda: saves.read_game_ids(self.session, rows, keys), done, lambda e: done(0))

    def unreadable_saves(self, package: str) -> str:
        """What of this emulator could not be backed up at all ("10 save folders"), or ""."""
        from ..core import saves
        return saves.cannot_back_up(self.session, package, self.rows)

    def _maybe_backup(self, force: bool = False) -> None:
        """Start an automatic backup when one is due and nothing else is going on.

        `force`: the handheld has just been connected, so back up even if the last one is recent.
        """
        import time
        from datetime import datetime
        from ..core import saves
        from ..core.savebackup import STAMP
        if (not self.auto_backup or self.saves_busy or self.job_running or self.emu_busy or self.inbox_busy
                or not self.connected or self.session.is_card or self.finish_in_background):
            return
        if self._saves_failed_at and time.monotonic() - self._saves_failed_at < self.BACKUP_RETRY_S:
            return
        if not self.session.library.online:
            return
        last = saves.last_backup(self.session)
        if not force and last and \
                (datetime.now() - datetime.strptime(last.stamp, STAMP)).total_seconds() < self.BACKUP_EVERY_S:
            return
        self.backup_saves(auto=True)

    def check_saves_before_removal(self, package: str, then) -> None:
        """Look at the device for saves and settings of this emulator that cannot be read;
        `then(saves_text, settings_text)` gets what was found."""
        from ..core import saves
        run_async(lambda: saves.unreadable_now_detail(self.session, package), lambda found: then(*found),
                  lambda e: then("", ""))

    def dismiss_restore_failures(self) -> None:
        self.restore_failed = []
        self.saves_changed.emit()

    def _start_saves(self, auto: bool, restoring: bool = False) -> None:
        self.saves_busy, self.saves_auto, self.saves_restoring, self._saves_stop = True, auto, restoring, False
        self.saves_changed.emit()

    def _end_saves(self, result) -> None:
        self.saves_busy = self.saves_restoring = False
        self.saves_results.append(result)
        self.saves_changed.emit()
        waiting, self._after_saves = self._after_saves, None
        if self.quit_when_restored:             # closed during a restore: it is done, so the app can go
            self.quit_when_restored = False
            self.quit_requested.emit()
        elif waiting:
            waiting()

    def _give_way(self, action) -> bool:
        """A running backup stops after its current file so `action` can run; a restore is left to finish."""
        if not self.saves_busy:
            return False
        if not self.saves_restoring:
            self._saves_stop = True
        self._after_saves = action
        return True

    def backup_saves(self, auto: bool = False) -> bool:
        """Copy changed saves to the library. Automatic runs are quiet; one asked for reports problems."""
        import time
        from ..core import saves
        from ..core.sync import Busy
        if self.saves_busy or self.job_running or not self.connected:
            return False
        self._start_saves(auto)

        def work():
            try:
                return saves.backup(self.session, should_stop=lambda: self._saves_stop)
            except (saves.SavesError, Busy) as e:
                return e

        def done(result):
            failed = isinstance(result, Exception) or result.stopped in ("device", "library")
            if auto:
                self._saves_failed_at = time.monotonic() if failed else 0.0
            elif isinstance(result, Exception):
                self.toast.emit(str(result), None, None)
            elif failed:
                self.toast.emit(saves.run_text(result), None, None)
            self._end_saves(result)
        run_async(work, done, done)
        return True

    def _restore(self, call, total: int, single_title: str = "") -> None:
        """`total`: how many items were asked for; 0 when only the restore itself finds out."""
        from ..core import saves
        from ..core.fmt import plural
        from ..core.sync import Busy
        self._start_saves(auto=False, restoring=True)

        def work():
            try:
                return call()
            except (saves.SavesError, Busy) as e:
                return e

        def done(result):
            if isinstance(result, Exception):
                self.restore_failed = []
                self._end_saves(result)
                self.toast.emit(str(result), None, None)
                return
            self.restore_failed = list(result.failed)
            restored, at = list(result.restored), result.undo_run
            undoable = list(result.undoable)    # only what the safety backup captured can be put back
            undo = (lambda: self.restore_saves(undoable, at=at)) if undoable else None
            asked = total or len(restored) + len(result.failed)
            if single_title:
                text = f"{saves.kind_word(self.session, restored[0])} restored for {single_title}" if restored \
                    else result.failed[0][1]
            elif asked:
                noun = saves.restore_noun(self.session, [*restored, *(k for k, _ in result.failed)])
                text = f"{len(restored)} of {plural(asked, noun)} restored"
            else:
                text = "There was nothing to restore"
            self._end_saves(result)
            self.toast.emit(text, "Undo" if undo else None, undo)
        run_async(work, done, done)

    def _can_restore(self, again) -> bool:
        if self.job_running:
            self.toast.emit("Saves cannot be restored while a sync is running", None, None)
            return False
        if self._give_way(again):
            return False
        if not self.connected:
            self.toast.emit("Connect the handheld to restore saves", None, None)
            return False
        return True

    def restore_saves(self, keys: list[str], at: str | None = None, from_set=None) -> None:
        """Put saves back (as of run `at`, or their newest copies). The toast offers Undo.

        `from_set`: a backup set of another handheld to take the copies from.
        """
        from ..core import saves
        if not keys or not self._can_restore(lambda: self.restore_saves(keys, at, from_set)):
            return
        title = next((i.title for e in self.saves_overview() for i in e.items if i.item.key == keys[0]),
                     keys[0].split("/", 1)[-1])
        self._restore(lambda: saves.restore(self.session, keys, at, from_set), len(keys),
                      title if len(keys) == 1 else "")

    def restore_all_saves(self, package: str | None = None, at: str | None = None, from_set=None) -> None:
        """Every save, or one emulator's, as of run `at` (None: the newest copies)."""
        from ..core import saves
        if not self._can_restore(lambda: self.restore_all_saves(package, at, from_set)):
            return
        # another handheld's set is read by the restore itself, away from the UI thread
        total = 0 if from_set is not None else \
            sum(len(e.items) for e in self.saves_overview() if package is None or e.package == package)
        if from_set is not None or total:
            self._restore(lambda: saves.restore_all(self.session, package, at, from_set), total)

    # ------------------------------------------------------------ inbox
    def import_inbox(self, pairs: list) -> None:
        """pairs: (InboxItem, system folder). Moves each into the library, then rescans."""
        if self.inbox_busy or not pairs:
            return
        if self.job_running and self.session.cfg.convert_on_import:     # converting is a long job, like a sync
            self.toast.emit("Wait for the sync to finish before importing", None, None)
            return
        import time
        from ..core.sync import Busy, job_lock
        self.inbox_busy = True
        s = self.session
        shown = {"key": None, "at": 0.0, "held": None}

        def report(p):
            """Pass on each new file and phase at once, and otherwise at most ten reports a second."""
            now = time.monotonic()
            if (p.phase, p.file) != shown["key"] or now - shown["at"] >= 0.1:
                shown.update(key=(p.phase, p.file), at=now, held=None)
                self.import_progress.emit(p)
            else:
                shown["held"] = p

        def work():
            cache = s.cache_dir if s.cfg.convert_on_import else None
            try:
                if cache is None:
                    records, problems = import_items(pairs, s.library, s.store, s.serial, report, cache)
                else:                   # a conversion clears its work folder: only one job at a time may run
                    with job_lock(s.data_dir):
                        records, problems = import_items(pairs, s.library, s.store, s.serial, report, cache)
            except Busy as e:
                return [], [str(e)]
            if shown["held"] is not None:           # the last report, if it was held back
                self.import_progress.emit(shown["held"])
            self.inbox_items = self.inbox.scan()
            return records, problems

        def done(result):
            records, problems = result
            self.inbox_busy = False
            self.import_progress.emit(None)
            self._reload()
            unconverted = [r for r in records if r.convert_problem]
            notes = (["Not imported: " + "; ".join(problems)] if problems else []) + \
                [f"{r.stem} was imported, but could not be converted: {r.convert_problem}" for r in unconverted]
            self.message.emit(" ".join(notes))
            if records:
                if len(records) == 1:
                    text = (notes[-1] if unconverted else f"{records[0].stem} was imported")
                else:
                    text = f"{len(records)} games were imported"
                    if unconverted:
                        text += f", but {len(unconverted)} could not be converted"
                self.toast.emit(text, "Undo", lambda: self.undo_imports(records))
            self._after_inbox()
        run_async(work, done, lambda e: done(([], [str(e)])))

    def undo_imports(self, records: list) -> None:
        """Move imported files back to the downloads folder (refused for any that changed since)."""
        if self.job_running:
            self.toast.emit("Imports cannot be undone while a sync is running", None, None)
            return
        if self.inbox_busy:
            self.toast.emit("Still importing. Try Undo again in a moment", None, None)
            return
        self.inbox_busy = True
        s = self.session

        def work():
            problems, left, back = [], [], []
            for record in records:
                try:
                    left += undo_import(record, s.library, s.store, s.serial)
                except InboxError as e:
                    problems.append(str(e))
                else:
                    back.append(record.stem)
            self.inbox_items = self.inbox.scan()
            return problems, left, back

        def done(result):
            problems, left, back = result
            self.inbox_busy = False
            self._reload()
            kept = [f"{name} was left in the library because it has changed" for name in left]
            self.message.emit("; ".join(problems + kept))
            if problems:
                self.toast.emit("Not moved back: " + problems[0], None, None)
            elif kept:                  # the downloads are back; only what conversion made stayed, as it changed
                moved = f"{back[0]} was" if len(back) == 1 else f"{len(back)} games were"
                self.toast.emit(f"{moved} moved back. {'; '.join(kept)}", None, None)
            self._after_inbox()
        run_async(work, done, lambda e: done(([str(e)], [], [])))

    def _after_inbox(self) -> None:
        """An import or conversion ended: if the window was closed meanwhile, nothing is left to stay open for."""
        if self.finish_in_background and not self.job_running and not self.inbox_busy:
            self.finish_in_background = False
            self.quit_requested.emit()

    # ------------------------------------------------------------ converting what the library has
    def convertible(self, keys) -> list[tuple[tuple[str, str], str]]:
        """(game, form) for each of these games that has a disc image the library could make smaller:
        form is ".chd" or ".rvz"."""
        from ..core import convert
        library = self.session.library
        if not library.online:
            return []
        found, forms = [], {}
        for system, stem in keys:
            if system not in forms:
                forms[system] = library.forms(system)
            conv = convert.look_loose(library, system, stem, forms[system])
            if conv is not None and not conv.problem:
                found.append(((system, stem), conv.files[0][conv.files[0].rindex("."):]))
        return found

    def convert_games(self, keys: list) -> None:
        """Make a CHD (or RVZ) of the disc images the library has for these games, beside the originals.
        Runs in the background; the header shows how far it is, and a toast says how it went."""
        if not keys:
            return
        if self.job_running or self.inbox_busy:
            self.toast.emit("Wait for the sync or import to finish first", None, None)
            return
        import time
        from ..core import convert
        from ..core.inbox import ImportProgress
        from ..core.sync import Busy, job_lock
        self.inbox_busy = True
        s = self.session
        titles = {r.key: r.title for r in self.rows}
        shown = {"key": None, "at": 0.0}

        def work():
            try:
                with job_lock(s.data_dir):
                    todo = []
                    for key in keys:
                        conv = convert.look_loose(s.library, *key)
                        if conv is not None:
                            todo.append((key, conv))
                    total = sum(conv.work for _, conv in todo)
                    done, base = [], 0
                    for n, (key, conv) in enumerate(todo, 1):
                        name = titles.get(key, key[1])

                        def report(phase: str, at: int, name=name, n=n, base=base) -> None:
                            phase = "verifying_game" if phase == "verifying" else phase
                            now = time.monotonic()
                            if (phase, n) != shown["key"] or now - shown["at"] >= 0.1:
                                shown.update(key=(phase, n), at=now)
                                self.import_progress.emit(
                                    ImportProgress(phase, name, n, len(todo), min(total, base + at), total))
                        try:
                            convert.run(s.library, conv, s.cache_dir, report)
                            done.append((name, conv.files[0][conv.files[0].rindex(".") + 1:].upper()))
                        except convert.ConvertError as e:
                            done.append((name, str(e), True))
                        base += conv.work
                    return done
            except Busy as e:
                return str(e)

        def finish(result) -> None:
            self.inbox_busy = False
            self.import_progress.emit(None)
            self._reload()
            if isinstance(result, str):
                self.toast.emit(result, None, None)
                self._after_inbox()
                return
            made = [r for r in result if len(r) == 2]
            failed = [r for r in result if len(r) == 3]
            if len(result) == 1:
                text = (f"{made[0][0]} was converted to {made[0][1]}" if made
                        else f"{failed[0][0]} could not be converted: {failed[0][1]}")
            else:
                text = f"{len(made)} games were converted"
                if failed:
                    text += f". {failed[0][0]} could not be converted: {failed[0][1]}"
            self.toast.emit(text if result else "Nothing to convert", None, None)
            self._after_inbox()
        run_async(work, finish, lambda e: finish(f"Could not convert: {e}"))

    # ------------------------------------------------------------ jobs
    def _run_job(self, start, resumed: bool | None = None) -> None:
        if self._give_way(lambda: self._run_job(start, resumed)):
            return                              # starts as soon as the running backup has let go
        if self.inbox_busy:
            self.toast.emit("Wait for the import or conversion to finish before syncing", None, None)
            return
        if self.job_running or not self.connected:
            return
        self.job_running = True
        self.last_error = ""
        self.runner = self.session.runner(on_progress=self.job_progress.emit)
        runner = self.runner
        self.rows_changed.emit()            # ticks are locked while a job runs

        def work() -> str:
            return runner.run(start(runner), resumed)

        def done(state):
            failed = [{"label": st.payload.get("name") or st.payload.get("stem") or st.payload.get("system", ""),
                       "kind": st.kind, "message": st.message}
                      for st in self.session.store.job_steps(runner._job) if st.state == "failed"]
            steps = self.session.store.job_steps(runner._job)
            games = lambda kind: len({(st.payload.get("system"), st.payload.get("stem")) for st in steps
                                      if st.kind == kind and st.state == "done"})
            esde_note = next((st.message for st in steps if st.kind == "refresh" and st.state == "done"), "")
            self.last_result = {"state": str(state), "job": runner._job, "failed": failed,
                                "added": games("push"), "removed": games("delete"),
                                "esde_later": "was not restarted" in esde_note}
            if str(state).startswith("error"):
                self.last_error = str(state)[7:]
            self.job_running = False
            self.runner = None
            background = self.finish_in_background
            if self.desktop:
                from . import shell
                shell.launcher_progress(None)
                if background or not self._window_active():
                    self._notify_result(str(state))
            try:
                self.job_finished.emit(str(state))
                self._reload()
            finally:
                if background and self.inbox_busy:
                    pass                            # an import is still running: it quits when it is done
                elif background:
                    self.finish_in_background = False
                    self.quit_requested.emit()      # closed and finished: nothing left to stay open for
                else:
                    self.refresh()
        run_async(work, done, lambda e: done(f"error: {e}"))

    def _window_active(self) -> bool:
        from PySide6.QtWidgets import QApplication
        return QApplication.activeWindow() is not None

    def _watch_progress(self, p) -> None:
        """While the window is closed, a sync that has to wait is paused instead of waiting unseen."""
        if self.finish_in_background and p.state in ("waiting_device", "low_battery", "waiting_emulator"):
            self.stop_job("pause")
        if self.desktop and p.bytes_total:
            import time
            from . import shell
            if time.monotonic() - self._launcher_at > 2:
                self._launcher_at = time.monotonic()
                shell.launcher_progress(p.bytes_done / p.bytes_total)

    def _notify_result(self, state: str) -> None:
        from . import shell
        result = self.last_result or {}
        if state == "done":
            body = f"{result.get('added', 0)} added, {result.get('removed', 0)} removed"
            if result.get("failed"):
                n = len(result["failed"])
                body += f", {n} step{'' if n == 1 else 's'} did not work"
            shell.notify("Sync finished", body)
        elif state == "paused":
            shell.notify("Sync paused", "It will carry on when you open Jewelcase with the device connected.")
        elif state.startswith("error"):
            shell.notify("Sync stopped", self.last_error or "Open Jewelcase to see what happened.")

    def start_sync(self, plan: SyncPlan) -> None:
        self._run_job(lambda runner: runner.start(plan))

    def resume_job(self, job_id: int) -> None:
        self._run_job(lambda runner: job_id, resumed=True)

    def remaining_steps(self) -> dict:
        from ..core.sync import remaining
        job = self.open_job()
        return remaining(self.session.store.job_steps(job)) if job is not None else {}

    def shutdown(self, timeout: float = 10.0) -> bool:
        """Closing the app pauses a running sync and stops a backup after its current file; a restore
        is left to finish. Returns True once nothing is running."""
        import time
        from PySide6.QtCore import QCoreApplication
        self._timer.stop()
        self.auto_backup = False
        self._after_saves = None
        if self.job_running:
            self.stop_job("pause")
        if self.saves_busy and not self.saves_restoring:
            self._saves_stop = True
        end = time.time() + timeout
        # a restore is never cut short: half a save on the handheld is worse than a slow exit
        # nor is an import or conversion: a process that outlives the window would finish unseen
        while (self.job_running or self.saves_busy or self.inbox_busy) and (
                time.time() < end or self.saves_restoring or self.inbox_busy):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        return not (self.job_running or self.saves_busy or self.inbox_busy)

    def copy_to_library(self, keys: list[tuple[str, str]]) -> None:
        rows = [r for r in self.rows if r.key in set(keys) and r.on_device]
        if rows:
            self._run_job(lambda runner: runner.copy_to_library(rows))

    def stop_job(self, mode: str = "pause") -> None:
        if self.runner:
            self.runner.stop(mode)

    def discard_job(self) -> None:
        job = self.open_job()
        if job is not None and not self.job_running:
            store = self.session.store
            store.close_job(job, "cancelled")
            self._reload()

            def undo():
                if self.open_job() is None and not self.job_running:
                    store.reopen_job(job)
                    self._reload()
            self.toast.emit("Unfinished sync was discarded", "Undo", undo)

    def open_job(self) -> int | None:
        return self.session.store.open_job(self.session.serial) if self.session.serial else None
