"""Putting the saves and settings of one handheld onto another: several backup sets in one library."""
import json
import os
import shutil

import pytest

from jewelcase.core import saves
from jewelcase.core.db import Store
from jewelcase.core.fakedevice import FakeDevice
from jewelcase.core.savebackup import SaveBackup
from jewelcase.core.savesources import Source, load_sources, map_root, map_text, scan_all
from jewelcase.core.session import open_session

from test_savebackup import put
from test_saves_cli import CC, KEY, Cli

OLD = "RG477V-OLD00001"
RA_CFG = "/sdcard/Android/data/com.retroarch.aarch64/files/retroarch.cfg"
CFG_KEY = "retroarch-settings/retroarch.cfg"
OTHER_CARD = "C0DE-CAFE"
OWN_CARD = "1A2B-3C4D"                         # the demo handheld's card
CARD_SRC = Source(id="card-saves", package="com.retroarch.aarch64", label="RetroArch", kind="save",
                  root=f"/storage/{OTHER_CARD}/Saves", include=("*.srm",), items="name", suffix=r"\.srm$")
CARD_KEY = "card-saves/Zelda"


@pytest.fixture
def run(capsys, tmp_path):
    return Cli(capsys, tmp_path / "demo")


def make_set(demo, files: dict, serial="OLD00001", model="RG477V", sources=None, registered=True, runs=1):
    """Back up a made-up other handheld into the demo library. Returns the stamps of its runs."""
    s = open_session(demo_dir=demo)
    dev = FakeDevice(demo / f"device-{serial}", serial=serial)
    store = s.store if registered else Store(demo / "unseen.db")        # unseen: a PC that never met this handheld
    backup = SaveBackup(dev, store, s.library.root / "_saves" / f"{model}-{serial}", serial)
    stamps = []
    for n in range(runs):
        for path, data in files.items():
            put(dev, path, data if isinstance(data, bytes) else data[n])
        result = backup.run(scan_all(dev, sources or load_sources()))
        assert result.complete and not result.skipped
        stamps.append(result.run)
    return stamps


def own_latest(run, source, rel):
    return (run.demo / "library" / "_saves" / "RG_Demo-DEMO0001" / "latest" / source / rel)


def session(run):
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    return s


# ---------------------------------------------------------------- which sets there are
def test_devices_lists_the_sets_of_the_library(run):
    run("saves", "backup")
    make_set(run.demo, {"/sdcard/RetroArch/saves/Chrono Compass (USA).srm": b"old", RA_CFG: b"x = 1"})
    data = run("--json", "saves", "devices").json
    own, other = data
    assert own["name"] == "RG_Demo-DEMO0001" and own["own"] is True and own["serial"] == "DEMO0001"
    assert own["saves"] >= 3 and own["settings"] == 0
    assert other["name"] == OLD and other["own"] is False and other["model"] == "RG477V"
    assert (other["serial"], other["saves"], other["states"], other["settings"]) == ("OLD00001", 1, 0, 1)
    assert other["last_backup"]
    text = run("saves", "devices").out
    assert OLD in text and "1 save" in text and "(this handheld)" in text and "last backed up" in text


def test_devices_with_nothing_backed_up(run):
    assert run("saves", "devices").out.strip() == "No backups in the library yet."


def test_devices_needs_the_library(run):
    import os
    os.rename(run.demo / "library", run.demo / "library-away")
    (run.demo / "library").mkdir()
    assert run("saves", "devices").code == 2 and "library disk is not connected" in run.out


# ---------------------------------------------------------------- restoring from another handheld
def test_restore_everything_from_another_handheld_and_undo(run):
    run("saves", "backup")
    own_before = run.read(CC)
    stamps = make_set(run.demo, {"/" + CC: b"progress from the old handheld"})
    out = run("--json", "saves", "restore", "--all", "--from", OLD, "--yes").json
    assert out["restored"] == [KEY] and out["failed"] == []
    assert run.read(CC) == b"progress from the old handheld"
    # what the new handheld held went into the NEW handheld's own set, and the other set is untouched
    assert own_latest(run, "retroarch-saves", "Chrono Compass (USA).srm").read_bytes() == own_before
    other = run.demo / "library" / "_saves" / OLD
    assert (other / "latest" / "retroarch-saves" / "Chrono Compass (USA).srm").read_bytes() \
        == b"progress from the old handheld"
    assert not (other / "history").exists()
    assert out["undo_run"] > stamps[0]
    undo = run("--json", "saves", "restore", KEY, "--at", out["undo_run"], "--yes").json
    assert undo["restored"] == [KEY] and run.read(CC) == own_before


