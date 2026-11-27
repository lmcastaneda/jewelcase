import hashlib

import pytest

from jewelcase.core import gamelist
from jewelcase.core.plan import build_plan, with_copy_first
from jewelcase.core.sync import REFRESH_ACTION, Runner, steps_for

from conftest import CARD, GL

GOW, BG = "God of War II (USA)", "Baldur's Gate & Co (USA)"
PPSSPP = "org.ppsspp.ppsspp"


class Harness:
    def __init__(self, world, tmp_path, emulators=(), sleep=None):
        self.w, self.tmp, self.events, self.slept = world, tmp_path, [], []
        self.sleep_hook = sleep
        self.runner = self.new_runner(emulators)

    def new_runner(self, emulators=()):
        w = self.w
        return Runner(lambda: w.dev if w.dev.online else None, w.library, w.store, w.profile, w.serial,
                      log_path=self.tmp / "sync.log", data_dir=self.tmp / "data",
                      emulator_packages=set(emulators), on_progress=self.events.append, sleep=self._sleep)

    def _sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) > 200:
            raise AssertionError("runner is waiting forever")
        if self.sleep_hook:
            self.sleep_hook()

    def plan(self):
        w = self.w
        return build_plan(w.rows(), w.dev.storage(CARD), w.library, w.dev.info())

    def want(self, system, stem, value):
        self.w.store.set_wanted(self.w.serial, system, stem, value)

    def sync(self, plan=None):
        self.job = self.runner.start(plan or self.plan())
        return self.runner.run(self.job)

    def steps(self, kind=None):
        return [s for s in self.w.store.job_steps(self.job) if kind in (None, s.kind)]

    def states(self):
        return [e.state for e in self.events]

    def esde(self, esde_system):
        return gamelist.parse((self.w.dev.root / GL.lstrip("/") / esde_system / "gamelist.xml").read_text())


@pytest.fixture
def h(world, tmp_path):
    return Harness(world, tmp_path / "run")


def test_add_game_end_to_end(h):
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done"
    on_dev = h.w.dev_path("ps2", "Champions (USA).chd")
    assert on_dev.read_bytes() == b"ch" * 800
    assert not on_dev.with_name(on_dev.name + ".part").exists()
    assert ("chmod", f"{CARD}/ps2/Champions (USA).chd.part") in h.w.dev.calls
    mtime = h.w.dev.stat(f"{CARD}/ps2/Champions (USA).chd").mtime
    assert h.w.store.dev_hash(h.w.serial, "ps2", "Champions (USA).chd", 1600, mtime) == \
        hashlib.md5(b"ch" * 800).hexdigest()
    row = h.w.row("ps2", "Champions (USA)")
    assert row.state == "on_device" and row.want
    assert h.w.store.wanted(h.w.serial) == {}
    assert h.w.store.open_job(h.w.serial) is None and h.w.store.last_job(h.w.serial)["state"] == "done"
    assert h.events[-1].state == "done"


def test_add_md5_mismatch_retries_once(h):
    h.want("ps2", "Champions (USA)", True)
    h.w.dev.corrupt_pushes = 1
    assert h.sync() == "done"
    assert h.w.dev_path("ps2", "Champions (USA).chd").read_bytes() == b"ch" * 800
    assert h.steps("push")[0].state == "done"


def test_add_md5_mismatch_twice_skips_that_game(h):
    h.want("ps2", "Champions (USA)", True)
    h.want("gamecube", "Pikmin 2 (USA)", True)
    h.w.dev.corrupt_pushes = 2
    assert h.sync() == "done"
    by_name = {s.payload["name"]: s for s in h.steps("push")}
    failed = [n for n, s in by_name.items() if s.state == "failed"]
    assert len(failed) == 1
    d = h.w.dev_path(by_name[failed[0]].payload["system"], failed[0])
    assert not d.exists() and not d.with_name(d.name + ".part").exists()
    ok = [n for n, s in by_name.items() if s.state == "done"]
    assert len(ok) == 1 and h.w.dev_path(by_name[ok[0]].payload["system"], ok[0]).exists()


def test_add_never_overwrites_a_different_file(h):
    h.want("ps2", "Champions (USA)", True)
    plan = h.plan()
    h.w.dev_path("ps2", "Champions (USA).chd").write_bytes(b"something else")
    assert h.sync(plan) == "done"
    assert h.steps("push")[0].state == "failed"
    assert h.w.dev_path("ps2", "Champions (USA).chd").read_bytes() == b"something else"


