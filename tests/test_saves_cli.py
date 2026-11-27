"""`jewelcase saves …` and the facade the window uses, against the demo world."""
import json
import os

import pytest

from jewelcase import cli
from jewelcase.core import config, saves
from jewelcase.core.session import open_session

RA = "sdcard/RetroArch/saves"
CC = RA + "/Chrono Compass (USA).srm"
KEY = "retroarch-saves/Chrono Compass (USA)"
CARD_KEY = "armsx2-saves/mcd001.ps2"


class Cli:
    def __init__(self, capsys, demo):
        self.capsys, self.demo = capsys, demo
        self("status")                                   # creates the demo world

    def __call__(self, *args):
        self.code = cli.main(["--demo-dir", str(self.demo), *args])
        captured = self.capsys.readouterr()
        self.out, self.err = captured.out, captured.err
        return self

    @property
    def json(self):
        return json.loads(self.out)

    def put(self, rel: str, data: bytes) -> None:
        p = self.demo / "device" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        t = p.stat().st_mtime + 120 + len(data)
        os.utime(p, (t, t))

    def read(self, rel: str) -> bytes:
        return (self.demo / "device" / rel).read_bytes()

    def emulator(self, label: str) -> dict:
        return next(e for e in self("--json", "saves").json["emulators"] if e["label"] == label)


@pytest.fixture
def run(capsys, tmp_path):
    return Cli(capsys, tmp_path / "demo")


def test_saves_backup_then_status(run):
    assert run("--json", "saves").json["last_backup"] is None
    assert run("saves", "backup").code == 0 and "copied" in run.out
    data = run("--json", "saves").json
    assert data["last_backup"] and data["library_online"] is True
    ra = next(e for e in data["emulators"] if e["label"] == "RetroArch")
    assert ra["status"] == "protected" and ra["unreadable"] == 0
    cc = next(i for i in ra["items"] if i["key"] == KEY)
    assert cc["versions"] == 1 and cc["kind"] == "Save" and cc["game"] == "snes/Chrono Compass (USA)"
    assert cc["title"] == "Chrono Compass" and cc["gone"] is False and cc["size"] > 0
    assert any(i["kind"] == "Save State" for i in ra["items"])
    card = next(i for i in run.emulator("ARMSX2")["items"] if i["key"] == CARD_KEY)
    assert card["kind"] == "Memory Card" and card["game"] is None


def test_backup_json_and_second_run(run):
    first = run("--json", "saves", "backup").json
    assert first["complete"] is True and first["copied"] >= 6 and first["skipped"] == []
    second = run("--json", "saves", "backup").json
    assert second["copied"] == 0 and second["unchanged"] == first["copied"]


def test_status_names_what_is_not_protected(run):
    run("saves", "backup")
    psp = run.emulator("PPSSPP")
    assert psp["status"] == "not_protected" and psp["unreadable"] == 2 and psp["items"] == []
    assert psp["reason"] == "2 save folders cannot be read" and "memory stick" in psp["advice"]
    assert run.emulator("M64Plus FZ Pro")["status"] == "not_reachable"
    assert run.emulator("DuckStation")["status"] == "nothing"
    text = run("saves").out
    assert "PPSSPP · Not Protected · 2 save folders cannot be read" in text
    assert "RetroArch · Protected" in text and "Chrono Compass" in text


def test_versions_and_restore(run):
    run("saves", "backup")
    run.put(CC, b"second save")
    run("saves", "backup")
    found = run("--json", "saves", "versions", KEY).json
    assert len(found) == 2 and found[0]["current"] is True and found[0]["run"] > found[1]["run"]
    assert run("saves", "restore", KEY, "--at", found[1]["run"], "--yes").code == 0
    assert "Restored" in run.out and run.read(CC) != b"second save"
    undo = run("--json", "saves", "restore", KEY, "--at", found[0]["run"], "--yes").json
    assert undo["restored"] == [KEY] and undo["failed"] == [] and run.read(CC) == b"second save"


