"""Tests for the findings of the whole-branch review. Each one failed before its fix."""
import hashlib
import os
import subprocess

import pytest

from jewelcase.core import adb, catalog, gamelist, inbox as inboxmod, systems
from jewelcase.core import sync as syncmod
from jewelcase.core.db import Store
from jewelcase.core.device import DeviceError, DeviceGone
from jewelcase.core.inbox import Inbox, detect_system
from jewelcase.core.library import Library
from jewelcase.core.plan import build_plan, prove_twin, with_copy_first
from jewelcase.core.sync import Busy, JobOpen, Runner

from conftest import CARD, GL, write_tree
from test_sync import BG, GOW, Harness

GOW_FILE = GOW + ".chd"


def twin(world, system, name, stem=None):
    return prove_twin(world.dev, world.library, world.store, world.profile, system, name, stem=stem)


def bump(path, body):
    """Replace a file's bytes and move its mtime forward, as a real change would."""
    old = path.stat().st_mtime
    path.write_bytes(body)
    os.utime(path, (old + 100, old + 100))


@pytest.fixture
def h(world, tmp_path):
    return Harness(world, tmp_path / "run")


def plan_of(world):
    return build_plan(world.rows(), world.dev.storage(CARD), world.library, world.dev.info())


# ---------------------------------------------------------------- 1. stale "record" twins
def test_device_file_changed_in_place_is_not_a_twin(world):
    assert twin(world, "ps2", GOW_FILE).ok
    bump(world.dev_path("ps2", GOW_FILE), b"XXXX" * 1000)            # same size, other bytes
    r = twin(world, "ps2", GOW_FILE)
    assert (r.ok, r.reason) == (False, "contents differ")


def test_library_file_swapped_same_size_is_not_a_twin(world):
    assert twin(world, "ps2", GOW_FILE).ok
    bump(world.lib_root / "ps2" / GOW_FILE, b"YYYY" * 1000)          # no rescan in between
    r = twin(world, "ps2", GOW_FILE)
    assert (r.ok, r.reason) == (False, "contents differ")


def test_unchanged_files_are_still_proven_from_the_record(world):
    assert twin(world, "ps2", GOW_FILE).how == "hash"
    world.dev.calls.clear()
    assert twin(world, "ps2", GOW_FILE).how == "record"
    assert not any(what == "md5" for what, _ in world.dev.calls)


# ---------------------------------------------------------------- 2. resume
def pause_at_first_delete(h):
    def hook(event):
        h.events.append(event)
        if event.kind == "delete" and not getattr(h, "paused", False):
            h.paused = True
            h.runner.stop("pause")
    h.runner.on_progress = hook
    assert h.sync() == "paused"


def test_resume_reverifies_and_blocks_when_library_is_gone(h, tmp_path):
    h.want("ps2", GOW, False)
    pause_at_first_delete(h)
    h.w.library.root = tmp_path / "unmounted"
    assert h.new_runner().run(h.job, resumed=True) == "done"
    assert h.w.dev_path("ps2", GOW_FILE).exists()
    assert [s.state for s in h.steps("delete")] == ["skipped"]
    assert [(s.state, s.message) for s in h.steps("verify")] == [("failed", "library offline")]


def test_resume_skips_games_ticked_again(h):
    h.want("ps2", GOW, False)
    h.want("ps2", BG, False)
    pause_at_first_delete(h)
    h.want("ps2", GOW, None)                       # the user ticked it again while paused
    assert h.new_runner().run(h.job, resumed=True) == "done"
    assert h.w.dev_path("ps2", GOW_FILE).exists()
    assert not h.w.dev_path("ps2", BG + ".chd").exists()
    skipped = [s for s in h.steps("delete") if s.payload["stem"] == GOW]
    assert [s.state for s in skipped] == ["skipped"] and "ticked" in skipped[0].message


def test_resume_skips_additions_unticked_again(h):
    h.want("ps2", "Champions (USA)", True)
    job = h.runner.start(h.plan())
    h.want("ps2", "Champions (USA)", None)
    assert h.runner.run(job, resumed=True) == "done"
    assert not h.w.dev_path("ps2", "Champions (USA).chd").exists()