def test_one_save_by_key_and_by_serial_alone(run):
    run("saves", "backup")
    make_set(run.demo, {"/" + CC: b"old"})
    assert run("saves", "restore", KEY, "--from", "old00001").code == 2 and "--yes" in run.out
    assert OLD in run.out
    assert run("--json", "saves", "restore", KEY, "--from", "OLD00001", "--yes").json["restored"] == [KEY]
    assert run.read(CC) == b"old"
    assert run("saves", "restore", "retroarch-saves/Nope", "--from", OLD, "--yes").code == 2
    assert "No such save" in run.out


def test_an_earlier_backup_of_the_other_handheld(run):
    run("saves", "backup")
    first, second = make_set(run.demo, {"/" + CC: (b"old v1", b"old v2")}, runs=2)
    latest = run.demo / "library" / "_saves" / OLD / "latest" / "retroarch-saves" / "Chrono Compass (USA).srm"
    os.utime(latest, (latest.stat().st_atime + 120, latest.stat().st_mtime + 120))     # the runs were minutes apart
    assert run("--json", "saves", "restore", "--all", "--from", OLD, "--at", first, "--yes").json["restored"] == [KEY]
    assert run.read(CC) == b"old v1"
    run("--json", "saves", "restore", "--all", "--from", OLD, "--yes")
    assert run.read(CC) == b"old v2"


def test_one_emulator_from_the_other_handheld(run):
    run("saves", "backup")
    card = "sdcard/Android/data/com.armsx2/files/memcards/mcd001.ps2"
    before = run.read(card)
    make_set(run.demo, {"/" + CC: b"old", "/" + card: b"old card"})
    out = run("--json", "saves", "restore", "--emulator", "RetroArch", "--from", OLD, "--yes").json
    assert out["restored"] == [KEY] and run.read(card) == before
    assert run("saves", "restore", "--emulator", "Nope", "--from", OLD, "--yes").code == 2


def test_an_unknown_set_is_named_with_the_ones_there_are(run):
    run("saves", "backup")
    make_set(run.demo, {"/" + CC: b"old"})
    assert run("saves", "restore", "--all", "--from", "Nobody-0000", "--yes").code == 2
    assert "No backup set is called Nobody-0000" in run.out and OLD in run.out


def test_a_replacement_handheld_with_no_backups_of_its_own(run):
    make_set(run.demo, {"/" + CC: b"old", RA_CFG: b"video_vsync = true"}, registered=False)   # a PC that never saw it
    assert not (run.demo / "library" / "_saves" / "RG_Demo-DEMO0001").exists()
    run.put(RA_CFG.lstrip("/"), b"fresh install")                   # RetroArch has been opened once on it
    assert saves.has_other_sets(session(run))
    out = run("--json", "saves", "restore", "--all", "--from", OLD, "--yes").json
    assert sorted(out["restored"]) == sorted([KEY, CFG_KEY]) and out["failed"] == []
    assert run.read(CC) == b"old" and run.read(RA_CFG.lstrip("/")) == b"video_vsync = true"
    assert (run.demo / "library" / "_saves" / "RG_Demo-DEMO0001" / "latest").is_dir()      # its own set now exists


def test_the_emulator_still_has_to_be_installed_and_closed(run):
    run("saves", "backup")
    make_set(run.demo, {"/" + CC: b"old"})
    s = session(run)
    s.device.foreground = "com.retroarch.aarch64"
    other = saves.other_set(s, OLD)
    assert saves.restore(s, [KEY], from_set=other).failed == [(KEY, "Close RetroArch on the handheld first.")]
    assert run.read(CC) != b"old"
    s.device.foreground = ""
    assert saves.restore(s, [KEY], from_set=other).restored == [KEY] and run.read(CC) == b"old"


