import hashlib
import os
import shutil
import subprocess

import pytest

from jewelcase.core import library as libmod
from jewelcase.core import systems
from jewelcase.core.db import Store
from jewelcase.core.library import Library, pick_device_form

needs_7z = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")

TREE = {
    "ps2": {"God of War II (USA).chd": b"c" * 300, "God of War II (USA).iso": b"i" * 900,
            "God of War II (USA).7z": b"7" * 100, "Only Archive (USA).7z": b"7" * 50,
            "notes.txt": "x", "gamelist.xml": "<gameList/>"},
    "psx": {"FF7 (USA).m3u": "FF7 (USA) (Disc 1).chd\nFF7 (USA) (Disc 2).chd\n",
            "FF7 (USA) (Disc 1).chd": b"1" * 10, "FF7 (USA) (Disc 2).chd": b"2" * 20,
            "images/FF7 (USA).png": b"png"},
    "_duplicates": {"Dup.chd": b"d"},
    ".hidden": {"x.chd": b"d"},
}


@pytest.fixture
def lib(tmp_path, tree):
    tree(tmp_path / "lib", TREE)
    library = Library(tmp_path / "lib", Store(":memory:"))
    library.scan()
    return library


def test_scan_indexes_sizes_not_hashes(tmp_path, tree, monkeypatch):
    tree(tmp_path / "lib", TREE)
    monkeypatch.setattr(hashlib, "md5", lambda *a, **k: (_ for _ in ()).throw(AssertionError("hashed")))
    library = Library(tmp_path / "lib", Store(":memory:"))
    assert library.scan() is True
    files = library.store.lib_files("ps2")
    assert {f.name: f.size for f in files}["God of War II (USA).chd"] == 300
    assert all(f.md5 is None for f in files)


def test_scan_ignores_underscore_dirs_and_non_roms(lib):
    assert lib.systems() == ["ps2", "psx"]
    assert "notes.txt" not in [f.name for f in lib.store.lib_files("ps2")]
    assert "gamelist.xml" not in [f.name for f in lib.store.lib_files("ps2")]
    assert all("images" not in f.name for f in lib.store.lib_files("psx"))


def test_scan_drops_systems_whose_folder_is_gone(lib):
    shutil.rmtree(lib.root / "psx")
    lib.scan()
    assert lib.systems() == ["ps2"]


def test_forms_group_chd_iso_7z(lib):
    forms = lib.forms("ps2")
    assert sorted(u.ext for u in forms["God of War II (USA)"]) == [".7z", ".chd", ".iso"]
    assert [u.ext for u in forms["Only Archive (USA)"]] == [".7z"]
    psx = lib.forms("psx")
    assert list(psx) == ["FF7 (USA)"] and len(psx["FF7 (USA)"][0].files) == 3


def test_unit_size(lib):
    unit = lib.forms("psx")["FF7 (USA)"][0]
    assert lib.unit_size("psx", unit) == len("FF7 (USA) (Disc 1).chd\nFF7 (USA) (Disc 2).chd\n") + 30


def test_pick_prefers_device_ext(lib):
    forms = lib.forms("ps2")["God of War II (USA)"]
    assert pick_device_form(systems.get("ps2"), forms, {".iso"}).ext == ".iso"


def test_pick_uses_table_order(lib):
    forms = lib.forms("ps2")["God of War II (USA)"]
    assert pick_device_form(systems.get("ps2"), forms, set()).ext == ".chd"


def test_archive_only_has_no_device_form(lib):
    assert pick_device_form(systems.get("ps2"), lib.forms("ps2")["Only Archive (USA)"], set()) is None
    assert pick_device_form(systems.get("ps2"), lib.forms("ps2")["Only Archive (USA)"], {".7z"}) is None


def test_generic_system_takes_any_non_archive(tmp_path, tree):
    tree(tmp_path / "lib", {"vectrex": {"A.vec": b"1", "B.7z": b"2"}})
    library = Library(tmp_path / "lib", Store(":memory:"))
    library.scan()
    odd = systems.get("vectrex")
    assert pick_device_form(odd, library.forms("vectrex")["A"], set()).ext == ".vec"
    assert pick_device_form(odd, library.forms("vectrex")["B"], set()) is None


