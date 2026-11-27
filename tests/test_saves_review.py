"""Fixes from the review of saves: game IDs read only when needed, not remembered after a failure, other
handhelds' sets, undo, settings wording and card ids."""
import shutil
import sys

import pytest

from jewelcase.core import gameids, saves
from jewelcase.core.demo import make_demo
from jewelcase.core.session import open_session
from gameimages import ps2_disc, psp_disc

RETROARCH = "com.retroarch.aarch64"


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- 1. which games are read
@pytest.fixture
def demo(tmp_path):
    make_demo(tmp_path / "demo")
    lib = tmp_path / "demo" / "library"
    put(lib / "ps2" / "Wrath of Kings (USA).iso", ps2_disc())
    put(lib / "psp" / "Portable Quest (USA).iso", psp_disc())
    return tmp_path / "demo"


def connected(demo):
    s = open_session(demo_dir=demo)
    s.connect()
    s.refresh()
    return s


@pytest.fixture
def seen(monkeypatch):
    names = []
    real = gameids.read_id
    monkeypatch.setattr(gameids, "read_id", lambda path, system: names.append(path.name) or real(path, system))
    return names


def test_a_backed_up_settings_file_alone_makes_no_game_be_read(demo, seen):
    put(demo / "device" / "sdcard/Android/data/com.retroarch.aarch64/files/retroarch.cfg", b"video_driver = gl\n")
    s = connected(demo)
    assert saves.backup(s, {RETROARCH}).complete
    assert any(r.source == "retroarch-settings" for r in s.store.save_files(s.serial))
    assert saves.read_game_ids(s) == 0 and seen == []


def test_only_the_systems_of_the_emulator_holding_id_named_saves_are_read(demo, seen):
    s = connected(demo)
    assert saves.backup(s, {"com.armsx2"}).complete
    saves.read_game_ids(s)
    assert "Wrath of Kings (USA).iso" in seen
    assert "Portable Quest (USA).iso" not in seen and not [n for n in seen if n.endswith(".rvz")]


# ---------------------------------------------------------------- 2. "could not read" is not "no ID"
def test_a_missing_tool_is_not_remembered_as_a_disc_without_an_id(world, monkeypatch):
    put(world.lib_root / "gamecube" / "Game (USA).rvz", b"RVZ\x01" + bytes(100))
    world.refresh()
    monkeypatch.setattr(gameids, "_dolphin_tool", lambda: None)
    gameids._attempted.clear()
    gameids.read_missing(world.library, world.store, world.rows(), {"gamecube"})
    f = next(f for f in world.store.lib_files("gamecube") if f.name == "Game (USA).rvz")
    assert world.store.lib_game_id("gamecube", f.name, f.size, f.mtime_ns) is None


def test_a_failed_read_is_tried_once_per_run_not_on_every_ask(world, monkeypatch):
    put(world.lib_root / "gamecube" / "Game (USA).rvz", b"RVZ\x01" + bytes(100))
    world.refresh()
    monkeypatch.setattr(gameids, "_dolphin_tool", lambda: None)
    gameids._attempted.clear()
    calls = []
    real = gameids.read_id
    monkeypatch.setattr(gameids, "read_id", lambda p, system: calls.append(p.name) or real(p, system))
    for _ in range(3):
        gameids.read_missing(world.library, world.store, world.rows(), {"gamecube"})
    assert calls.count("Game (USA).rvz") == 1


def test_a_failing_chdman_is_not_remembered_as_a_disc_without_an_id(tmp_path, monkeypatch):
    tool = tmp_path / "chdman"
    tool.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
    tool.chmod(0o755)
    monkeypatch.setattr(gameids, "_chdman", lambda: str(tool))
    with pytest.raises(OSError):
        gameids.read_id(put(tmp_path / "a.chd", b"MComprHD" + bytes(100)), "ps2")


def test_the_read_ids_of_files_that_left_the_library_are_dropped(world):
    put(world.lib_root / "ps2" / "Wrath (USA).iso", ps2_disc())
    world.refresh()
    gameids.read_missing(world.library, world.store, world.rows(), {"ps2"})
    f = next(f for f in world.store.lib_files("ps2") if f.name == "Wrath (USA).iso")
    assert world.store.lib_game_id("ps2", f.name, f.size, f.mtime_ns) == "SLUS-20312"
    (world.lib_root / "ps2" / "Wrath (USA).iso").unlink()
    world.refresh()
    assert world.store.lib_game_id("ps2", f.name, f.size, f.mtime_ns) is None


# ---------------------------------------------------------------- shared helpers for sets and restores
from test_savesets import (CC, CFG_KEY, CFG_OLD, KEY, OLD, OTHER_CARD, OWN_CARD, RA_CFG, make_set,  # noqa: E402
                           own_latest)
from test_saves_cli import Cli  # noqa: E402
from jewelcase.core.savesources import map_text  # noqa: E402


@pytest.fixture
def run(capsys, tmp_path):
    return Cli(capsys, tmp_path / "demo")


def session(run):
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    return s


