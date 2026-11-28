import json

import pytest

from jewelcase import cli


def run(capsys, demo, *args):
    code = cli.main(["--demo-dir", str(demo), *args])
    out = capsys.readouterr().out
    return code, out


def run_json(capsys, demo, *args):
    code, out = run(capsys, demo, "--json", *args)
    assert code == 0, out
    return json.loads(out)


@pytest.fixture
def demo(tmp_path):
    return tmp_path / "demo"


def test_status_json(capsys, demo):
    s = run_json(capsys, demo, "status")
    assert s["device"]["connected"] is True and s["device"]["serial"] == "DEMO0001"
    assert s["device"]["battery"] == 81 and s["device"]["power"] == "usb"
    assert s["card"]["free"] > 0 and s["card"]["total"] == 2048 * 1024 * 1024
    assert s["library"]["online"] is True
    assert s["games"]["on_device"] > 20 and s["games"]["library_only"] > 5
    assert s["pending"] == {"add": 0, "remove": 0}


def test_status_text(capsys, demo):
    code, out = run(capsys, demo, "status")
    assert code == 0 and "RG Demo" in out and "81%" in out


def test_games_never_played_json(capsys, demo):
    games = run_json(capsys, demo, "games", "--list", "never_played")
    assert len(games) >= 8
    assert all(g["on_device"] and g["seconds"] < 60 for g in games)
    assert {"key", "title", "system", "state", "size", "plays", "seconds", "last_played", "want"} <= set(games[0])
    ps2 = run_json(capsys, demo, "games", "--system", "ps2")
    assert ps2 and all(g["system"] == "ps2" for g in ps2)


def test_untick_list_then_plan_json(capsys, demo):
    never = {g["key"] for g in run_json(capsys, demo, "games", "--list", "never_played")}
    code, out = run(capsys, demo, "untick", "--list", "never_played")
    assert code == 0 and str(len(never)) in out
    plan = run_json(capsys, demo, "plan")
    assert {i["key"] for i in plan["remove"]} == never and plan["add"] == []
    assert plan["fits"] is True and plan["free_after"] > plan["free_before"]


def test_tick_list_puts_the_whole_list_back(capsys, demo):
    never = {g["key"] for g in run_json(capsys, demo, "games", "--list", "never_played")}
    run(capsys, demo, "untick", "--list", "never_played")
    assert {i["key"] for i in run_json(capsys, demo, "plan")["remove"]} == never
    code, out = run(capsys, demo, "tick", "--list", "never_played")
    assert code == 0 and str(len(never)) in out
    plan = run_json(capsys, demo, "plan")
    assert plan["remove"] == [] and plan["add"] == []


def test_tick_without_keys_or_list_says_what_to_name(capsys, demo):
    code, out = run(capsys, demo, "tick")
    assert code == 2


def test_tick_unknown_key_fails(capsys, demo):
    code, out = run(capsys, demo, "tick", "ps2/No Such Game")
    assert code == 2


def test_sync_requires_yes(capsys, demo):
    run(capsys, demo, "untick", "gba/Minish Hat (USA)")
    code, out = run(capsys, demo, "sync")
    assert code == 2 and "--yes" in out
    assert (demo / "device/storage/1A2B-3C4D/gba/Minish Hat (USA).zip").exists()


def test_sync_demo_removes_and_adds(capsys, demo):
    run(capsys, demo, "untick", "gba/Minish Hat (USA)", "snes/Chrono Compass (USA)")
    run(capsys, demo, "tick", "gba/Golden Dawn (USA, Europe)")
    code, out = run(capsys, demo, "sync", "--yes")
    assert code == 0, out
    card = demo / "device/storage/1A2B-3C4D"
    assert not (card / "gba/Minish Hat (USA).zip").exists()
    assert not (card / "snes/Chrono Compass (USA).zip").exists()
    assert (card / "gba/Golden Dawn (USA, Europe).zip").exists()
    after = {g["key"]: g for g in run_json(capsys, demo, "games")}
    assert after["gba/Minish Hat (USA)"]["state"] == "library_only"
    assert after["gba/Minish Hat (USA)"]["seconds"] == int(5.5 * 3600)      # history kept
    assert after["gba/Golden Dawn (USA, Europe)"]["state"] == "on_device"
    assert run_json(capsys, demo, "plan")["add"] == []


def test_sync_nothing_to_do(capsys, demo):
    code, out = run(capsys, demo, "sync", "--yes")
    assert code == 0 and "Nothing to do" in out


