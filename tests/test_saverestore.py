"""Putting saves back on the device."""
from datetime import datetime

import pytest

from jewelcase.core.db import Store
from jewelcase.core.fakedevice import FakeDevice
from jewelcase.core.savebackup import SaveBackup
from jewelcase.core.saverestore import blocked, restore, versions
from jewelcase.core.savesources import Source, group_items, scan_all

from test_savebackup import delete, put

RA = "/sdcard/RetroArch/saves"
PSP = "/sdcard/PSP/SAVEDATA"
CARDS = "/sdcard/Android/data/com.armsx2/files/memcards"
ROOT_DOLPHIN = "/sdcard/Android/data/org.dolphinemu.dolphinemu/files/GC"
EB = RA + "/EarthBound (USA).srm"
CARD = CARDS + "/mcd001.ps2"

SRC_RA = Source(id="ra-saves", package="com.retroarch.aarch64", label="RetroArch", kind="save", root=RA,
                include=("**/*.srm", "**/*.rtc"), items="name", suffix=r"\.[^./]+$")
SRC_PSP = Source(id="psp-saves", package="org.ppsspp.ppsspp", label="PPSSPP", kind="save", root=PSP,
                 include=("*/**",), items="folder")
SRC_CARDS = Source(id="armsx2-saves", package="com.armsx2", label="ARMSX2", kind="save", root=CARDS,
                   include=("*.ps2",), items="file")
SRC_DOLPHIN = Source(id="dolphin-gc-saves", package="org.dolphinemu.dolphinemu", label="Dolphin", kind="save",
                     root=ROOT_DOLPHIN, include=("**/*.gci",), items="file")
SOURCES = [SRC_RA, SRC_PSP, SRC_CARDS]
INSTALLED = {"com.retroarch.aarch64", "org.ppsspp.ppsspp", "com.armsx2"}


class World:
    def __init__(self, tmp_path):
        self.dev = FakeDevice(tmp_path / "dev")
        self.store = Store(tmp_path / "c.db")
        self.backup = SaveBackup(self.dev, self.store, tmp_path / "lib" / "_saves" / "Fake-FAKE0001", "FAKE0001")
        self.sources = {s.id: s for s in SOURCES}
        self.roots = {s.id: s.root for s in SOURCES}
        self.runs = []
        put(self.dev, EB, b"v1")
        put(self.dev, PSP + "/ULUS1/DATA.BIN", b"psp v1")
        put(self.dev, PSP + "/ULUS1/PARAM.SFO", b"sfo")
        put(self.dev, CARD, b"card v1")
        self.go()                               # run 0: everything new
        put(self.dev, CARD, b"card v2")
        self.go()                               # run 1: only the card changed
        put(self.dev, EB, b"v2")
        put(self.dev, PSP + "/ULUS1/DATA.BIN", b"psp v2")
        self.go()                               # run 2: EarthBound and the PSP save changed

    def go(self):
        r = self.backup.run(scan_all(self.dev, SOURCES))
        assert r.complete and not r.skipped
        self.runs.append(r.run)
        return r

    def items(self):
        out = []
        for s in SOURCES:
            rels = [row.rel for row in self.store.save_files("FAKE0001", s.id)]
            out += group_items(s, rels, {"snes": {"EarthBound (USA)"}}, ("snes",))
        return out

    def item(self, name):
        return next(i for i in self.items() if i.name == name)

    def restore(self, items, at=None, installed=INSTALLED):
        items = items if isinstance(items, list) else [items]
        return restore(self.dev, self.backup, self.sources, self.roots, items, at, installed)

    def read(self, path):
        return self.dev._p(path).read_bytes()


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_versions_list_runs_where_the_item_changed(w):
    found = versions(w.backup, w.item("EarthBound (USA)"))
    assert [v.run for v in found] == [w.runs[2], w.runs[0]]
    assert [v.current for v in found] == [True, False] and [v.size for v in found] == [2, 2]
    assert found[0].when == datetime.strptime(w.runs[2], "%Y%m%d-%H%M%S")
    assert [v.run for v in versions(w.backup, w.item("mcd001.ps2"))] == [w.runs[1], w.runs[0]]


def test_versions_of_a_folder_item_add_up_its_files(w):
    found = versions(w.backup, w.item("ULUS1"))
    assert [(v.run, v.size) for v in found] == [(w.runs[2], 9), (w.runs[0], 9)]