def test_restore_needs_yes(run):
    run("saves", "backup")
    before = run.read(CC)
    run.put(CC, b"newer")
    assert run("saves", "restore", KEY).code == 2 and "--yes" in run.out
    assert run.read(CC) == b"newer" != before


def test_unknown_save_key(run):
    run("saves", "backup")
    assert run("saves", "restore", "retroarch-saves/Nope", "--yes").code == 2 and "No such save" in run.out
    assert run("saves", "versions", "retroarch-saves/Nope").code == 2


def test_restore_emulator_and_all(run):
    first = run("--json", "saves", "backup").json["run"]
    run.put(CC, b"spoiled")
    run.put("sdcard/Android/data/com.armsx2/files/memcards/mcd001.ps2", b"blank card")
    run("saves", "backup")
    out = run("--json", "saves", "restore", "--emulator", "ARMSX2", "--at", first, "--yes").json
    assert CARD_KEY in out["restored"] and KEY not in out["restored"]
    assert run.read("sdcard/Android/data/com.armsx2/files/memcards/mcd001.ps2") != b"blank card"
    assert run.read(CC) == b"spoiled"
    out = run("--json", "saves", "restore", "--all", "--at", first, "--yes").json
    assert KEY in out["restored"] and run.read(CC) != b"spoiled"
    assert run("saves", "restore", "--emulator", "Nope", "--yes").code == 2


def test_restore_reports_what_failed(run):
    run("saves", "backup")
    run.put(CC, b"spoiled")
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.device.foreground = "com.retroarch.aarch64"
    result = saves.restore(s, [KEY])
    assert result.failed == [(KEY, "Close RetroArch on the handheld first.")]


def test_library_offline(run):
    os.rename(run.demo / "library", run.demo / "library-away")
    (run.demo / "library").mkdir()                   # an unmounted disk: the folder is there, empty
    assert run("saves", "backup").code == 2
    assert "The library disk is not connected, so saves are not being backed up." in run.out
    assert run("--json", "saves").json["library_online"] is False


def test_sync_backs_up_afterwards(run):
    key = next(g["key"] for g in run("--json", "games").json if g["state"] == "library_only")
    run("tick", key)
    assert run("sync", "--yes").code == 0
    assert "Saves backed up" in run.out
    assert run("--json", "saves").json["last_backup"]


def test_sync_json_stays_json(run):
    key = next(g["key"] for g in run("--json", "games").json if g["state"] == "library_only")
    run("tick", key)
    data = run("--json", "sync", "--yes").json
    assert data["state"] == "done" and data["saves"]["copied"] >= 6


def test_failed_backup_does_not_fail_the_sync(run):
    (run.demo / "library" / "_saves").write_text("in the way")
    key = next(g["key"] for g in run("--json", "games").json if g["state"] == "library_only")
    run("tick", key)
    assert run("sync", "--yes").code == 0
    assert "Saves were not backed up" in run.out and "Sync done" in run.out


def test_save_states_switch(run):
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    s.cfg.backup_save_states = False
    r = saves.backup(s)
    assert r.complete and r.copied >= 5
    kinds = {i.item.kind for e in saves.overview(s) for i in e.items}
    assert kinds == {"save"}


def test_find_items_for_a_game(run):
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    saves.backup(s)
    found = saves.find_items(s, "snes", "Chrono Compass (USA)")
    assert sorted(i.kind for i in found) == ["save", "state"]
    assert saves.find_items(s, "snes", "Super Plumber Land (USA)") == []