def test_remove_game_end_to_end(h):
    h.want("ps2", GOW, False)
    assert h.sync() == "done"
    assert not h.w.dev_path("ps2", GOW + ".chd").exists()
    assert set(h.esde("ps2")) == {BG + ".chd", "Device Only (USA).chd"}
    card_list = gamelist.parse(h.w.dev_path("ps2", "gamelist.xml").read_text())
    assert set(card_list) == {"Champions (USA).chd"}
    backups = [p.name for p in (h.tmp / "data" / "backups").rglob("*") if p.is_file()]
    assert len(backups) == 2 and all("gamelist.xml" in b for b in backups)
    assert not h.w.dev_path("ps2", f"images/{GOW}.png").exists()
    assert (h.w.lib_root / "ps2" / "images" / f"{GOW}.png").read_bytes() == b"art"
    assert (h.w.dev.root / "sdcard/ES-DE/downloaded_media/ps2/covers" / f"{GOW}.png").exists()
    assert h.w.store._q("SELECT count(*) FROM dev_hash WHERE name=?", (GOW + ".chd",)) == [(0,)]
    row = h.w.row("ps2", GOW)
    assert (row.state, row.plays, row.seconds, row.title, row.want) == ("library_only", 2, 3000, "God of War II", False)
    assert h.w.store.wanted(h.w.serial) == {}
    assert h.w.dev.broadcasts == [REFRESH_ACTION]


def test_remove_without_twin_is_skipped_and_file_stays(h):
    (h.w.lib_root / "ps2" / (GOW + ".chd")).write_bytes(b"XXXX" * 1000)     # same size, other bytes
    h.w.library.scan()
    h.want("ps2", GOW, False)
    h.want("ps2", BG, False)
    assert h.sync() == "done"
    assert h.w.dev_path("ps2", GOW + ".chd").exists()
    assert GOW + ".chd" in h.esde("ps2")
    assert h.w.dev_path("ps2", f"images/{GOW}.png").exists()
    assert not h.w.dev_path("ps2", BG + ".chd").exists() and BG + ".chd" not in h.esde("ps2")
    verify = {s.payload["name"]: s for s in h.steps("verify")}
    assert verify[GOW + ".chd"].state == "failed" and verify[GOW + ".chd"].message == "contents differ"
    assert [s.state for s in h.steps("delete") if s.payload["stem"] == GOW] == ["skipped"]
    assert h.w.row("ps2", GOW).want is False      # the tick stays so the user can see it did not happen


def test_remove_game_with_apostrophe(h):
    h.want("ps2", BG, False)
    assert h.sync() == "done"
    assert not h.w.dev_path("ps2", BG + ".chd").exists()
    assert set(h.esde("ps2")) == {GOW + ".chd", "Device Only (USA).chd"}
    assert h.w.dev_path("ps2", GOW + ".chd").exists()


def test_gamelist_without_matching_entries_is_left_alone(h):
    h.want("ps2", BG, False)        # the card-side gamelist never listed this game
    h.w.dev.calls.clear()
    h.sync()
    written = [p for what, p in h.w.dev.calls if what == "write"]
    assert written == [f"{GL}/ps2/gamelist.xml"]


def test_remove_multifile_game(h):
    h.want("psx", "MTV (USA)", False)
    assert h.sync() == "done"
    left = {p.name for p in h.w.dev_path("psx", "x").parent.iterdir()}
    assert left == {"FF7 (USA).m3u", "FF7 (USA) (Disc 1).chd", "FF7 (USA) (Disc 2).chd"}
    assert "MTV (USA).cue" not in h.esde("psx")


def test_remove_keeps_file_needed_by_kept_game(h):
    h.w.dev_path("psx", "Remix (USA).cue").write_text('FILE "MTV (USA) (Track 2).bin" BINARY\n')
    h.w.refresh()
    h.want("psx", "MTV (USA)", False)
    assert h.sync() == "done"
    assert h.w.dev_path("psx", "MTV (USA) (Track 2).bin").exists()
    assert not h.w.dev_path("psx", "MTV (USA) (Track 1).bin").exists()
    assert not h.w.dev_path("psx", "MTV (USA).cue").exists()


