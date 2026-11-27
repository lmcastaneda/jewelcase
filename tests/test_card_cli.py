"""The command line with `--card PATH`: the demo's library, and a folder as the device."""
import json

import pytest

from jewelcase import cli

KEY = "gba/Metro Fusion (USA)"          # a small game the demo library has and a new card does not


@pytest.fixture
def card(tmp_path):
    root = tmp_path / "SDCARD"
    (root / "gba").mkdir(parents=True)
    return root


def run(capsys, tmp_path, card, *args):
    code = cli.main(["--demo-dir", str(tmp_path / "demo"), "--card", str(card), *args])
    return code, capsys.readouterr().out


def test_status_names_the_card_as_the_device(capsys, tmp_path, card):
    code, out = run(capsys, tmp_path, card, "--json", "status")
    s = json.loads(out)
    assert code == 0 and s["device"]["connected"] is True
    assert s["device"]["model"] == "SDCARD" and s["device"]["transport"] == "card"
    assert s["device"]["serial"].startswith("card-") and s["card"]["total"] > 0
    code, out = run(capsys, tmp_path, card, "status")
    assert "SDCARD" in out and "card or folder" in out and "%" not in out.splitlines()[0]


def test_a_game_is_ticked_and_synced_onto_the_card(capsys, tmp_path, card):
    assert run(capsys, tmp_path, card, "tick", KEY)[0] == 0
    code, out = run(capsys, tmp_path, card, "sync")
    assert code == 2 and "--yes" in out
    code, out = run(capsys, tmp_path, card, "sync", "--yes")
    assert code == 0 and "done" in out and "Saves" not in out
    assert (card / "gba" / "Metro Fusion (USA).zip").is_file()
    code, out = run(capsys, tmp_path, card, "--json", "games", "--system", "gba")
    assert next(g for g in json.loads(out) if g["key"] == KEY)["on_device"] is True


def test_a_card_that_is_not_there_is_not_connected(capsys, tmp_path):
    code, out = run(capsys, tmp_path, tmp_path / "gone", "--json", "status")
    assert code == 0 and json.loads(out)["device"]["connected"] is False
    code, out = run(capsys, tmp_path, tmp_path / "gone", "sync", "--yes")
    assert code in (0, 2)


def test_apps_are_not_available_for_a_card(capsys, tmp_path, card):
    for cmd in ("emulators", "saves", "esde"):
        code, out = run(capsys, tmp_path, card, cmd)
        assert code == 2 and "Not available for a card" in out


def test_a_games_folder_typed_outside_the_card_is_refused_with_a_sentence(capsys, tmp_path, card):
    code, out = run(capsys, tmp_path, card, "device", "--games-folder", "/../x")
    assert code == 2 and "outside the card" in out and "Traceback" not in out


def test_an_empty_card_says_what_to_do_in_status(capsys, tmp_path):
    empty = tmp_path / "EMPTY"
    empty.mkdir()
    code, out = run(capsys, tmp_path, empty, "status")
    assert code == 0 and "The card is empty. Make a folder named ROMs on it to use it." in out
    code, out = run(capsys, tmp_path, empty, "--json", "status")
    assert "The card is empty" in json.loads(out)["device"]["problem"]


@pytest.mark.parametrize("argv", [("covers", "--push"), ("saves", "backup"), ("saves", "restore", "--all"),
                                  ("saves", "restore"), ("saves",)])
def test_commands_that_need_a_handheld_say_so_for_a_card(capsys, tmp_path, card, argv):
    code, out = run(capsys, tmp_path, card, *argv)
    assert code == 2 and "Not available for a card" in out and "Traceback" not in out


def test_the_bios_check_works_on_a_card_by_looking_for_bios_folders_on_it(capsys, tmp_path, card):
    (card / "bios").mkdir()
    (card / "bios" / "scph1001.bin").write_bytes(b"not a real one")
    code, out = run(capsys, tmp_path, card, "--json", "bios")
    assert code == 0 and "PlayStation" in out and "Traceback" not in out