def test_restore_older_version_and_undo(w):
    item = w.item("EarthBound (USA)")
    r = w.restore(item, at=w.runs[0])
    assert w.read(EB) == b"v1" and r.restored == [item.key] and r.failed == []
    assert r.undo_run > w.runs[2]
    u = w.restore(w.item("EarthBound (USA)"), at=r.undo_run)
    assert w.read(EB) == b"v2" and u.restored == [item.key]


def test_restore_latest_is_the_default(w):
    put(w.dev, EB, b"spoiled")
    r = w.restore(w.item("EarthBound (USA)"))
    assert w.read(EB) == b"v2"
    assert w.restore(w.item("EarthBound (USA)"), at=r.undo_run).restored and w.read(EB) == b"spoiled"


def test_device_copy_is_backed_up_first(w):
    put(w.dev, EB, b"unsaved progress")
    r = w.restore(w.item("EarthBound (USA)"), at=w.runs[0])
    assert w.read(EB) == b"v1"
    assert w.backup.latest_path("ra-saves", "EarthBound (USA).srm").read_bytes() == b"unsaved progress"
    assert w.store.last_save_run("FAKE0001").stamp == w.runs[2]            # the pre-backup is not a full run


def test_failed_pre_backup_stops_the_restore(w):
    put(w.dev, EB, b"unsaved progress")
    w.dev.mutate_on_pull = 2
    r = w.restore(w.item("EarthBound (USA)"), at=w.runs[0])
    assert r.restored == [] and r.failed[0][0] == "ra-saves/EarthBound (USA)"
    assert "could not be backed up first" in r.failed[0][1] and w.read(EB) == b"unsaved progress"


def test_refuses_while_emulator_is_open(w):
    w.dev.foreground = "com.retroarch.aarch64"
    r = w.restore(w.item("EarthBound (USA)"), at=w.runs[0])
    assert r.failed == [("ra-saves/EarthBound (USA)", "Close RetroArch on the handheld first.")]
    assert w.read(EB) == b"v2" and not [c for c in w.dev.calls if c[0] == "push"]


def test_folder_item_restores_all_files_and_leaves_extras(w):
    put(w.dev, PSP + "/ULUS1/EXTRA.BIN", b"made later")
    put(w.dev, PSP + "/ULUS1/PARAM.SFO", b"sfo 2")
    r = w.restore(w.item("ULUS1"), at=w.runs[0])
    assert r.restored == ["psp-saves/ULUS1"]
    assert w.read(PSP + "/ULUS1/DATA.BIN") == b"psp v1" and w.read(PSP + "/ULUS1/PARAM.SFO") == b"sfo"
    assert w.read(PSP + "/ULUS1/EXTRA.BIN") == b"made later"
    assert w.backup.latest_path("psp-saves", "ULUS1/EXTRA.BIN").read_bytes() == b"made later"   # backed up first


def test_missing_subfolders_are_created(w):
    delete(w.dev, PSP + "/ULUS1/DATA.BIN")
    delete(w.dev, PSP + "/ULUS1/PARAM.SFO")
    w.dev._p(PSP + "/ULUS1").rmdir()
    r = w.restore(w.item("ULUS1"))
    assert r.restored == ["psp-saves/ULUS1"] and w.read(PSP + "/ULUS1/DATA.BIN") == b"psp v2"


def test_android_data_needs_the_package_folder(w):
    assert blocked(w.dev, SRC_DOLPHIN, ROOT_DOLPHIN, {"org.dolphinemu.dolphinemu"}) == \
        "Open Dolphin once on the handheld, then restore."
    assert blocked(w.dev, SRC_DOLPHIN, ROOT_DOLPHIN, set()) == "Install Dolphin first."
    w.dev.mkdir("/sdcard/Android/data/org.dolphinemu.dolphinemu")
    assert blocked(w.dev, SRC_DOLPHIN, ROOT_DOLPHIN, {"org.dolphinemu.dolphinemu"}) == ""
    assert blocked(w.dev, SRC_RA, "/sdcard/Somewhere/new", {"com.retroarch.aarch64"}) == ""   # shared storage is free
    assert blocked(w.dev, SRC_RA, "/sdcard/Somewhere/new", set()) == "Install RetroArch first."    # but not for a missing app


