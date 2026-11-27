"""Saves named by a game ID are linked to the game: in the restore menu's lookup, the Saves page and the CLI."""
import pytest

from jewelcase.core import gameids, saves
from jewelcase.core.demo import make_demo
from jewelcase.core.session import open_session
from gameimages import ps2_disc

from test_saves_cli import Cli

STATE = "armsx2-states/SLUS-20312 (D224D348).00.p2s"
GAME = ("ps2", "Wrath of Kings (USA)")
NAME = "SLUS-20312 (D224D348).00.p2s — Wrath of Kings"


@pytest.fixture
def demo(tmp_path):
    """The demo world with one more PlayStation 2 game, a real (tiny) disc whose ID the demo's save state carries."""
    make_demo(tmp_path / "demo")
    disc = tmp_path / "demo" / "library" / "ps2" / "Wrath of Kings (USA).iso"
    disc.write_bytes(ps2_disc())
    return tmp_path / "demo"


@pytest.fixture
def session(demo):
    s = open_session(demo_dir=demo)
    s.connect()
    s.refresh()
    assert saves.backup(s).complete
    return s


def reads(monkeypatch):
    seen = []
    real = gameids.read_id
    monkeypatch.setattr(gameids, "read_id", lambda path, system: seen.append(path.name) or real(path, system))
    return seen


def test_the_save_state_named_by_the_games_id_belongs_to_the_game_and_the_game_is_read_once(session, monkeypatch):
    seen = reads(monkeypatch)
    assert saves.find_items(session, *GAME, read=False) == [] and seen == []      # nothing is read unless asked
    armsx2 = next(e for e in saves.overview(session) if e.label == "ARMSX2")
    assert seen == [] and {i.item.key: i.item.game for i in armsx2.items}[STATE] is None
    found = saves.find_items(session, *GAME)
    assert [(i.key, i.kind) for i in found] == [(STATE, "state")]
    assert found[0].game == GAME and found[0].shape == "game"
    saves.find_items(session, *GAME)
    assert seen.count("Wrath of Kings (USA).iso") == 1
    assert saves.find_items(session, "ps2", "Clockwork Citadel (USA)") == []      # other games do not get it


def test_the_saves_page_names_the_game_beside_an_id_named_save_and_leaves_other_saves_alone(session):
    saves.read_game_ids(session)
    armsx2 = next(e for e in saves.overview(session) if e.label == "ARMSX2")
    by_key = {i.item.key: i for i in armsx2.items}
    assert by_key[STATE].title == NAME
    card = by_key["armsx2-saves/mcd001.ps2"]
    assert card.title == "mcd001.ps2" and card.item.game is None and card.item.shape == "shared"


def test_a_state_whose_game_is_not_in_the_library_stays_as_it_was(demo):
    (demo / "library" / "ps2" / "Wrath of Kings (USA).iso").unlink()
    s = open_session(demo_dir=demo)
    s.connect()
    s.refresh()
    saves.backup(s)
    saves.read_game_ids(s)
    armsx2 = next(e for e in saves.overview(s) if e.label == "ARMSX2")
    assert next(i for i in armsx2.items if i.item.key == STATE).title == "SLUS-20312 (D224D348).00.p2s"


def test_jewelcase_saves_shows_the_game_and_json_links_it(capsys, demo):
    run = Cli(capsys, demo)
    run("saves", "backup")
    assert run("saves").code == 0 and NAME in run.out
    item = next(i for e in run("--json", "saves").json["emulators"] if e["label"] == "ARMSX2" for i in e["items"]
                if i["key"] == STATE)
    assert item["game"] == "ps2/Wrath of Kings (USA)" and item["title"] == NAME
