"""Converting an archive right after it is imported from the downloads folder into the library."""
import shutil

import pytest

from jewelcase.core import convert
from jewelcase.core.db import Store
from jewelcase.core.inbox import (ImportProgress, InboxError, InboxItem, import_item, import_items, undo_import)
from jewelcase.core.library import Library
from test_convert import ISO, chdman, no_chdman, pack  # noqa: F401  (the stub chdman and the archive helper)

pytestmark = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")

SHADOW = "Shadow (Europe)"


@pytest.fixture
def box(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


@pytest.fixture
def lib(tmp_path):
    for s in ("ps2", "ps3", "psx", "psp"):
        (tmp_path / "lib" / s).mkdir(parents=True)
    library = Library(tmp_path / "lib", Store(":memory:"))
    library.scan()
    return library


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


def item(box, name, system="ps2"):
    path = box / name
    return InboxItem(path, (path,), path.stat().st_size, system, "inside the archive", "ready")


def test_an_imported_archive_is_converted_right_after_it_lands(box, lib, cache, chdman):
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, "SER1", convert_cache=cache)
    folder = lib.root / "ps2"
    assert sorted(p.name for p in folder.iterdir()) == [SHADOW + ".7z", SHADOW + ".chd"]
    assert (folder / (SHADOW + ".chd")).read_bytes() == b"MComprHDcreatedvd" + ISO
    assert lib.has("ps2", SHADOW + ".chd") and not (box / (SHADOW + ".7z")).exists()
    assert [name for name, _, _ in record.made] == [SHADOW + ".chd"] and record.convert_problem == ""
    assert lib.store.wanted("SER1").get(("ps2", SHADOW)) is True        # playable now, so it is queued like any import
    assert chdman() == ["createdvd", "verify"]


def test_without_the_setting_the_archive_is_only_imported(box, lib, chdman):
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, "SER1")
    assert [p.name for p in (lib.root / "ps2").iterdir()] == [SHADOW + ".7z"] and record.made == ()
    assert lib.store.wanted("SER1").get(("ps2", SHADOW)) is None


def test_an_unpacked_game_is_made_where_the_system_keeps_games_unpacked(box, lib, cache, chdman):
    pack(box, "Lumines (USA).7z", {"Lumines (USA).dec.iso": ISO, "readme.txt": b"x"})
    record = import_item(item(box, "Lumines (USA).7z", "psp"), "psp", lib, lib.store, None, convert_cache=cache)
    assert (lib.root / "psp" / "Lumines (USA).iso").read_bytes() == ISO and len(record.made) == 1
    assert chdman() == []


def test_a_conversion_that_fails_does_not_fail_the_import(box, lib, cache, chdman):
    chdman.install(fail_create=True)
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, "SER1", convert_cache=cache)
    assert record.convert_problem == "chdman could not make the CHD: Error: could not read the input"
    assert [p.name for p in (lib.root / "ps2").iterdir()] == [SHADOW + ".7z"]      # the archive is imported, nothing else
    assert record.made == () and not (box / (SHADOW + ".7z")).exists()
    assert lib.store.wanted("SER1").get(("ps2", SHADOW)) is None                    # nothing playable to queue
    assert list(cache.rglob("*.iso")) == []


def test_an_archive_that_cannot_be_converted_says_why_and_is_still_imported(box, lib, cache, chdman):
    pack(box, "Odd (USA).7z", {"notes.txt": b"x"})
    record = import_item(item(box, "Odd (USA).7z"), "ps2", lib, lib.store, None, convert_cache=cache)
    assert record.convert_problem == "The archive holds no game file." and (lib.root / "ps2" / "Odd (USA).7z").exists()


def test_a_game_the_library_already_plays_is_left_as_it_is(box, lib, cache, chdman):
    (lib.root / "ps2" / (SHADOW + ".chd")).write_bytes(b"already")
    lib.scan()
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, None, convert_cache=cache)
    assert record.made == () and record.convert_problem == ""
    assert (lib.root / "ps2" / (SHADOW + ".chd")).read_bytes() == b"already"