def test_a_source_marked_not_restorable(w):
    from dataclasses import replace
    assert blocked(w.dev, replace(SRC_RA, restore=False), RA, INSTALLED) == \
        "Jewelcase cannot restore saves for RetroArch on this device."


def test_restore_all_skips_a_failing_item_and_continues(w):
    w.dev.foreground = "com.armsx2"
    put(w.dev, EB, b"spoiled")
    put(w.dev, PSP + "/ULUS1/DATA.BIN", b"spoiled")
    r = w.restore(w.items())
    assert sorted(r.restored) == ["psp-saves/ULUS1", "ra-saves/EarthBound (USA)"]
    assert r.failed == [("armsx2-saves/mcd001.ps2", "Close ARMSX2 on the handheld first.")]
    assert w.read(EB) == b"v2" and w.read(PSP + "/ULUS1/DATA.BIN") == b"psp v2"


def test_device_lost_stops_restore_all(w):
    w.dev.fail_after_bytes = 1
    r = w.restore(w.items(), at=w.runs[0])
    assert r.restored == [] and len(r.failed) == 3
    assert all(reason == "The handheld was disconnected." for _, reason in r.failed)


def test_pushed_copy_is_verified(w):
    w.dev.corrupt_pushes = 1
    r = w.restore(w.item("EarthBound (USA)"), at=w.runs[0])
    assert r.restored == [] and r.failed[0][1] == "The copy on the handheld did not match and was not used."
    assert w.read(EB) == b"v2"
    assert not list(w.dev.root.rglob("*.jewelcase-tmp"))


def test_a_folder_item_is_all_or_nothing(w):
    w.dev.corrupt_pushes = 1                  # the first of its two files arrives damaged
    r = w.restore(w.item("ULUS1"), at=w.runs[0])
    assert r.restored == [] and w.read(PSP + "/ULUS1/DATA.BIN") == b"psp v2"
    assert not list(w.dev.root.rglob("*.jewelcase-tmp"))


def test_no_copy_from_that_time(w):
    r = w.restore(w.item("EarthBound (USA)"), at="20000101-000000")
    assert r.failed == [("ra-saves/EarthBound (USA)", "There is no copy of this save from that time.")]
    assert w.read(EB) == b"v2"


def test_nothing_on_device_is_deleted(w):
    w.dev.calls.clear()
    w.restore(w.items(), at=w.runs[0])
    assert not [c for c in w.dev.calls if c[0] == "remove" and not c[1].endswith(".jewelcase-tmp")]
    w.dev.corrupt_pushes = 1
    w.restore(w.item("EarthBound (USA)"), at=w.runs[2])
    assert not [c for c in w.dev.calls if c[0] == "remove" and not c[1].endswith(".jewelcase-tmp")]


# ---------------------------------------------------------------- found by the branch review
def test_restore_refuses_when_the_device_save_cannot_be_read(w):
    put(w.dev, CARD, b"newer card")
    w.dev.unreadable_files = {CARD}
    w.dev.calls.clear()
    r = w.restore(w.item("mcd001.ps2"), at=w.runs[0])
    assert r.restored == [] and r.failed[0][0] == "armsx2-saves/mcd001.ps2"
    assert "could not be backed up first" in r.failed[0][1] and "cannot be read" in r.failed[0][1]
    assert w.dev._p(CARD).read_bytes() == b"newer card" and not [c for c in w.dev.calls if c[0] == "push"]


def test_restore_refuses_when_the_device_save_is_too_large_to_back_up(w, monkeypatch):
    from jewelcase.core import savesources
    put(w.dev, CARD, b"a much bigger card than before")
    monkeypatch.setattr(savesources, "MAX_FILE", 10)
    w.dev.calls.clear()
    r = w.restore(w.item("mcd001.ps2"), at=w.runs[0])
    assert r.restored == [] and "could not be backed up first" in r.failed[0][1]
    assert w.dev._p(CARD).read_bytes() == b"a much bigger card than before"
    assert not [c for c in w.dev.calls if c[0] == "push"]


def test_restoring_a_date_again_after_a_rollback(w):
    assert w.restore(w.item("ULUS1"), at=w.runs[0]).restored        # roll back to the first version
    w.go()                                                          # a backup sees the rolled-back save
    put(w.dev, PSP + "/ULUS1/DATA.BIN", b"spoiled")
    r = w.restore(w.item("ULUS1"), at=w.runs[0])
    assert r.restored == ["psp-saves/ULUS1"] and w.read(PSP + "/ULUS1/DATA.BIN") == b"psp v1"


