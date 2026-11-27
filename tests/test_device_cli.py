"""`jewelcase device`: show the games folder, correct it, detect it again."""
import json

import pytest

from jewelcase import cli

CARD = "/storage/1A2B-3C4D"


@pytest.fixture
def demo(tmp_path):
    return tmp_path / "demo"


def run(capsys, demo, *args):
    code = cli.main(["--demo-dir", str(demo), *args])
    return code, capsys.readouterr().out


def other_folder(demo):
    """A second place on the demo handheld that holds a system folder."""
    folder = demo / "device" / "sdcard" / "ROMs" / "gba"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Only Here (USA).zip").write_bytes(b"zip")


def test_device_prints_the_device_and_how_the_games_folder_was_chosen(capsys, demo):
    code, out = run(capsys, demo, "device")
    assert code == 0 and "RG Demo" in out and f"Games folder: {CARD} (detected)" in out
    code, out = run(capsys, demo, "--json", "device")
    d = json.loads(out)
    assert d["games_folder"] == CARD and d["games_folder_source"] == "detected" and d["kind"] == "handheld"
    assert d["connected"] is True and d["serial"] == "DEMO0001"


def test_games_folder_sets_it_and_it_stays_set(capsys, demo):
    run(capsys, demo, "device")
    other_folder(demo)
    code, out = run(capsys, demo, "device", "--games-folder", "/sdcard/ROMs/")
    assert code == 0 and "now /sdcard/ROMs" in out and "(set by you)" in out
    code, out = run(capsys, demo, "--json", "device")        # a new run: remembered
    assert json.loads(out)["games_folder"] == "/sdcard/ROMs" and json.loads(out)["games_folder_source"] == "set"
    games = json.loads(run(capsys, demo, "--json", "games")[1])
    assert {g["system"] for g in games if g["on_device"]} == {"gba"}


def test_detect_goes_back_to_automatic_detection(capsys, demo):
    run(capsys, demo, "device")
    other_folder(demo)
    run(capsys, demo, "device", "--games-folder", "/sdcard/ROMs")
    code, out = run(capsys, demo, "device", "--detect")
    assert code == 0 and f"Games folder: {CARD} (detected)" in out


def test_a_folder_that_cannot_be_used_is_refused_with_the_reason(capsys, demo):
    run(capsys, demo, "device")
    code, out = run(capsys, demo, "device", "--games-folder", "/sdcard/Nowhere")
    assert code == 2 and "does not exist" in out
    code, out = run(capsys, demo, "device", "--games-folder", "/sdcard/ES-DE")
    assert code == 2 and "no system folders" in out
    assert json.loads(run(capsys, demo, "--json", "device")[1])["games_folder"] == CARD


def test_it_cannot_change_while_a_sync_is_unfinished(capsys, demo):
    run(capsys, demo, "device")
    other_folder(demo)
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=demo)
    s.connect()
    s.store.create_job(s.serial, [{"kind": "refresh"}])
    code, out = run(capsys, demo, "device", "--games-folder", "/sdcard/ROMs")
    assert code == 2 and "sync" in out


def test_both_options_at_once_make_no_sense(capsys, demo):
    code, out = run(capsys, demo, "device", "--detect", "--games-folder", "/x")
    assert code == 2 and "not both" in out