def test_copy_to_library(capsys, demo):
    code, out = run(capsys, demo, "copy-to-library", "ps2/Glass Garden (USA)")
    assert code == 0, out
    assert (demo / "library/ps2/Glass Garden (USA).chd").exists()


def leave_unfinished_sync(demo):
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=demo)
    s.connect()
    s.refresh()
    job = s.runner().start(s.plan())
    s.store.close()
    return job


def test_sync_refuses_while_an_earlier_one_is_unfinished(capsys, demo):
    run(capsys, demo, "untick", "gba/Minish Hat (USA)")
    leave_unfinished_sync(demo)
    code, out = run(capsys, demo, "sync", "--yes")
    assert code == 2 and "not finished" in out and "--resume" in out
    assert (demo / "device/storage/1A2B-3C4D/gba/Minish Hat (USA).zip").exists()
    code, out = run(capsys, demo, "sync", "--resume")
    assert code == 0, out
    assert not (demo / "device/storage/1A2B-3C4D/gba/Minish Hat (USA).zip").exists()


def test_sync_discard_drops_the_unfinished_one(capsys, demo):
    run(capsys, demo, "untick", "gba/Minish Hat (USA)")
    leave_unfinished_sync(demo)
    code, out = run(capsys, demo, "sync", "--discard")
    assert code == 0 and "Discarded" in out
    assert (demo / "device/storage/1A2B-3C4D/gba/Minish Hat (USA).zip").exists()
    assert len(run_json(capsys, demo, "plan")["remove"]) == 1       # the tick is still there


def test_sync_says_what_happened_to_es_de(capsys, demo):
    run(capsys, demo, "tick", "gba/Golden Dawn (USA, Europe)")
    code, out = run(capsys, demo, "sync", "--yes")
    assert code == 0 and "ES-DE shows the changes when it is next opened." in out      # it was not in front
    run(capsys, demo, "tick", "gba/Metro Fusion (USA)")
    assert run_json(capsys, demo, "sync", "--yes")["esde"] == "ES-DE shows the changes when it is next opened"


# ---------------------------------------------------------------- converting archives
import shutil                                               # noqa: E402

needs_7z = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")
SAGA = "ps2/Northwind Saga (USA)"


@pytest.fixture
def no_chdman(monkeypatch):
    from jewelcase.core import convert
    monkeypatch.setattr(convert, "_chdman", lambda: None)


@needs_7z
def test_plan_says_which_additions_are_converted_first(capsys, demo, no_chdman):
    run(capsys, demo, "tick", SAGA)
    add = run_json(capsys, demo, "plan")["add"]
    assert [(a["key"], a["files"], a["convert"]) for a in add] == [
        (SAGA, ["Northwind Saga (USA).iso"], "Northwind Saga (USA).zip")]
    _, out = run(capsys, demo, "plan")
    assert "Northwind Saga — converted from Northwind Saga (USA).zip first" in out


@needs_7z
def test_convert_makes_the_game_beside_its_archive_without_a_sync(capsys, demo, no_chdman):
    code, out = run(capsys, demo, "convert", SAGA)
    assert code == 0 and out.strip() == "Northwind Saga: made Northwind Saga (USA).iso (8.0 MB) beside its archive."
    assert sorted(p.name for p in (demo / "library/ps2").iterdir() if "Northwind" in p.name) == [
        "Northwind Saga (USA).iso", "Northwind Saga (USA).zip"]
    states = {g["key"]: g["state"] for g in run_json(capsys, demo, "games", "--system", "ps2")}
    assert states[SAGA] == "library_only"
    assert not (demo / "device/storage/1A2B-3C4D/ps2/Northwind Saga (USA).iso").exists()


@needs_7z
def test_convert_json_lists_what_was_made(capsys, demo, no_chdman):
    done = run_json(capsys, demo, "convert", SAGA)
    assert done == {"converted": [{"key": SAGA, "files": ["Northwind Saga (USA).iso"], "bytes": 8 * 1024 * 1024}],
                    "failed": []}


def test_convert_says_when_there_is_nothing_to_convert(capsys, demo):
    code, out = run(capsys, demo, "convert", "ps2/Ember & Ash (USA)", "ps2/Glass Garden (USA)")
    assert code == 2
    assert out.splitlines() == ["Ember & Ash: the library already has it in a form the handheld plays.",
                                "Glass Garden: the library has no archive of it."]