def test_a_restored_save_stays_the_emulators_own_file(w):
    """A file adb pushes into an app's folder belongs to adb; the emulator could not write to its own card."""
    put(w.dev, CARD, b"spoiled")
    r = w.restore(w.item("mcd001.ps2"))
    assert r.restored == ["armsx2-saves/mcd001.ps2"] and w.read(CARD) == b"card v2"
    assert w.dev.app_can_use(CARD)
    assert not list(w.dev.root.rglob("*.jewelcase-tmp"))


def test_a_save_that_was_gone_comes_back_writable_for_the_emulator(w):
    delete(w.dev, PSP + "/ULUS1/DATA.BIN")
    delete(w.dev, PSP + "/ULUS1/PARAM.SFO")
    w.dev._p(PSP + "/ULUS1").rmdir()
    r = w.restore(w.item("ULUS1"))
    assert r.restored == ["psp-saves/ULUS1"] and w.read(PSP + "/ULUS1/DATA.BIN") == b"psp v2"
    assert w.dev.app_can_use(PSP + "/ULUS1/DATA.BIN") and w.dev.app_can_use(PSP + "/ULUS1/PARAM.SFO")


def test_a_save_that_does_not_read_back_is_not_called_restored(w):
    w.dev.spoil_in_place = 1
    r = w.restore(w.item("EarthBound (USA)"), at=w.runs[0])
    assert r.restored == []
    assert r.failed == [("ra-saves/EarthBound (USA)",
                         "It did not read back the same after it was written. Restore it again.")]
    assert r.undo_run and not list(w.dev.root.rglob("*.jewelcase-tmp"))


# ---------------------------------------------------------------- emulator settings
SRC_CFG = Source(id="flycast-settings", package="com.flycast.emulator", label="Flycast", kind="settings",
                 root="/sdcard/Android/data/com.flycast.emulator/files", include=("emu.cfg", "mappings/*.cfg"),
                 items="file")
EMU_CFG = SRC_CFG.root + "/emu.cfg"


def test_settings_come_back_with_undo_like_saves(tmp_path):
    w = World(tmp_path)
    w.sources[SRC_CFG.id], w.roots[SRC_CFG.id] = SRC_CFG, SRC_CFG.root
    put(w.dev, EMU_CFG, b"volume = 5")
    put(w.dev, SRC_CFG.root + "/mappings/pad.cfg", b"a = 1")
    scans = scan_all(w.dev, [SRC_CFG])
    first = w.backup.run(scans)
    put(w.dev, EMU_CFG, b"volume = 11")
    w.backup.run(scan_all(w.dev, [SRC_CFG]))
    put(w.dev, EMU_CFG, b"volume = 0 (not backed up yet)")
    items = group_items(SRC_CFG, [r.rel for r in w.store.save_files("FAKE0001", SRC_CFG.id)], {}, ())
    cfg = next(i for i in items if i.name == "emu.cfg")
    r = w.restore(cfg, at=first.run, installed=INSTALLED | {"com.flycast.emulator"})
    assert r.restored == [cfg.key] and r.failed == [] and w.read(EMU_CFG) == b"volume = 5"
    assert w.read(SRC_CFG.root + "/mappings/pad.cfg") == b"a = 1"           # another file is left alone
    u = w.restore(cfg, at=r.undo_run, installed=INSTALLED | {"com.flycast.emulator"})
    assert u.restored == [cfg.key] and w.read(EMU_CFG) == b"volume = 0 (not backed up yet)"


def test_settings_wait_until_the_emulator_is_closed(tmp_path):
    w = World(tmp_path)
    put(w.dev, EMU_CFG, b"volume = 5")
    w.backup.run(scan_all(w.dev, [SRC_CFG]))
    cfg = group_items(SRC_CFG, ["emu.cfg"], {}, ())[0]
    w.dev.foreground = "com.flycast.emulator"
    r = restore(
        w.dev, w.backup, {SRC_CFG.id: SRC_CFG}, {SRC_CFG.id: SRC_CFG.root}, [cfg], None, {"com.flycast.emulator"})
    assert r.restored == [] and r.failed == [(cfg.key, "Close Flycast on the handheld first.")]