def test_nothing_on_the_handheld_is_deleted(run):
    run("saves", "backup")
    extra = "sdcard/RetroArch/saves/Only Here (USA).srm"
    run.put(extra, b"only on this handheld")
    make_set(run.demo, {"/" + CC: b"old"})
    run("--json", "saves", "restore", "--all", "--from", OLD, "--yes")
    assert run.read(extra) == b"only on this handheld"


# ---------------------------------------------------------------- paths that belong to one handheld
@pytest.fixture
def card_world(run, monkeypatch):
    make_set(run.demo, {f"/storage/{OTHER_CARD}/Saves/Zelda.srm": b"old zelda"}, sources=[CARD_SRC])
    monkeypatch.setattr(saves, "sources", lambda session_: [CARD_SRC])
    s = session(run)
    return s, saves.other_set(s, OLD)


def test_a_folder_on_the_old_card_is_mapped_to_the_new_card(run, card_world):
    s, other = card_world
    out = saves.restore(s, [CARD_KEY], from_set=other)          # this handheld has one card, so the folder moves to it
    assert out.restored == [CARD_KEY] and out.failed == []
    assert run.read(f"storage/{OWN_CARD}/Saves/Zelda.srm") == b"old zelda"
    assert not (run.demo / "device" / "storage" / OTHER_CARD).exists()


def test_a_file_that_cannot_be_mapped_is_skipped_and_says_so(run, card_world):
    s, other = card_world
    shutil.rmtree(run.demo / "device" / "storage" / OWN_CARD)        # no memory card in this handheld
    out = saves.restore(s, [CARD_KEY], from_set=other)
    assert out.restored == [] and out.undo_run == ""
    [(key, why)] = out.failed
    assert key == CARD_KEY and f"memory card {OTHER_CARD}" in why and "has no memory card" in why
    assert not list((run.demo / "device").rglob("Zelda.srm"))


def test_two_cards_are_never_guessed_between(run, card_world):
    s, other = card_world
    (run.demo / "device" / "storage" / "9999-AAAA").mkdir()
    out = saves.restore(s, [CARD_KEY], from_set=other)
    assert out.restored == [] and "2 memory cards" in out.failed[0][1] and "cannot tell" in out.failed[0][1]
    assert not list((run.demo / "device").rglob("Zelda.srm"))


def test_a_card_that_is_present_is_kept(run, card_world):
    s, other = card_world
    (run.demo / "device" / "storage" / OTHER_CARD).mkdir()           # the very same card is in this handheld
    (run.demo / "device" / "storage" / "9999-AAAA").mkdir()
    out = saves.restore(s, [CARD_KEY], from_set=other)
    assert out.restored == [CARD_KEY] and run.read(f"storage/{OTHER_CARD}/Saves/Zelda.srm") == b"old zelda"


def test_a_mapped_folder_is_backed_up_first_where_it_will_be_written(run, card_world):
    s, other = card_world
    run.put(f"storage/{OWN_CARD}/Saves/Zelda.srm", b"new handheld zelda")
    out = saves.restore(s, [CARD_KEY], from_set=other)
    assert out.restored == [CARD_KEY] and run.read(f"storage/{OWN_CARD}/Saves/Zelda.srm") == b"old zelda"
    assert own_latest(run, "card-saves", "Zelda.srm").read_bytes() == b"new handheld zelda"
    undo = saves.restore(s, [CARD_KEY], at=out.undo_run)
    assert undo.restored == [CARD_KEY] and run.read(f"storage/{OWN_CARD}/Saves/Zelda.srm") == b"new handheld zelda"


CFG_OLD = (b'savefile_directory = "/storage/C0DE-CAFE/RetroArch/saves"\n'
           b'system_directory = "/storage/C0DE-CAFE/RetroArch/system"\nvideo_vsync = "true"\n')