@needs_7z
def test_sync_converts_a_ticked_archive_and_copies_it(capsys, demo, no_chdman):
    run(capsys, demo, "tick", SAGA)
    code, out = run(capsys, demo, "sync", "--yes")
    assert code == 0, out
    made = demo / "device/storage/1A2B-3C4D/ps2/Northwind Saga (USA).iso"
    assert made.read_bytes() == (demo / "library/ps2/Northwind Saga (USA).iso").read_bytes() and made.stat().st_size
    states = {g["key"]: g["state"] for g in run_json(capsys, demo, "games", "--system", "ps2")}
    assert states[SAGA] == "on_device"


def test_convert_leaves_a_game_alone_that_the_library_can_already_play(capsys, demo):
    import zipfile
    run(capsys, demo, "status")
    with zipfile.ZipFile(demo / "library/ps2/Ember & Ash (USA).zip", "w") as z:
        z.writestr("Ember & Ash (USA).iso", bytes(4096))
    before = sorted(p.name for p in (demo / "library/ps2").iterdir())
    code, out = run(capsys, demo, "convert", "ps2/Ember & Ash (USA)")
    assert code == 2 and out.strip() == "Ember & Ash: the library already has it in a form the handheld plays."
    assert sorted(p.name for p in (demo / "library/ps2").iterdir()) == before


def test_covers_counts_say_where_the_covers_come_from(capsys, demo):
    s = run_json(capsys, demo, "covers")
    assert s["from_esde"] >= 1 and s["downloaded"] == 0 and s["none"] > 0 and "arrived" not in s


def test_covers_download_asks_the_network_only_for_games_without_a_cover(capsys, demo, monkeypatch):
    from jewelcase.core import covers
    asked = []
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 12 + b"\0\0\0\0IEND\xaeB`\x82"

    def get(url):
        asked.append(url)
        return png if "Northwind" in url else None
    monkeypatch.setattr(covers, "net_get", get)
    before = run_json(capsys, demo, "covers")
    assert asked == []                                      # counting asks nothing
    after = run_json(capsys, demo, "covers", "--download")
    assert after["arrived"] == 1 and after["downloaded"] == 1 and after["none"] == before["none"] - 1
    assert after["from_esde"] == before["from_esde"]
    assert not any("God%20of%20War" in u for u in asked)    # it has a cover already
    code, out = run(capsys, demo, "covers")
    assert code == 0 and "Downloaded: 1" in out


# ---------------------------------------------------------------- a disc image the library already has
@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman not installed")
def test_convert_makes_a_chd_of_a_loose_iso_and_keeps_the_iso(capsys, demo):
    import re
    run(capsys, demo, "status")
    iso = demo / "library/ps2/Loose Disc (USA).iso"
    iso.write_bytes(b"iso-" * 5120)
    code, out = run(capsys, demo, "convert", "ps2/Loose Disc (USA)")
    assert code == 0, out
    assert re.fullmatch(r"Loose Disc: made Loose Disc \(USA\)\.chd \(.+\) beside the original, which is kept\.\n?", out)
    assert iso.read_bytes() == b"iso-" * 5120 and (demo / "library/ps2/Loose Disc (USA).chd").is_file()
    code, out = run(capsys, demo, "convert", "ps2/Loose Disc (USA)")          # nothing better to make now
    assert code == 2 and out.strip() == "Loose Disc: the library already has it in a form the handheld plays."


def test_convert_leaves_a_loose_iso_alone_without_chdman(capsys, demo, no_chdman):
    run(capsys, demo, "status")
    (demo / "library/ps2/Loose Disc (USA).iso").write_bytes(b"iso-" * 5120)
    code, out = run(capsys, demo, "convert", "ps2/Loose Disc (USA)")
    assert code == 2 and out.strip() == "Loose Disc: the library already has it in a form the handheld plays."
    assert sorted(p.name for p in (demo / "library/ps2").iterdir() if "Loose" in p.name) == ["Loose Disc (USA).iso"]


# ---------------------------------------------------------------- the owner's own smart lists
@pytest.fixture
def with_lists(monkeypatch):
    from jewelcase.core.smartlists import UserList
    real = cli.open_session

    def open_with_lists(*args, **kw):
        s = real(*args, **kw)
        s.cfg.user_lists = [UserList("user:pocket-sized", "Pocket Sized", size="at_most", size_mb=2, on_device=True),
                            UserList("user:ps2-only", "PS2 only", systems=("ps2",))]
        return s
    monkeypatch.setattr(cli, "open_session", open_with_lists)