# ---------------------------------------------------------------- 3. another handheld's set
def test_a_dry_run_from_another_handheld_hashes_nothing_and_writes_no_rows(run, monkeypatch):
    from jewelcase.core import savebackup
    make_set(run.demo, {"/" + CC: b"old", RA_CFG: b"x = 1"}, registered=False)
    run("saves", "backup")
    hashed = []
    real = savebackup.md5_file
    monkeypatch.setattr(savebackup, "md5_file", lambda p: hashed.append(p) or real(p))
    for args in (("--emulator", "RetroArch"), ("--all",), (KEY,)):
        assert run("saves", "restore", *args, "--from", OLD).code == 2 and "--yes" in run.out
    assert hashed == []
    s = session(run)
    assert s.store.save_files(OLD) == [] and s.store.save_files("OLD00001") == []


def test_a_set_that_cannot_be_read_gives_a_sentence_not_a_traceback(run, monkeypatch):
    from jewelcase.core.savebackup import SaveBackup
    make_set(run.demo, {"/" + CC: b"old"}, registered=False)
    run("saves", "backup")

    def broken(self):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(SaveBackup, "rebuild_index", broken)
    s = session(run)
    other = saves.other_set(s, OLD)
    with pytest.raises(saves.SavesError) as e:
        saves.read_set(s, other)
    assert "could not be read" in str(e.value)
    assert run("saves", "restore", "--all", "--from", OLD, "--yes").code == 2
    assert "could not be read" in run.out


def test_read_set_gives_the_items_and_runs_of_the_set(run):
    stamps = make_set(run.demo, {"/" + CC: (b"v1", b"v2")}, registered=False, runs=2)
    run("saves", "backup")
    s = session(run)
    contents = saves.read_set(s, saves.other_set(s, OLD))
    assert [i.key for i in contents.items] == [KEY] and len(contents.runs) >= 1
    assert s.store.save_files(OLD)                       # the index of a foreign set is keyed by its folder


# ---------------------------------------------------------------- 4. undo only what was captured
def test_undo_does_not_cover_a_file_the_handheld_did_not_have_before(run):
    run("saves", "backup")                                  # this handheld's own set now holds an old copy
    (run.demo / "device" / CC).unlink()                    # ... but the file is not on the device any more
    make_set(run.demo, {"/" + CC: b"from the old handheld"})
    s = session(run)
    out = saves.restore(s, [KEY], from_set=saves.other_set(s, OLD))
    assert out.restored == [KEY] and run.read(CC) == b"from the old handheld"
    assert out.undoable == []


def test_undo_covers_a_file_the_safety_backup_captured(run):
    run("saves", "backup")
    make_set(run.demo, {"/" + CC: b"from the old handheld"})
    s = session(run)
    out = saves.restore(s, [KEY], from_set=saves.other_set(s, OLD))
    assert out.restored == [KEY] and out.undoable == [KEY]


# ---------------------------------------------------------------- 5. card ids are rewritten only for another handheld's set
def test_a_settings_restore_from_the_handhelds_own_set_is_byte_exact(run):
    run.put(RA_CFG.lstrip("/"), CFG_OLD)                    # names a card this handheld does not have
    run("saves", "backup")
    run.put(RA_CFG.lstrip("/"), b"video_vsync = false")
    out = run("--json", "saves", "restore", CFG_KEY, "--yes").json
    assert out["restored"] == [CFG_KEY] and run.read(RA_CFG.lstrip("/")) == CFG_OLD


def test_an_own_set_settings_restore_does_not_need_a_card(run):
    run.put(RA_CFG.lstrip("/"), CFG_OLD)
    run("saves", "backup")
    run.put(RA_CFG.lstrip("/"), b"video_vsync = false")
    shutil.rmtree(run.demo / "device" / "storage" / OWN_CARD)
    out = run("--json", "saves", "restore", CFG_KEY, "--yes").json
    assert out["restored"] == [CFG_KEY] and run.read(RA_CFG.lstrip("/")) == CFG_OLD


# ---------------------------------------------------------------- 6. an emulator that is not installed
def test_settings_in_shared_storage_need_the_emulator_installed(run):
    cfg = "/sdcard/RetroArch/config/Snes9x/Snes9x.opt"
    make_set(run.demo, {cfg: b"snes9x_blargg = disabled"})
    run("saves", "backup")
    s = session(run)
    del s.device.fake_packages["com.retroarch.aarch64"]
    key = "retroarch-core-settings/Snes9x/Snes9x.opt"
    out = saves.restore(s, [key], from_set=saves.other_set(s, OLD))
    assert out.restored == [] and out.failed == [(key, "Install RetroArch first.")]
    assert not (run.demo / "device" / cfg.lstrip("/")).exists()


# ---------------------------------------------------------------- 7. a set is its folder
def test_a_serial_with_a_dash_is_not_cut(run):
    make_set(run.demo, {"/" + CC: b"old"}, serial="AB-CD-01")
    s = session(run)
    [other] = [x for x in saves.backup_sets(s) if not x.own]
    assert (other.name, other.model, other.serial) == ("RG477V-AB-CD-01", "RG477V", "AB-CD-01")