def test_settings_naming_the_old_card_are_pointed_at_the_new_one(run):
    run.put(RA_CFG.lstrip("/"), b"video_vsync = false")
    make_set(run.demo, {RA_CFG: CFG_OLD})
    out = run("--json", "saves", "restore", CFG_KEY, "--from", OLD, "--yes").json
    assert out["restored"] == [CFG_KEY]
    assert run.read(RA_CFG.lstrip("/")) == CFG_OLD.replace(OTHER_CARD.encode(), OWN_CARD.encode())
    assert OTHER_CARD.encode() in (run.demo / "library" / "_saves" / OLD / "latest" / "retroarch-settings"
                                   / "retroarch.cfg").read_bytes()            # the backup itself is never changed
    undo = run("--json", "saves", "restore", CFG_KEY, "--at", out["undo_run"], "--yes").json
    assert undo["restored"] == [CFG_KEY] and run.read(RA_CFG.lstrip("/")) == b"video_vsync = false"


def test_settings_that_cannot_be_pointed_at_a_card_are_skipped(run):
    run.put(RA_CFG.lstrip("/"), b"video_vsync = false")
    make_set(run.demo, {RA_CFG: CFG_OLD})
    (run.demo / "device" / "storage" / "9999-AAAA").mkdir()
    out = run("--json", "saves", "restore", CFG_KEY, "--from", OLD, "--yes")
    assert out.code == 1 and out.json["restored"] == []
    assert out.json["failed"][0][0] == CFG_KEY and f"memory card {OTHER_CARD}" in out.json["failed"][0][1]
    assert run.read(RA_CFG.lstrip("/")) == b"video_vsync = false"


def test_settings_without_a_card_in_them_are_not_touched(run):
    run.put(RA_CFG.lstrip("/"), b"old")
    make_set(run.demo, {RA_CFG: b'video_vsync = "true"\nnetplay_nickname = "/storage/emulated/0/x"\n'})
    run("--json", "saves", "restore", CFG_KEY, "--from", OLD, "--yes")
    assert run.read(RA_CFG.lstrip("/")) == b'video_vsync = "true"\nnetplay_nickname = "/storage/emulated/0/x"\n'


# ---------------------------------------------------------------- the mapping itself
def test_map_root():
    cards = ["1A2B-3C4D"]
    assert map_root("/sdcard/RetroArch/saves", cards) == ("/sdcard/RetroArch/saves", "")
    assert map_root("/storage/emulated/0/x", cards) == ("/storage/emulated/0/x", "")
    assert map_root("/storage/C0DE-CAFE/Saves", cards) == ("/storage/1A2B-3C4D/Saves", "")
    assert map_root("/storage/1a2b-3c4d/Saves", cards) == ("/storage/1a2b-3c4d/Saves", "")
    assert map_root("/storage/C0DE-CAFE", cards) == ("/storage/1A2B-3C4D", "")
    assert map_root("/storage/C0DE-CAFE/Saves", [])[0] is None
    assert map_root("/storage/C0DE-CAFE/Saves", ["1A2B-3C4D", "9999-AAAA"])[0] is None
    assert map_root("/storage/C0DE-CAFE/Saves", ["C0DE-CAFE", "9999-AAAA"]) == ("/storage/C0DE-CAFE/Saves", "")


def test_map_text():
    cards = ["1A2B-3C4D"]
    assert map_text(b"a=/storage/C0DE-CAFE/x\nb=/storage/C0DE-CAFE/y", cards)[0] \
        == b"a=/storage/1A2B-3C4D/x\nb=/storage/1A2B-3C4D/y"
    assert map_text(b"a=/storage/1A2B-3C4D/x", cards)[0] == b"a=/storage/1A2B-3C4D/x"
    assert map_text(b"no paths", []) == (b"no paths", "")
    assert map_text(b"a=/storage/C0DE-CAFE/x/storage/AAAA-BBBB/y", cards)[0] is None
    assert map_text(b"a=/storage/C0DE-CAFE/x", [])[0] is None


def test_json_of_the_sets_is_plain_data(run):
    run("saves", "backup")
    make_set(run.demo, {"/" + CC: b"old"})
    json.dumps(run("--json", "saves", "devices").json)