def test_psp_folder_titles_come_from_param_sfo(run, tmp_path):
    from test_savesources import make_sfo
    run.put("sdcard/PSP/SAVEDATA/ULUS10285000/PARAM.SFO", make_sfo({"TITLE": "Chains of Dusk"}))
    run.put("sdcard/PSP/SAVEDATA/ULUS10285000/DATA.BIN", b"psp save")
    run("saves", "backup")
    item = next(i for i in run.emulator("PPSSPP")["items"] if i["key"] == "ppsspp-shared-saves/ULUS10285000")
    assert item["title"] == "Chains of Dusk" and run.emulator("PPSSPP")["status"] == "partly"


def test_backup_runs_one_at_a_time(run):
    from jewelcase.core.sync import Busy, job_lock
    s = open_session(demo_dir=run.demo)
    s.connect()
    with job_lock(s.data_dir):
        with pytest.raises(Busy):
            saves.backup(s)


def test_config_remembers_the_save_state_switch(tmp_path):
    cfg = config.Config(library=tmp_path)
    assert cfg.backup_save_states is True
    cfg.backup_save_states = False
    config.save(cfg, tmp_path / "config.toml")
    assert config.load(tmp_path / "config.toml").backup_save_states is False
    (tmp_path / "plain.toml").write_text('library = "/x"\n')
    assert config.load(tmp_path / "plain.toml").backup_save_states is True


def test_files_that_cannot_be_opened_count_as_not_protected(run):
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    cards = "/sdcard/Android/data/com.armsx2/files/memcards/"
    s.device.unreadable_files = {cards + "mcd001.ps2", cards + "mcd002.ps2"}
    r = saves.backup(s)
    assert r.complete and r.skipped == [] and r.blocked == {"armsx2-saves": 2}
    armsx2 = next(e for e in saves.overview(s) if e.label == "ARMSX2")
    assert armsx2.status == "partly" and armsx2.blocked == 2 and armsx2.unreadable == 0
    assert armsx2.reason == "1 save state; 2 files cannot be read"
    psp = next(e for e in saves.overview(s) if e.label == "PPSSPP")
    assert psp.reason == "2 save folders cannot be read" and psp.blocked == 0
    assert saves.cannot_back_up(s, "com.armsx2") == "2 files" and saves.cannot_back_up(s, "org.ppsspp.ppsspp") == "2 save folders"
    assert saves.cannot_back_up(s, "com.retroarch.aarch64") == ""


# ---------------------------------------------------------------- found by the branch review
def test_unreadable_saves_are_found_without_an_earlier_backup(run):
    s = open_session(demo_dir=run.demo)
    s.connect()
    assert saves.cannot_back_up(s, "org.ppsspp.ppsspp") == ""            # nothing has scanned yet
    assert saves.unreadable_now(s, "org.ppsspp.ppsspp") == "2 save folders"
    assert saves.unreadable_now(s, "com.armsx2") == "" and saves.unreadable_now(s, "no.such.app") == ""


def test_files_skipped_by_the_last_backup_lower_the_status(run, monkeypatch):
    from jewelcase.core import savesources
    monkeypatch.setattr(savesources, "MAX_FILE", 9 * 1024 * 1024)
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    assert saves.backup(s).complete
    armsx2 = next(e for e in saves.overview(s) if e.label == "ARMSX2")
    assert armsx2.status == "partly"
    assert armsx2.skipped == (("SLUS-20312 (D224D348).00.p2s", "larger than 256 MB"),)
    assert armsx2.reason == "2 memory cards; 1 file could not be copied"
    assert next(e for e in saves.overview(s) if e.label == "RetroArch").status == "protected"


def test_restore_at_must_name_a_backup_run(run):
    run("saves", "backup")
    run.put(CC, b"second save")
    assert run("saves", "restore", KEY, "--at", "yesterday", "--yes").code == 2
    assert "not a backup run" in run.out and run.read(CC) == b"second save"
    assert run("saves", "restore", "--all", "--at", "2026-11-26", "--yes").code == 2