def test_resume_after_disconnect_during_push(world, tmp_path):
    def plug_back_in():
        world.dev.online = True
    h = Harness(world, tmp_path / "run", sleep=plug_back_in)
    h.want("ps2", "Champions (USA)", True)
    world.dev.fail_after_bytes = 500
    assert h.sync() == "done"
    assert "waiting_device" in h.states()
    folder = world.dev_path("ps2", "x").parent
    assert (folder / "Champions (USA).chd").read_bytes() == b"ch" * 800
    assert not list(folder.glob("*.part"))


def test_resume_after_disconnect_during_remove(h):
    h.want("ps2", GOW, False)
    h.want("ps2", BG, False)

    def unplug_at_first_delete(event):
        if event.kind == "delete" and h.w.dev.online and not h.unplugged:
            h.unplugged = True
            h.w.dev.online = False
            h.runner.stop("pause")
    h.unplugged = False
    h.runner.on_progress = lambda e: (h.events.append(e), unplug_at_first_delete(e))
    assert h.sync() == "paused"
    assert h.w.store.open_job(h.w.serial) == h.job
    assert h.w.dev_path("ps2", GOW + ".chd").exists()                 # nothing deleted yet
    h.w.dev.online = True
    assert set(h.esde("ps2")) == {"Device Only (USA).chd"}            # gamelist step had finished
    second = h.new_runner()
    assert second.run(h.job) == "done"
    assert not h.w.dev_path("ps2", GOW + ".chd").exists() and not h.w.dev_path("ps2", BG + ".chd").exists()
    assert [s.state for s in h.steps("check")] == ["done"]
    assert len([p for p in (h.tmp / "data" / "backups").rglob("*gamelist.xml*") if p.is_file()]) == 2


def test_waits_while_emulator_in_foreground(world, tmp_path):
    seen = []

    def quit_game():
        if world.dev.foreground == PPSSPP:          # (the runner also sleeps briefly after the refresh)
            seen.append(world.dev_path("ps2", GOW + ".chd").exists())
        world.dev.foreground = "org.es_de.frontend"
    h = Harness(world, tmp_path / "run", emulators=[PPSSPP], sleep=quit_game)
    world.dev.foreground = PPSSPP
    h.want("ps2", GOW, False)
    assert h.sync() == "done"
    assert "waiting_emulator" in h.states()
    assert seen and all(seen)                      # the game was still there while we waited
    assert not world.dev_path("ps2", GOW + ".chd").exists()


def test_low_battery_pauses(world, tmp_path):
    def charge():
        world.dev.battery = 50
    h = Harness(world, tmp_path / "run", sleep=charge)
    world.dev.battery, world.dev.power = 5, "usb"
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done"
    assert "low_battery" in h.states()


def test_low_battery_on_wall_power_does_not_pause(h):
    h.w.dev.battery, h.w.dev.power = 5, "ac"
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done" and "low_battery" not in h.states()


def test_read_error_skips_one_game(h, monkeypatch):
    h.want("ps2", "Champions (USA)", True)
    h.want("gamecube", "Pikmin 2 (USA)", True)
    real = h.w.library.md5

    def flaky(system, name, progress=None):
        if name.startswith("Champions"):
            raise OSError(5, "Input/output error")
        return real(system, name, progress)
    monkeypatch.setattr(h.w.library, "md5", flaky)
    assert h.sync() == "done"
    states = {s.payload["name"]: (s.state, s.message) for s in h.steps("push")}
    assert states["Champions (USA).chd"][0] == "failed" and "read error" in states["Champions (USA).chd"][1]
    assert states["Pikmin 2 (USA).rvz"][0] == "done"
    assert h.w.dev_path("gamecube", "Pikmin 2 (USA).rvz").exists()


def test_refresh_broadcast_sent_once(h):
    h.want("ps2", GOW, False)
    h.want("psx", "MTV (USA)", False)
    h.want("ps2", "Champions (USA)", True)
    h.sync()
    assert h.w.dev.broadcasts == [REFRESH_ACTION]
    assert [s.kind for s in h.steps()][-1] == "refresh"


def test_reappeared_entry_is_stripped_again(h):
    path = h.w.dev.root / GL.lstrip("/") / "ps2" / "gamelist.xml"
    original = path.read_text()
    h.w.dev.broadcast = lambda action: path.write_text(original)     # ES-DE writes its stale copy back
    h.want("ps2", GOW, False)
    assert h.sync() == "done"
    assert GOW + ".chd" not in h.esde("ps2")
    assert "reappeared" in (h.tmp / "sync.log").read_text()