def test_two_folders_with_one_serial_keep_their_own_content_and_are_not_guessed_between(run):
    make_set(run.demo, {"/" + CC: b"from the first"}, serial="OLD00001", model="RG477V")
    make_set(run.demo, {"/" + CC: b"second", "/sdcard/RetroArch/saves/Other (USA).srm": b"only here"},
             serial="OLD00001", model="RG477W")
    s = session(run)
    first, second = (saves.other_set(s, n) for n in ("RG477V-OLD00001", "RG477W-OLD00001"))
    assert [i.key for i in saves.set_items(s, first)] == [KEY]
    assert sorted(i.key for i in saves.set_items(s, second)) == sorted([KEY, "retroarch-saves/Other (USA)"])
    with pytest.raises(saves.SavesError) as e:
        saves.other_set(s, "OLD00001")
    assert "more than one" in str(e.value).lower() and "RG477W-OLD00001" in str(e.value)
    assert run("saves", "restore", "--all", "--from", "OLD00001", "--yes").code == 2
    assert "more than one" in run.out.lower()
    out = saves.restore(s, [KEY], from_set=second)
    assert out.restored == [KEY] and run.read(CC) == b"second"


# ---------------------------------------------------------------- 8. settings switched off
def _unreadable_settings(s):
    s.device.unreadable_files = {RA_CFG}


def test_settings_switched_off_are_not_reported_as_unreadable(run):
    run.put(RA_CFG.lstrip("/"), b"x = 1")
    s = session(run)
    s.cfg.backup_settings = False
    _unreadable_settings(s)
    assert saves.unreadable_now(s, RETROARCH) == ""
    assert saves.unreadable_now_detail(s, RETROARCH) == ("", "")
    saves.backup(s)
    ra = next(e for e in saves.overview(s) if e.package == RETROARCH)
    assert "settings" not in ra.reason and ra.status == "protected"


def test_a_stale_settings_scan_is_ignored_once_the_switch_is_off(run):
    run.put(RA_CFG.lstrip("/"), b"x = 1")
    s = session(run)
    _unreadable_settings(s)
    saves.backup(s)
    assert "1 settings file cannot be read" in next(e for e in saves.overview(s) if e.package == RETROARCH).reason
    s.cfg.backup_settings = False
    assert "settings" not in next(e for e in saves.overview(s) if e.package == RETROARCH).reason


def test_removal_names_settings_that_cannot_be_read(run):
    run.put(RA_CFG.lstrip("/"), b"x = 1")
    s = session(run)
    _unreadable_settings(s)
    assert saves.unreadable_now_detail(s, RETROARCH) == ("", "1 settings file")


# ---------------------------------------------------------------- 9. wording
def test_the_command_line_says_items_when_settings_were_restored(run):
    run.put(RA_CFG.lstrip("/"), b"x = 1")
    run("saves", "backup")
    run.put(RA_CFG.lstrip("/"), b"y = 2")
    assert run("saves", "restore", CFG_KEY, "--yes").out.startswith("Restored 1 item.")
    assert run("saves", "restore", KEY, "--yes").out.startswith("Restored 1 save.")


# ---------------------------------------------------------------- 10. other spellings of a card id
CARDS = ["1A2B-3C4D"]


@pytest.mark.parametrize("before,after", [
    (b'{"p": "\\/storage\\/C0DE-CAFE\\/RetroArch"}', b'{"p": "\\/storage\\/1A2B-3C4D\\/RetroArch"}'),
    (b"dir = /mnt/media_rw/C0DE-CAFE/Games", b"dir = /mnt/media_rw/1A2B-3C4D/Games"),
    (b"u = content://com.android.externalstorage.documents/tree/C0DE-CAFE%3AGames/document/C0DE-CAFE%3AGames%2Fx",
     b"u = content://com.android.externalstorage.documents/tree/1A2B-3C4D%3AGames/document/1A2B-3C4D%3AGames%2Fx"),
])
def test_other_spellings_of_a_card_id_are_pointed_at_this_card(before, after):
    assert map_text(before, CARDS) == (after, "")


def test_spellings_of_one_card_count_as_one_and_of_two_cards_as_two():
    mixed = b"a=/storage/C0DE-CAFE/x\nb=/mnt/media_rw/C0DE-CAFE/y\nc=tree/C0DE-CAFE%3AG"
    assert map_text(mixed, CARDS)[0] == b"a=/storage/1A2B-3C4D/x\nb=/mnt/media_rw/1A2B-3C4D/y\nc=tree/1A2B-3C4D%3AG"
    assert map_text(b"a=/storage/C0DE-CAFE/x\nb=/mnt/media_rw/9999-AAAA/y", CARDS)[0] is None
    assert map_text(b"a=/mnt/media_rw/1A2B-3C4D/x tree/1A2B-3C4D%3AG", CARDS)[0] == b"a=/mnt/media_rw/1A2B-3C4D/x tree/1A2B-3C4D%3AG"