def test_cannot_start_a_second_job_while_one_is_open(h):
    h.want("ps2", GOW, False)
    plan = h.plan()
    h.runner.start(plan)
    with pytest.raises(JobOpen):
        h.runner.start(plan)
    with pytest.raises(JobOpen):
        h.runner.copy_to_library([h.w.row("ps2", "Device Only (USA)")])


# ---------------------------------------------------------------- 3. unplugging is a pause, not a failure
class Replies:
    def __init__(self, *replies, state=b"device\n"):
        self.replies, self.state, self.calls = list(replies), state, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[3] == "get-state":
            ok = self.state.strip() == b"device"
            return subprocess.CompletedProcess(argv, 0 if ok else 1, self.state if ok else b"", b"" if ok else self.state)
        out, err, rc = self.replies.pop(0) if self.replies else (b"", b"", 0)
        return subprocess.CompletedProcess(argv, rc, out, err)


def real_dev(run, serial="SER1"):
    return adb.AdbDevice("/bin/adb", adb.AdbTarget(serial, "device", "m", "usb"), run=run)


def test_silent_adb_failure_with_device_gone_is_device_gone():
    run = Replies((b"", b"", 255), state=b"error: device 'SER1' not found\n")
    with pytest.raises(DeviceGone):
        real_dev(run).md5("/x")


def test_adb_failure_with_device_still_there_is_an_error():
    run = Replies((b"", b"adb: error: failed to copy 'a' to 'b': remote No space left on device\n", 1))
    with pytest.raises(DeviceError):
        real_dev(run).md5("/x")


def test_listing_while_unplugged_is_not_an_empty_device():
    run = Replies((b"", b"", 1), state=b"error: no devices/emulators found\n")
    with pytest.raises(DeviceGone):
        real_dev(run).list_files("/storage/X")
    run = Replies((b"", b"find: /nope: No such file or directory\n", 1))
    assert real_dev(run).list_files("/nope") == []


def test_failed_push_removes_the_partial_file(h):
    h.want("ps2", "Champions (USA)", True)
    h.w.dev.push_error = "adb: error: failed to copy: remote No space left on device"
    assert h.sync() == "done"
    assert h.steps("push")[0].state == "failed" and "No space left" in h.steps("push")[0].message
    assert not list(h.w.dev_path("ps2", "x").parent.glob("*.part"))


# ---------------------------------------------------------------- 4. gamelist writes are atomic
def test_write_bytes_goes_through_a_temporary_file():
    data = b"<gameList/>"
    good = hashlib.md5(data).hexdigest().encode()
    run = Replies((b"", b"", 0), (good + b"  /g/gamelist.xml.jewelcase-tmp\n", b"", 0), (b"", b"", 0))
    real_dev(run).write_bytes("/g/gamelist.xml", data)
    shell = [c[4] for c in run.calls]
    assert shell[0] == "cat > /g/gamelist.xml.jewelcase-tmp"
    assert shell[1] == "md5sum /g/gamelist.xml.jewelcase-tmp"
    assert shell[2] == "mv -f /g/gamelist.xml.jewelcase-tmp /g/gamelist.xml"


def test_write_bytes_mismatch_leaves_the_original():
    run = Replies((b"", b"", 0), (b"f" * 32 + b"  x\n", b"", 0), (b"", b"", 0))
    with pytest.raises(DeviceError):
        real_dev(run).write_bytes("/g/gamelist.xml", b"<gameList/>")
    shell = [c[4] for c in run.calls]
    assert not any(s.startswith("mv ") for s in shell) and shell[-1] == "rm -f /g/gamelist.xml.jewelcase-tmp"


def test_interrupted_gamelist_write_leaves_the_original(world, tmp_path):
    path = world.dev.root / GL.lstrip("/") / "ps2" / "gamelist.xml"
    before = path.read_text()
    seen = []

    def plug_back_in():
        seen.append(path.read_text())
        world.dev.online = True
    h = Harness(world, tmp_path / "run", sleep=plug_back_in)
    h.want("ps2", GOW, False)
    world.dev.fail_next_write = True
    assert h.sync() == "done"
    assert seen and seen[0] == before                       # untouched while the device was away
    assert set(h.esde("ps2")) == {BG + ".chd", "Device Only (USA).chd"}
    assert not h.w.dev_path("ps2", GOW_FILE).exists()