def test_delete_outside_rom_root_asserts(h):
    outside = h.w.dev.root / "sdcard" / "precious.txt"
    outside.write_text("keep me")
    h.job = h.w.store.create_job(h.w.serial, [
        {"kind": "delete", "system": "ps2", "stem": "x", "name": "../../../sdcard/precious.txt"},
        {"kind": "delete", "system": "..", "stem": "x", "name": "sdcard/precious.txt"}])
    assert h.runner.run(h.job) == "done"
    assert outside.read_text() == "keep me"
    assert [s.state for s in h.steps()] == ["failed", "failed"]


def test_copy_to_library_job(h):
    row = h.w.row("ps2", "Device Only (USA)")
    h.job = h.runner.copy_to_library([row])
    assert h.runner.run(h.job) == "done"
    assert (h.w.lib_root / "ps2" / "Device Only (USA).chd").read_bytes() == b"do" * 250
    assert not list((h.w.lib_root / "ps2").glob("*.part"))
    assert h.w.library.cached_md5("ps2", "Device Only (USA).chd") == hashlib.md5(b"do" * 250).hexdigest()
    assert h.w.row("ps2", "Device Only (USA)").state == "on_device"
    assert h.w.dev_path("ps2", "Device Only (USA).chd").exists()


def test_copy_first_then_remove(h):
    h.want("ps2", "Device Only (USA)", False)
    plan = with_copy_first(h.plan(), {("ps2", "Device Only (USA)")})
    assert h.sync(plan) == "done"
    assert (h.w.lib_root / "ps2" / "Device Only (USA).chd").read_bytes() == b"do" * 250
    assert not h.w.dev_path("ps2", "Device Only (USA).chd").exists()
    assert [s.message for s in h.steps("verify")] == ["record"]


def test_copy_to_library_refused_when_full(h, monkeypatch):
    monkeypatch.setattr(h.w.library, "free_bytes", lambda: 100)
    h.want("ps2", "Device Only (USA)", False)
    assert h.sync(with_copy_first(h.plan(), {("ps2", "Device Only (USA)")})) == "done"
    assert h.steps("pull")[0].state == "failed" and "full" in h.steps("pull")[0].message
    assert h.w.dev_path("ps2", "Device Only (USA).chd").exists()


def test_cancel_stops_between_steps(h):
    h.want("ps2", GOW, False)
    h.runner.on_progress = lambda e: h.runner.stop("cancel")
    assert h.sync() == "cancelled"
    assert h.w.dev_path("ps2", GOW + ".chd").exists()
    assert h.w.store.open_job(h.w.serial) is None
    assert h.w.store.last_job(h.w.serial)["state"] == "cancelled"


def test_steps_order_and_log(h):
    h.want("ps2", GOW, False)
    h.want("ps2", "Champions (USA)", True)
    steps = steps_for(h.plan(), h.w.profile)
    assert [s["kind"] for s in steps] == ["verify", "capture", "gamelist", "delete", "delete_art",
                                          "check", "push", "refresh"]
    h.sync()
    log = (h.tmp / "sync.log").read_text()
    assert "God of War II (USA).chd" in log and "Champions (USA).chd" in log and "done" in log


def test_progress_reports_bytes(h):
    h.want("ps2", "Champions (USA)", True)
    h.sync()
    pushes = [e for e in h.events if e.kind == "push"]
    assert pushes and max(e.bytes_done for e in pushes) == 1600 and pushes[0].bytes_total == 1600
    assert pushes[0].label == "Champions (USA).chd" and pushes[0].step_count == 2


# ---------------------------------------------------------------- ES-DE after a sync
ESDE = "org.es_de.frontend"


def _restarts(dev):
    return [c for c in dev.calls if c[0] in ("stop", "start")]


def test_es_de_is_restarted_after_a_sync_that_changed_games(h):
    h.w.dev.foreground = ESDE
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done"
    assert _restarts(h.w.dev) == [("stop", ESDE), ("start", ESDE + "/.MainActivity")]
    assert h.steps("refresh")[0].message == "ES-DE was restarted"