def test_progress_names_the_phases_and_only_moves_forward(box, lib, cache, chdman):
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    seen: list[ImportProgress] = []
    records, problems = import_items([(item(box, SHADOW + ".7z"), "ps2")], lib, lib.store, None, seen.append,
                                     convert_cache=cache)
    assert problems == [] and len(records[0].made) == 1
    phases = list(dict.fromkeys(p.phase for p in seen))
    assert phases == ["checking", "copying", "verifying", "unpacking", "compressing", "verifying_game"]
    assert [p.done for p in seen] == sorted(p.done for p in seen)
    assert seen[-1].done == seen[-1].total and all(p.done <= p.total for p in seen)
    assert {p.name for p in seen if p.converting} == {SHADOW + ".7z"}


def test_progress_does_not_move_back_when_a_conversion_fails(box, lib, cache, chdman):
    chdman.install(fail_create=True)
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    pack(box, "Lumines (USA).7z", {"Lumines (USA).iso": ISO})
    seen: list[ImportProgress] = []
    pairs = [(item(box, SHADOW + ".7z"), "ps2"), (item(box, "Lumines (USA).7z", "psp"), "psp")]
    import_items(pairs, lib, lib.store, None, seen.append, convert_cache=cache)
    assert [p.done for p in seen] == sorted(p.done for p in seen) and seen[-1].done == seen[-1].total


# ---------------------------------------------------------------- undoing
def test_undoing_an_import_takes_the_converted_game_out_again(box, lib, cache, chdman):
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    original = (box / (SHADOW + ".7z")).read_bytes()
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, "SER1", convert_cache=cache)
    assert undo_import(record, lib, lib.store, "SER1") == []
    assert (box / (SHADOW + ".7z")).read_bytes() == original
    assert list((lib.root / "ps2").iterdir()) == [] and lib.names("ps2") == []
    assert lib.store.wanted("SER1").get(("ps2", SHADOW)) is None


def test_undoing_leaves_a_converted_file_that_changed_since(box, lib, cache, chdman):
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, None, convert_cache=cache)
    made = lib.root / "ps2" / (SHADOW + ".chd")
    made.write_bytes(made.read_bytes() + b"edited")
    left = undo_import(record, lib, lib.store, None)
    assert left == [SHADOW + ".chd"] and made.exists() and lib.has("ps2", made.name)
    assert (box / (SHADOW + ".7z")).exists()


def test_undoing_never_removes_a_file_that_was_there_before(box, lib, cache, chdman):
    # an unrelated file with the converted game's name appears after the import: it is not the one conversion made
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, None, convert_cache=cache)
    made = lib.root / "ps2" / (SHADOW + ".chd")
    made.unlink()
    made.write_bytes(b"somebody else's")
    assert undo_import(record, lib, lib.store, None) == [SHADOW + ".chd"]
    assert made.read_bytes() == b"somebody else's"


def test_an_undo_that_is_refused_removes_nothing_that_conversion_made(box, lib, cache, chdman):
    pack(box, SHADOW + ".7z", {SHADOW + ".iso": ISO})
    record = import_item(item(box, SHADOW + ".7z"), "ps2", lib, lib.store, None, convert_cache=cache)
    (box / (SHADOW + ".7z")).write_bytes(b"the same name again")
    with pytest.raises(InboxError, match="already in the downloads folder again"):
        undo_import(record, lib, lib.store, None)
    assert (lib.root / "ps2" / (SHADOW + ".chd")).exists()


# ---------------------------------------------------------------- the setting
def test_converting_on_import_is_on_by_default_and_remembered(tmp_path):
    from jewelcase.core import config
    assert config.load(tmp_path / "none.toml").convert_on_import is True
    cfg = config.Config()
    cfg.convert_on_import = False
    config.save(cfg, tmp_path / "config.toml")
    assert config.load(tmp_path / "config.toml").convert_on_import is False