def test_md5_cached_until_file_changes(lib, monkeypatch):
    name = "God of War II (USA).chd"
    want = hashlib.md5(b"c" * 300).hexdigest()
    assert lib.md5("ps2", name) == want
    calls = []
    real = libmod.md5_file
    monkeypatch.setattr(libmod, "md5_file", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert lib.md5("ps2", name) == want and calls == []
    p = lib.file_path("ps2", name)
    p.write_bytes(b"x" * 301)
    assert lib.md5("ps2", name) == hashlib.md5(b"x" * 301).hexdigest() and calls == [1]
    assert lib.size("ps2", name) == 301


def test_md5_read_error_propagates(lib, monkeypatch):
    def boom(*a, **k):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(libmod, "md5_file", boom)
    with pytest.raises(OSError):
        lib.md5("ps2", "God of War II (USA).chd")


def test_offline_library_keeps_last_index(lib, tmp_path):
    before = sorted(lib.forms("ps2"))
    lib.root = tmp_path / "unmounted"
    assert lib.online is False
    assert lib.scan() is False
    assert sorted(lib.forms("ps2")) == before
    assert lib.size("ps2", "God of War II (USA).chd") == 300
    # an empty mount point is also "offline", not "an empty library"
    (tmp_path / "unmounted").mkdir()
    assert lib.online is False and lib.scan() is False
    assert lib.systems() == ["ps2", "psx"]
    assert Library(None, Store(":memory:")).online is False


def test_offline_playlists_fall_back_to_single_files(lib, tmp_path):
    lib.root = tmp_path / "unmounted"
    lib.invalidate()
    assert "FF7 (USA)" in lib.forms("psx")


def test_import_manifest_only_ok_rows_with_matching_size(lib):
    good = "a" * 32
    rows = ["system\tfile\tbytes\tmd5_card\tmd5_venus\tsize_venus\tstate",
            f"ps2\tGod of War II (USA).chd\t300\t{good}\t{good}\t300\tok",
            f"ps2\tGod of War II (USA).iso\t900\t{good}\t{good}\t901\tok",          # size differs
            f"ps2\tOnly Archive (USA).7z\t50\t{good}\t{'b' * 32}\t50\tmd5-mismatch",
            f"ps2\tGhost.chd\t5\t{good}\t{good}\t5\tok",                            # not in library
            f"psx\tFF7 (USA) (Disc 1).chd\t10\t\t{good}\t10\tpull-fail"]
    (lib.root / "card-pull-manifest.tsv").write_text("\n".join(rows) + "\n")
    assert lib.import_manifest() == 1
    md5s = {f.name: f.md5 for f in lib.store.lib_files("ps2")}
    assert md5s["God of War II (USA).chd"] == good and md5s["God of War II (USA).iso"] is None
    assert lib.manifest_device_hashes() == [("ps2", "God of War II (USA).chd", 300, good),
                                            ("ps2", "God of War II (USA).iso", 900, good),
                                            ("ps2", "Ghost.chd", 5, good)]


def test_import_manifest_absent_is_zero(lib):
    assert lib.import_manifest() == 0 and lib.manifest_device_hashes() == []


def test_free_bytes(lib):
    assert lib.free_bytes() > 0


@needs_7z
def test_archive_member_md5(tmp_path):
    inner = tmp_path / "Tom's Game & Co (USA).iso"
    inner.write_bytes(b"iso-bytes" * 1000)
    arc = tmp_path / "Tom's Game & Co (USA).7z"
    subprocess.run(["7z", "a", str(arc), inner.name], cwd=tmp_path, check=True, capture_output=True)
    assert libmod.archive_members(arc) == [(inner.name, 9000)]
    assert libmod.find_archive_member(arc, inner.name, 9000) is True
    assert libmod.find_archive_member(arc, inner.name, 9001) is False
    assert libmod.archive_member_md5(arc, inner.name) == hashlib.md5(b"iso-bytes" * 1000).hexdigest()
    assert libmod.archive_ok(arc) is True
    arc.write_bytes(arc.read_bytes()[:200])
    assert libmod.archive_ok(arc) is False


def test_tool_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(libmod.ToolMissing):
        libmod.archive_members(tmp_path / "x.7z")