def test_es_de_is_not_restarted_while_a_game_is_running(world, tmp_path):
    h = Harness(world, tmp_path / "run", emulators=[PPSSPP])
    world.dev.foreground = PPSSPP
    h.want("ps2", "Champions (USA)", True)              # adding a game does not wait for the one being played
    assert h.sync() == "done"
    assert _restarts(world.dev) == []
    assert h.steps("refresh")[0].message == \
        "A game is running, so ES-DE was not restarted. It shows the changes the next time it starts."


def test_copying_to_the_library_does_not_restart_es_de(h):
    row = next(r for r in h.w.rows() if r.state == "device_only")
    h.job = h.runner.copy_to_library([row])
    assert h.runner.run(h.job) == "done" and _restarts(h.w.dev) == []


def test_what_es_de_saves_as_it_is_stopped_is_stripped_again(h):
    path = h.w.dev.root / GL.lstrip("/") / "ps2" / "gamelist.xml"
    original = path.read_text()
    h.w.dev.foreground = ESDE
    h.w.dev.before_stop = lambda: path.write_text(original)        # ES-DE writes its stale copy on the way out
    h.want("ps2", GOW, False)
    assert h.sync() == "done"
    assert GOW + ".chd" not in h.esde("ps2")
    order = [c[0] for c in h.w.dev.calls if c[0] in ("stop", "write", "start")]
    assert order[-3:] == ["stop", "write", "start"]


def _install_es_de(dev):
    import zipfile

    from jewelcase.core import esde
    from jewelcase.core.device import PackageInfo
    from test_esde import BUNDLED, RULES
    for path in (dev.root / "data/app/esde/base.apk", dev.root / esde.CUSTOM_SYSTEMS.lstrip("/")):
        path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dev.root / "data/app/esde/base.apk", "w") as z:
        z.writestr("assets/systems/android/es_systems.xml", BUNDLED)
        z.writestr("assets/systems/android/es_find_rules.xml", RULES)
    dev.fake_apks[ESDE] = "/data/app/esde/base.apk"
    dev.fake_packages[ESDE] = PackageInfo(ESDE, "3.5.0-65", False, False, True)
    return dev.root / esde.CUSTOM_SYSTEMS.lstrip("/")


def test_es_de_in_the_background_is_closed_and_the_sync_does_not_claim_a_restart(h):
    h.w.dev.foreground = "com.android.launcher3"
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done"
    assert _restarts(h.w.dev) == [("stop", ESDE)]
    assert h.steps("refresh")[0].message == "ES-DE shows the changes when it is next opened"


def test_a_game_in_an_emulator_only_es_de_knows_also_keeps_es_de_running(h):
    _install_es_de(h.w.dev)
    h.w.dev.foreground = "aenu.aps3e.premium"           # in ES-DE's rules; the runner was told of no emulators
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done"
    assert _restarts(h.w.dev) == []
    assert "was not restarted" in h.steps("refresh")[0].message


def test_es_de_comes_back_after_the_handheld_drops_in_the_middle_of_the_last_step(world, tmp_path):
    def plug_back_in():
        world.dev.online = True
    h = Harness(world, tmp_path / "run", sleep=plug_back_in)
    world.dev.foreground = ESDE

    def unplug():
        world.dev.online = False                        # ES-DE is stopped, then the cable comes out
    world.dev.before_stop = unplug
    h.want("ps2", GOW, False)
    assert h.sync() == "done"
    assert _restarts(world.dev)[-1] == ("start", ESDE + "/.MainActivity") and world.dev.foreground == ESDE


def test_a_system_es_de_does_not_look_for_is_added_when_its_first_game_arrives(h):
    dev = h.w.dev
    custom = _install_es_de(dev)
    custom.write_text(f"""<?xml version="1.0"?>
<systemList>
    <system>
        <name>ps2</name>
        <fullname>Sony PlayStation 2</fullname>
        <path>{CARD}/ps2</path>
        <command label="ARMSX2 (Standalone)">%EMULATOR_ARMSX2% %DATA%=%ROMSAF%</command>
    </system>
</systemList>
""")
    h.want("gamecube", "Pikmin 2 (USA)", True)
    assert h.sync() == "done"
    assert f"<path>{CARD}/gamecube</path>" in custom.read_text()
    assert "GameCube" in h.steps("refresh")[0].message
    before = custom.read_text()
    h.want("ps2", "Champions (USA)", True)
    assert h.sync() == "done" and custom.read_text() == before          # ps2 was already there
