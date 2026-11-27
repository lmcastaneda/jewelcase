"""A card or folder as the device: choosing it, reading it, and syncing onto it."""
import hashlib

import pytest

from jewelcase.core import catalog
from jewelcase.core.carddevice import CardDevice
from jewelcase.core.config import Config
from jewelcase.core.db import Store
from jewelcase.core.library import Library
from jewelcase.core.session import Session

from conftest import write_tree

GOW, CH = "God of War II (USA)", "Champions (USA)"


def make_session(tmp_path, card, **kw):
    lib = write_tree(tmp_path / "library", {
        "ps2": {GOW + ".chd": b"gow2" * 1000, CH + ".chd": b"ch" * 800},
        "psx": {"FF7 (USA).m3u": "FF7 (USA) (Disc 1).chd\n", "FF7 (USA) (Disc 1).chd": b"d1" * 500}})
    cfg = Config(library=lib, inbox=tmp_path / "inbox", card=card)
    store = Store(":memory:")
    return Session(cfg, store, Library(lib, store), tmp_path / "data", adb_enabled=False, **kw)


@pytest.fixture
def card(tmp_path):
    root = tmp_path / "SDCARD"
    write_tree(root, {"roms/ps2": {GOW + ".chd": b"gow2" * 1000}, "roms/gba": {"Old (USA).zip": b"zip"},
                      "DCIM": {"photo.jpg": b"not a game"}})
    return root


@pytest.fixture
def session(tmp_path, card):
    s = make_session(tmp_path, card)
    assert s.connect()
    s.refresh()
    return s


def run_sync_plan(s, plan):
    runner = s.runner()
    job = runner.start(plan)
    return job, runner.run(job)


def run_sync(s, tmp_path):
    return run_sync_plan(s, s.plan())


def test_a_configured_card_is_the_device_and_its_games_folder_is_found(session, card):
    assert isinstance(session.device, CardDevice) and session.is_card
    assert session.profile.rom_root == "/roms" and session.profile.gamelists_dir is None
    info = session.info()
    assert info.transport == "card" and info.battery == -1 and info.model == "SDCARD"
    rows = {r.key: r for r in session.rows()}
    assert rows[("ps2", GOW)].on_device and rows[("gba", "Old (USA)")].state == "device_only"
    assert session.storage().total > 0


def test_a_card_without_es_de_has_games_but_no_play_history(session):
    rows = session.rows()
    assert all(r.plays == 0 and r.seconds == 0 and r.last_played is None for r in rows)


def test_a_sync_puts_a_ticked_game_on_the_card_with_a_verified_copy(session, tmp_path, card):
    session.store.set_wanted(session.serial, "ps2", CH, True)
    plan = session.plan()
    assert [i.row.stem for i in plan.adds] == [CH] and plan.fits
    job, state = run_sync(session, tmp_path)
    assert state == "done"
    got = card / "roms" / "ps2" / (CH + ".chd")
    assert got.read_bytes() == b"ch" * 800
    assert hashlib.md5(got.read_bytes()).hexdigest() == session.library.md5("ps2", CH + ".chd")
    assert not list(card.rglob("*.part")) and not list(card.rglob("*.jewelcase-tmp"))
    steps = session.store.job_steps(job)
    assert all(s.state == "done" for s in steps)
    refresh = next(s for s in steps if s.kind == "refresh")
    assert "ES-DE" not in refresh.message                   # nothing to restart on a card
    session.refresh()
    assert next(r for r in session.rows() if r.key == ("ps2", CH)).on_device
    assert [r.key for r in session.plan().adds] == []


def test_a_sync_never_overwrites_a_different_file_on_the_card(session, tmp_path, card):
    session.store.set_wanted(session.serial, "ps2", CH, True)
    plan = session.plan()
    (card / "roms" / "ps2" / (CH + ".chd")).write_bytes(b"something else")     # it appears after the review
    job, state = run_sync_plan(session, plan)
    assert (card / "roms" / "ps2" / (CH + ".chd")).read_bytes() == b"something else"
    assert [s.state for s in session.store.job_steps(job) if s.kind == "push"] == ["failed"]