def test_lists_shows_every_list_with_its_count(capsys, demo, with_lists):
    lists = run_json(capsys, demo, "lists")
    ids = [x["id"] for x in lists]
    assert ids == ["never_played", "not_played", "most_played", "largest", "device_only",
                   "user:pocket-sized", "user:ps2-only"]
    by_id = {x["id"]: x for x in lists}
    assert by_id["user:pocket-sized"] == {"id": "user:pocket-sized", "name": "Pocket Sized", "builtin": False,
                                          "count": by_id["user:pocket-sized"]["count"]}
    assert by_id["never_played"]["builtin"] is True
    assert by_id["user:ps2-only"]["count"] == len(run_json(capsys, demo, "games", "--system", "ps2"))
    assert by_id["user:pocket-sized"]["count"] > 0
    code, out = run(capsys, demo, "lists")
    assert code == 0 and "Pocket Sized" in out and "user:pocket-sized" in out and "Never Played" in out


def test_games_list_takes_the_owners_lists_by_id_or_by_name(capsys, demo, with_lists):
    by_id = run_json(capsys, demo, "games", "--list", "user:pocket-sized")
    by_name = run_json(capsys, demo, "games", "--list", "pocket sized")
    assert by_id == by_name and by_id
    assert all(g["on_device"] and g["size"] <= 2 * 1024 * 1024 for g in by_id)
    assert run_json(capsys, demo, "games", "--list", "Never Played") == run_json(
        capsys, demo, "games", "--list", "never_played")


def test_an_unknown_list_is_refused_and_names_the_choices(capsys, demo, with_lists):
    code, out = run(capsys, demo, "games", "--list", "nothing like it")
    assert code == 2 and "No such list" in out and "user:ps2-only" in out
    code, out = run(capsys, demo, "untick", "--list", "nothing like it")
    assert code == 2 and "No such list" in out


def test_an_unknown_system_is_refused_and_names_the_choices(capsys, demo):
    code, out = run(capsys, demo, "games", "--system", "nothing-like-it")
    assert code == 2 and "No such system: nothing-like-it" in out and "ps2" in out


def test_a_known_system_with_no_games_left_after_the_list_still_says_no_games(capsys, demo, with_lists):
    # "PS2 only" holds nothing from any other system, so the list empties that system by construction.
    system = next(g["system"] for g in run_json(capsys, demo, "games") if g["system"] != "ps2")
    assert run_json(capsys, demo, "games", "--system", system)
    code, out = run(capsys, demo, "games", "--system", system, "--list", "user:ps2-only")
    assert code == 0 and out.strip() == "No games."


def test_untick_list_takes_the_owners_lists(capsys, demo, with_lists):
    chosen = {g["key"] for g in run_json(capsys, demo, "games", "--list", "Pocket Sized")}
    code, out = run(capsys, demo, "untick", "--list", "Pocket Sized")
    assert code == 0 and f"Unticked {len(chosen)} games" in out


@needs_7z
def test_convert_gives_a_one_line_answer_when_7z_cannot_be_started(capsys, demo, no_chdman, monkeypatch, tmp_path):
    from jewelcase.core import convert
    monkeypatch.setattr(convert, "_seven_zip", lambda: str(tmp_path / "no-such-7z"))
    code, out = run(capsys, demo, "convert", SAGA)
    assert code == 2 and len(out.splitlines()) == 1 and out.startswith("Northwind Saga: 7z could not be started")


@needs_7z
def test_convert_gives_a_one_line_answer_when_a_tool_goes_missing_midway(capsys, demo, no_chdman, monkeypatch):
    from jewelcase.core import convert
    from jewelcase.core.library import ToolMissing

    def gone(*a, **k):
        raise ToolMissing("7z is not installed")
    monkeypatch.setattr(convert, "run", gone)
    code, out = run(capsys, demo, "convert", SAGA)
    assert code == 2 and out.splitlines() == ["Northwind Saga: 7z is not installed."]


def test_an_unreadable_settings_file_is_said_once_on_stderr(capsys, demo, monkeypatch):
    from jewelcase import cli
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=demo)
    s.cfg.unreadable = True
    monkeypatch.setattr(cli, "open_session", lambda **kw: s)
    cli.main(["status"])
    err = capsys.readouterr().err
    assert err.count("The settings file could not be read") == 1 and "config.toml.broken" in err


# ---------------------------------------------------------------- the version, for bug reports
def test_version_is_printed_without_opening_anything(capsys):
    import jewelcase
    with pytest.raises(SystemExit) as stop:
        cli.main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"jewelcase {jewelcase.__version__}"


def test_the_version_is_the_one_the_project_file_states():
    import tomllib
    from pathlib import Path

    import jewelcase
    project = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    assert jewelcase.__version__ == project["project"]["version"]