# ---------------------------------------------------------------- emulator settings
RA_CFG = "sdcard/Android/data/com.retroarch.aarch64/files/retroarch.cfg"
CORE_OPT = "sdcard/RetroArch/config/Snes9x/Snes9x.opt"
CFG_KEY = "retroarch-settings/retroarch.cfg"
OPT_KEY = "retroarch-core-settings/Snes9x/Snes9x.opt"


def test_settings_are_backed_up_and_listed_as_their_own_kind(run):
    run.put(RA_CFG, b"video_vsync = true")
    run.put(CORE_OPT, b"snes9x_hires = enabled")
    run.put("sdcard/RetroArch/overlays/pad.cfg", b"not a setting")
    run("saves", "backup")
    ra = run.emulator("RetroArch")
    found = {i["key"]: i for i in ra["items"]}
    assert found[CFG_KEY]["kind"] == "Settings" and found[OPT_KEY]["kind"] == "Settings"
    assert not [k for k in found if "overlays" in k]
    assert found[CFG_KEY]["title"] == "retroarch.cfg" and found[CFG_KEY]["game"] is None
    assert ra["status"] == "protected" and ra["reason"].endswith("2 settings files")
    assert "Settings" in run("saves").out


def test_settings_are_restored_per_emulator_and_with_all(run):
    run.put(RA_CFG, b"video_vsync = true")
    first = run("--json", "saves", "backup").json["run"]
    run.put(RA_CFG, b"video_vsync = false")
    run.put(CC, b"spoiled")
    run("saves", "backup")
    run.put(RA_CFG, b"my newest, not backed up")
    out = run("--json", "saves", "restore", "--emulator", "RetroArch", "--at", first, "--yes").json
    assert CFG_KEY in out["restored"] and out["failed"] == []
    assert run.read(RA_CFG) == b"video_vsync = true"
    undo = run("--json", "saves", "restore", CFG_KEY, "--at", out["undo_run"], "--yes").json
    assert undo["restored"] == [CFG_KEY] and run.read(RA_CFG) == b"my newest, not backed up"


def test_settings_wait_for_a_closed_emulator(run):
    run.put(RA_CFG, b"video_vsync = true")
    run("saves", "backup")
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.device.foreground = "com.retroarch.aarch64"
    assert saves.restore(s, [CFG_KEY]).failed == [(CFG_KEY, "Close RetroArch on the handheld first.")]


def test_the_settings_switch(run):
    run.put(RA_CFG, b"video_vsync = true")
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    assert s.cfg.backup_settings is True
    s.cfg.backup_settings = False
    assert saves.backup(s).complete
    assert {i.item.kind for e in saves.overview(s) for i in e.items} == {"save", "state"}
    s.cfg.backup_settings = True
    saves.backup(s)
    assert {i.item.kind for e in saves.overview(s) for i in e.items} == {"save", "state", "settings"}


def test_config_remembers_the_settings_switch(tmp_path):
    cfg = config.Config(library=tmp_path)
    assert cfg.backup_settings is True
    cfg.backup_settings = False
    config.save(cfg, tmp_path / "config.toml")
    assert config.load(tmp_path / "config.toml").backup_settings is False
    (tmp_path / "plain.toml").write_text('library = "/x"\n')
    assert config.load(tmp_path / "plain.toml").backup_settings is True


def test_settings_that_cannot_be_read_are_counted_apart_from_saves(run):
    run.put(RA_CFG, b"video_vsync = true")
    s = open_session(demo_dir=run.demo)
    s.connect()
    s.refresh()
    s.device.unreadable_files = {"/" + RA_CFG}
    r = saves.backup(s)
    assert r.complete and r.blocked == {"retroarch-settings": 1}
    ra = next(e for e in saves.overview(s) if e.label == "RetroArch")
    assert ra.blocked == 0 and ra.status == "partly" and "1 settings file cannot be read" in ra.reason
    assert saves.cannot_back_up(s, "com.retroarch.aarch64") == ""        # the Remove dialog is about saves