def test_removing_from_the_card_needs_proof_and_keeps_the_library_copy(session, tmp_path, card):
    session.store.set_wanted(session.serial, "ps2", GOW, False)
    job, state = run_sync(session, tmp_path)
    assert state == "done" and not (card / "roms" / "ps2" / (GOW + ".chd")).exists()
    assert (tmp_path / "library" / "ps2" / (GOW + ".chd")).exists()
    # a game only the card has has no verified copy, so it is not offered for removal
    session.refresh()
    session.store.set_wanted(session.serial, "gba", "Old (USA)", False)
    assert [(r.stem, why) for r, why in session.plan().skipped] == [("Old (USA)", "no library copy")]
    assert session.plan().removes == []


def test_a_sync_onto_a_card_can_be_paused_and_resumed(session, tmp_path, card):
    session.store.set_wanted(session.serial, "ps2", CH, True)
    session.store.set_wanted(session.serial, "psx", "FF7 (USA)", True)
    runner = session.runner(on_progress=lambda p: runner.stop("pause") if p.kind == "push" and p.state == "running" else None)
    job = runner.start(session.plan())
    assert runner.run(job) == "paused" and session.store.open_job(session.serial) == job
    assert session.runner().run(job, resumed=True) == "done"
    assert (card / "roms" / "psx" / "FF7 (USA) (Disc 1).chd").exists()
    assert (card / "roms" / "ps2" / (CH + ".chd")).exists()


def test_a_card_that_is_not_mounted_is_simply_not_connected(tmp_path):
    s = make_session(tmp_path, tmp_path / "missing")
    assert not s.connect() and s.device is None and s.problem == ""


def test_a_card_with_no_game_folders_says_so(tmp_path):
    root = write_tree(tmp_path / "PHOTOS", {"DCIM": {"a.jpg": b"x"}})
    s = make_session(tmp_path, root)
    assert not s.connect() and "No game folders" in s.problem and s.device is None


def test_a_card_with_an_empty_roms_folder_can_be_used_to_start_a_collection(tmp_path):
    root = tmp_path / "NEW"
    (root / "ROMs").mkdir(parents=True)
    s = make_session(tmp_path, root)
    assert s.connect() and s.profile.rom_root == "/ROMs"
    s.refresh()
    s.store.set_wanted(s.serial, "ps2", CH, True)
    job, state = run_sync(s, tmp_path)
    assert state == "done" and (root / "ROMs" / "ps2" / (CH + ".chd")).exists()


def test_an_adb_handheld_wins_over_a_configured_card(tmp_path, card, world, monkeypatch):
    s = make_session(tmp_path, card, )
    s.adb_enabled = True
    monkeypatch.setattr(Session, "_adb_candidates", lambda self: [world.dev])
    assert s.connect() and s.device is world.dev and not s.is_card
    world.dev.online = False
    monkeypatch.setattr(Session, "_adb_candidates", lambda self: [])
    assert s.connect() and s.is_card                # the handheld went away: the card is what is left


def test_the_card_setting_is_kept_in_the_config_file(tmp_path, card):
    from jewelcase.core import config
    s = make_session(tmp_path, None)
    s.config_path = tmp_path / "config" / "config.toml"
    s.set_card(card)
    assert config.load(s.config_path).card == card
    s.set_card(None)
    assert config.load(s.config_path).card is None


def test_the_same_card_is_the_same_device_next_time(tmp_path, card):
    first = make_session(tmp_path, card)
    first.connect()
    again = make_session(tmp_path / "again", card)
    again.connect()
    assert first.serial == again.serial


def test_games_folder_detection_on_a_card_looks_in_the_usual_places(tmp_path):
    root = write_tree(tmp_path / "C1", {"psx": {"a.chd": b"1"}})
    assert catalog.detect_profile(CardDevice(root)).rom_root == "/"
    root = write_tree(tmp_path / "C2", {"ROMs/psx": {"a.chd": b"1"}, "ES-DE/gamelists/psx": {"gamelist.xml": "<gameList/>"}})
    p = catalog.detect_profile(CardDevice(root))
    assert (p.rom_root, p.gamelists_dir, p.card_root) == ("/ROMs", "/ES-DE/gamelists", "/")