# ---------------------------------------------------------------- 5. one sync at a time
def test_second_runner_refuses_while_one_is_syncing(h):
    h.want("ps2", GOW, False)
    job = h.runner.start(h.plan())
    with syncmod.job_lock(h.tmp / "data"):
        with pytest.raises(Busy):
            h.runner.run(job)
    assert h.w.dev_path("ps2", GOW_FILE).exists()
    assert h.runner.run(job) == "done"


# ---------------------------------------------------------------- 6. the device has a form the library lacks
def test_device_form_missing_from_library_needs_a_copy_first(world):
    (world.lib_root / "ps2" / GOW_FILE).rename(world.lib_root / "ps2" / (GOW + ".iso"))
    world.refresh()
    world.store.set_wanted(world.serial, "ps2", GOW, False)
    plan = plan_of(world)
    assert plan.removes == [] and [(r.stem, why) for r, why in plan.skipped] == [(GOW, "no library copy")]
    assert catalog.files_missing_from_library(world.row("ps2", GOW), world.library) == [GOW_FILE]
    plan = with_copy_first(plan, {("ps2", GOW)})
    assert [i.files for i in plan.pulls] == [(GOW_FILE,)] and len(plan.removes) == 1


# ---------------------------------------------------------------- 7. one device, however it is connected
def test_hardware_serial_identifies_the_device_over_wifi():
    wifi = "adb-RG477V00000001-aBcDeF._adb-tls-connect._tcp"
    run = Replies((b"RG477V00000001\n", b"", 0), (b"0" * 32 + b"  /x\n", b"", 0))
    dev = real_dev(run, wifi)
    assert dev.serial == wifi
    dev.use_hardware_serial()
    assert dev.serial == "RG477V00000001"
    dev.md5("/x")
    assert run.calls[-1][:3] == ["/bin/adb", "-s", wifi]       # still addressed by its transport


# ---------------------------------------------------------------- 8. shared files
def test_shared_file_survives_when_the_other_game_stays(h):
    w = h.w
    cue = 'FILE "MTV (USA) (Track 2).bin" BINARY\n'
    w.dev_path("psx", "Remix (USA).cue").write_text(cue)
    (w.lib_root / "psx" / "Remix (USA).cue").write_text(cue.replace("BINARY", "binary"))   # same size, differs
    w.refresh()
    h.want("psx", "MTV (USA)", False)
    h.want("psx", "Remix (USA)", False)
    assert h.sync() == "done"
    assert w.dev_path("psx", "Remix (USA).cue").exists()                 # failed verification: stays
    assert w.dev_path("psx", "MTV (USA) (Track 2).bin").exists()         # and so does the track it needs
    assert not w.dev_path("psx", "MTV (USA) (Track 1).bin").exists()
    assert [s.state for s in h.steps("check")] == ["done"]


def test_plan_keeps_files_of_games_that_cannot_be_removed(world):
    world.dev_path("psx", "Remix (USA).cue").write_text('FILE "MTV (USA) (Track 2).bin" BINARY\n')
    world.refresh()                                                      # Remix has no library copy at all
    world.store.set_wanted(world.serial, "psx", "MTV (USA)", False)
    world.store.set_wanted(world.serial, "psx", "Remix (USA)", False)
    plan = plan_of(world)
    assert [i.row.stem for i in plan.removes] == ["MTV (USA)"]
    assert "MTV (USA) (Track 2).bin" not in plan.removes[0].files


# ---------------------------------------------------------------- 10. removing never needs free space
def test_remove_only_plan_fits_on_a_full_card(world):
    from jewelcase.core.device import Storage
    world.store.set_wanted(world.serial, "ps2", GOW, False)
    full = Storage(239 * 10 ** 9, 239 * 10 ** 9 - 10 ** 8, 10 ** 8)
    assert build_plan(world.rows(), full, world.library, world.dev.info()).fits is True


