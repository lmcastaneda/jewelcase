"""Correcting the games folder that detection picked."""
import pytest

from jewelcase.core import gamesfolder
from jewelcase.core.catalog import FolderError
from jewelcase.core.config import Config
from jewelcase.core.library import Library
from jewelcase.core.session import Session

from conftest import CARD, write_tree

SD = "/sdcard/ROMs"


@pytest.fixture
def s(world, tmp_path):
    """A session on the world's handheld, whose card holds ps2, psx and gamecube; internal storage has a ROMs
    folder with one system in it."""
    write_tree(world.dev.root / SD.lstrip("/"), {"gba": {"Old (USA).zip": b"zip"}})
    return session_for(world, tmp_path)


def session_for(world, tmp_path):
    cfg = Config(library=world.lib_root, inbox=tmp_path / "inbox")
    s = Session(cfg, world.store, Library(world.lib_root, world.store), tmp_path / "data",
                demo_device=world.dev)
    assert s.connect()
    s.refresh()
    return s


def test_detection_picks_the_folder_with_most_systems_and_says_it_was_detected(s):
    assert s.profile.rom_root == CARD
    assert gamesfolder.source(s) == "detected"


def test_the_candidates_are_the_folders_detection_considered_with_their_counts(s):
    found = {c.path: c for c in gamesfolder.choices(s)}
    assert list(found) == [CARD, SD]
    assert found[CARD].systems == 3 and found[CARD].games == 10
    assert found[SD].systems == 1 and found[SD].games == 1


def test_the_current_folder_is_a_choice_even_when_detection_would_not_list_it(s):
    write_tree(s.device.root / "sdcard/Games", {"snes": {"a.zip": b"1"}})
    gamesfolder.set_folder(s, "/sdcard/Games")
    assert [c.path for c in gamesfolder.choices(s)][0] == "/sdcard/Games"


def test_a_folder_set_by_you_is_used_and_remembered_for_that_device(s, world, tmp_path):
    p = gamesfolder.set_folder(s, SD)
    assert p.rom_root == SD and p.card_root == "/sdcard" and p.gamelists_dir == s.profile.gamelists_dir
    assert s.profile == p and gamesfolder.source(s) == "set"
    s.refresh()
    assert {r.system for r in s.rows() if r.on_device} == {"gba"}          # what is listed follows the new folder
    again = session_for(world, tmp_path / "later")                         # a restart: a new session, same database
    assert again.profile.rom_root == SD and gamesfolder.source(again) == "set"


def test_the_override_belongs_to_one_device(s, world, tmp_path):
    gamesfolder.set_folder(s, SD)
    world.dev.serial = "OTHER-HANDHELD"
    other = session_for(world, tmp_path / "other")
    assert other.profile.rom_root == CARD and gamesfolder.source(other) == "detected"


def test_a_path_is_tidied_before_it_is_kept(s):
    assert gamesfolder.set_folder(s, " /sdcard/ROMs/ ").rom_root == SD


@pytest.mark.parametrize("typed, why", [
    ("/sdcard/Nowhere", "does not exist"),
    ("/sdcard/ES-DE", "no system folders"),
])
def test_a_folder_that_is_not_there_or_holds_no_systems_is_refused_with_the_reason(s, typed, why):
    before = s.profile
    with pytest.raises(FolderError, match=why):
        gamesfolder.set_folder(s, typed)
    assert s.profile == before and gamesfolder.source(s) == "detected"


def test_nothing_can_change_while_a_sync_is_unfinished(s):
    job = s.store.create_job(s.serial, [{"kind": "refresh"}])
    with pytest.raises(FolderError, match="sync"):
        gamesfolder.set_folder(s, SD)
    with pytest.raises(FolderError, match="sync"):
        gamesfolder.detect_again(s)
    assert s.profile.rom_root == CARD
    s.store.close_job(job, "cancelled")
    assert gamesfolder.set_folder(s, SD).rom_root == SD


def test_detect_again_goes_back_to_automatic_detection(s):
    gamesfolder.set_folder(s, SD)
    p = gamesfolder.detect_again(s)
    assert p.rom_root == CARD and s.profile == p and gamesfolder.source(s) == "detected"
    assert s.store.meta(s.serial, "profile")["rom_root"] == CARD


def test_without_a_device_the_folder_cannot_be_checked(s):
    s.device = None
    with pytest.raises(FolderError, match="Connect"):
        gamesfolder.set_folder(s, SD)
    with pytest.raises(FolderError, match="Connect"):
        gamesfolder.detect_again(s)


def test_a_card_device_can_have_its_folder_set_too(tmp_path):
    from jewelcase.core.carddevice import CardDevice
    from jewelcase.core.db import Store
    root = write_tree(tmp_path / "SD", {"roms/psx": {"a.chd": b"1"}, "extra/gba": {"b.zip": b"2"}})
    cfg = Config(library=tmp_path / "lib", inbox=tmp_path / "in", card=root)
    (tmp_path / "lib").mkdir()
    store = Store(":memory:")
    s = Session(cfg, store, Library(cfg.library, store), tmp_path / "data", adb_enabled=False)
    assert s.connect() and s.profile.rom_root == "/roms"
    assert gamesfolder.set_folder(s, "extra").rom_root == "/extra"
    assert s.profile.card_root == "/"
    assert isinstance(s.device, CardDevice)