def test_a_freshly_formatted_empty_card_says_what_to_do(tmp_path):
    (tmp_path / "empty").mkdir()
    s = make_session(tmp_path, tmp_path / "empty")
    assert not s.connect() and s.device is None
    assert s.problem == "The card is empty. Make a folder named ROMs on it to use it."
    (tmp_path / "empty" / "ROMs").mkdir()
    assert s.connect() and s.problem == ""          # and it works once there is a ROMs folder


def test_a_games_folder_typed_outside_the_card_is_refused_in_words(session):
    from jewelcase.core import gamesfolder
    for typed in ("/../x", "/roms/../../x", "../.."):
        with pytest.raises(catalog.FolderError, match="outside the card"):
            catalog.check_games_folder(session.device, typed)
        with pytest.raises(gamesfolder.FolderError, match="outside the card"):
            gamesfolder.set_folder(session, typed)


def test_without_adb_and_without_a_card_connect_gives_up_at_once_as_before(tmp_path, monkeypatch):
    """Pins what `main` did: no adb means "adb not found" and nothing else is looked at."""
    from jewelcase.core import adb as adbmod
    monkeypatch.setattr(adbmod, "find_adb", lambda configured, data_dir=None: None)
    s = make_session(tmp_path, None)
    s.adb_enabled = True
    s.store.set_meta("old-handheld", "profile", {"rom_root": "/roms", "gamelists_dir": None, "card_root": "/"})
    assert s.connect() is False
    assert s.problem == "adb not found" and s.device is None and s.card_problem == ""
    assert s.serial is None and s.profile is None       # the remembered handheld is not picked up on this path


def test_with_a_card_configured_and_no_adb_the_card_is_still_used(tmp_path, card, monkeypatch):
    from jewelcase.core import adb as adbmod
    monkeypatch.setattr(adbmod, "find_adb", lambda configured, data_dir=None: None)
    s = make_session(tmp_path, card)
    s.adb_enabled = True
    assert s.connect() and s.is_card and s.problem == ""


def test_a_card_given_on_the_command_line_is_never_saved_as_the_setting(tmp_path, monkeypatch):
    from jewelcase.core import config
    from jewelcase.core.session import open_session
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    kept = tmp_path / "KEPT"
    kept.mkdir()
    s = open_session(library=tmp_path / "lib-for-now", card=tmp_path / "CARD-for-now")
    s.cfg.download_covers = True                # a preference toggled in this run
    s.save_config()
    saved = config.load(s.config_path)
    assert saved.card is None and saved.library is None and saved.download_covers is True
    s.set_card(kept)                            # choosing a card in the app is the setting
    s.set_library(tmp_path / "chosen-lib")
    saved = config.load(s.config_path)
    assert saved.card == kept and saved.library == tmp_path / "chosen-lib"


def test_the_saved_card_stays_when_another_one_is_given_for_one_run(tmp_path, monkeypatch):
    from jewelcase.core import config
    from jewelcase.core.session import open_session
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    mine = tmp_path / "MINE"
    mine.mkdir()
    open_session(card=mine).set_card(mine)
    s = open_session(card=tmp_path / "ONCE")
    s.cfg.download_covers = True
    s.save_config()
    assert config.load(s.config_path).card == mine


class FatCard(CardDevice):
    """A card whose filesystem ignores case: /ROMs, /roms and /Roms are one folder."""
    def _p(self, path):
        full = self.root
        for part in [x for x in path.split("/") if x]:
            same = [d for d in full.iterdir() if d.name.lower() == part.lower()] if full.is_dir() else []
            full = same[0] if same else full / part
        return full


def test_a_case_blind_card_lists_each_real_games_folder_once(tmp_path):
    from jewelcase.core import gamesfolder
    root = write_tree(tmp_path / "FAT", {"ROMs/psx": {"a.chd": b"1"}})
    dev = FatCard(root)
    assert [c.path for c in catalog.candidates(dev)] == ["/ROMs"]
    s = make_session(tmp_path, root)
    assert s.connect()
    s.device = dev
    s.profile = catalog.Profile("/roms", None, "/")          # remembered under another spelling
    assert [c.path for c in gamesfolder.choices(s)] == ["/ROMs"]