# ---------------------------------------------------------------- 11. playlists
def test_unreadable_playlist_cannot_be_synced(world):
    write_tree(world.lib_root, {"psx": {"Solo (USA).cue": 'FILE "Solo (USA).bin" BINARY\n',
                                        "Solo (USA).bin": b"s" * 300}})
    (world.lib_root / "psx" / "Solo (USA).cue").chmod(0)
    try:
        world.refresh()
        row = world.row("psx", "Solo (USA)")
        assert row.lib_unit.missing
        world.store.set_wanted(world.serial, "psx", "Solo (USA)", True)
        plan = plan_of(world)
        assert plan.adds == []
        assert [(r.stem, why) for r, why in plan.skipped] == [("Solo (USA)", "incomplete in the library")]
    finally:
        (world.lib_root / "psx" / "Solo (USA).cue").chmod(0o644)


def test_playlists_are_remembered_when_the_library_is_offline(world, tmp_path):
    fresh = Library(tmp_path / "unmounted", world.store)                 # a new run, disk not mounted
    assert fresh.online is False
    ff7 = fresh.forms("psx")["FF7 (USA)"]
    assert len(ff7) == 1 and len(ff7[0].files) == 3
    assert set(fresh.forms("psx")) == {"FF7 (USA)", "MTV (USA)"}


def test_playlists_are_not_reread_on_every_listing(world, monkeypatch):
    world.library.invalidate()
    world.library.forms("psx")
    from pathlib import Path
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read again")))
    world.library.invalidate()
    assert len(world.library.forms("psx")["FF7 (USA)"][0].files) == 3


# ---------------------------------------------------------------- 12. copying into the library
def test_pull_does_not_overwrite_an_unindexed_library_file(h):
    target = h.w.lib_root / "ps2" / "Device Only (USA).chd"
    target.write_bytes(b"something the scan has not seen yet")
    h.job = h.runner.copy_to_library([h.w.row("ps2", "Device Only (USA)")])
    assert h.runner.run(h.job) == "done"
    assert h.steps("pull")[0].state == "failed" and "different file" in h.steps("pull")[0].message
    assert target.read_bytes() == b"something the scan has not seen yet"


def test_copies_into_the_library_are_flushed_before_they_are_trusted(h, monkeypatch, tmp_path):
    flushed = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (flushed.append(fd), real(fd))[1])
    h.job = h.runner.copy_to_library([h.w.row("ps2", "Device Only (USA)")])
    assert h.runner.run(h.job) == "done" and len(flushed) >= 1
    flushed.clear()
    box = tmp_path / "Downloads"
    box.mkdir()
    f = box / "A (USA).gba"
    f.write_bytes(b"rom")
    os.utime(f, (1, 1))
    inboxmod.import_item(Inbox(box).scan()[0], "gba", h.w.library, h.w.store, None)
    assert len(flushed) >= 1


# ---------------------------------------------------------------- 13. file types
def test_megadrive_md_roms_are_games():
    md = systems.get("megadrive")
    assert systems.is_rom_file(md, "Sonic (USA).md")
    assert not systems.is_rom_file(systems.get("ps2"), "notes.md")


def test_inbox_tells_a_megadrive_rom_from_a_markdown_file(tmp_path):
    rom = bytearray(0x200)
    rom[0x100:0x110] = b"SEGA MEGA DRIVE "
    (tmp_path / "Sonic (USA).md").write_bytes(bytes(rom))
    (tmp_path / "README.md").write_text("# notes\n" * 100)
    for f in tmp_path.iterdir():
        os.utime(f, (1, 1))
    assert detect_system(tmp_path / "Sonic (USA).md") == ("megadrive", "disc header")
    assert [i.path.name for i in Inbox(tmp_path).scan()] == ["Sonic (USA).md"]


def test_save_files_beside_roms_are_not_games():
    n64 = systems.get("n64")
    for name in ("Zelda (USA).eep", "Zelda (USA).sra", "Zelda (USA).mpk", "Game.dsv", "card.mcr", "x.srm"):
        assert not systems.is_rom_file(n64, name), name
