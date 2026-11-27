import hashlib
import shutil
import struct
import subprocess
import time
import zipfile

import pytest

from jewelcase.core import inbox as inboxmod
from jewelcase.core.db import Store
from jewelcase.core.inbox import Inbox, InboxError, detect_system, import_item
from jewelcase.core.library import Library

needs_7z = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")
OLD = time.time() - 3600


def iso(system_id: bytes = b"", extra: bytes = b"", at0: bytes = b"") -> bytes:
    """A 2 MiB disc image with an ISO9660 volume descriptor naming `system_id`."""
    data = bytearray(2 * 1024 * 1024)
    data[0:len(at0)] = at0
    data[0x8000:0x8006] = b"\x01CD001"
    data[0x8008:0x8008 + len(system_id)] = system_id
    data[0x10000:0x10000 + len(extra)] = extra
    return bytes(data)


def aged(p, age=3600):
    import os
    os.utime(p, (time.time() - age, time.time() - age))
    return p


def put(folder, name, body=b"x" * 100, age=3600):
    p = folder / name
    p.write_bytes(body)
    return aged(p, age)


@pytest.fixture
def box(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


@pytest.fixture
def lib(tmp_path):
    for s in ("gba", "ps2", "psp", "gamecube", "psx"):
        (tmp_path / "lib" / s).mkdir(parents=True)
    library = Library(tmp_path / "lib", Store(":memory:"))
    library.scan()
    return library


def test_detect_by_extension(box):
    for name, system in [("a.gba", "gba"), ("a.sfc", "snes"), ("a.z64", "n64"), ("a.nds", "nds"),
                         ("a.wbfs", "wii"), ("a.cso", "psp"), ("a.NES", "nes"), ("a.gdi", "dreamcast")]:
        assert detect_system(put(box, name)) == (system, "file type"), name


def test_detect_psp_iso(box):
    assert detect_system(put(box, "g.iso", iso(b"PSP GAME"))) == ("psp", "disc header")


def test_detect_ps2_iso(box):
    body = iso(b"PLAYSTATION", b"BOOT2 = cdrom0:\\SLUS_123.45;1\r\n")
    assert detect_system(put(box, "g.iso", body)) == ("ps2", "disc header")


def test_detect_ps1_iso(box):
    body = iso(b"PLAYSTATION", b"BOOT = cdrom:\\SLUS_005.94;1\r\n")
    assert detect_system(put(box, "g.iso", body)) == ("psx", "disc header")


def test_detect_gamecube_and_wii_iso(box):
    gc = bytearray(2048)
    gc[0x1C:0x20] = bytes.fromhex("C2339F3D")
    wii = bytearray(2048)
    wii[0x18:0x1C] = bytes.fromhex("5D1C9EA3")
    assert detect_system(put(box, "gc.iso", bytes(gc))) == ("gamecube", "disc header")
    assert detect_system(put(box, "wii.iso", bytes(wii))) == ("wii", "disc header")


def test_detect_rvz_by_disc_type(box):
    def rvz(disc_type):
        data = bytearray(0x60)
        data[0:4] = b"RVZ\x01"
        data[0x48:0x4C] = struct.pack(">I", disc_type)
        return bytes(data)
    assert detect_system(put(box, "a.rvz", rvz(1))) == ("gamecube", "disc header")
    assert detect_system(put(box, "b.rvz", rvz(2))) == ("wii", "disc header")
    assert detect_system(put(box, "c.rvz", b"junk" * 40))[0] is None


def test_detect_zip_with_single_rom(box):
    z = box / "Minish Cap (USA).zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Minish Cap (USA).gba", b"rom")
        f.writestr("readme.txt", "hi")
    assert detect_system(z) == ("gba", "inside the archive")


def test_detect_zip_with_iso_reads_header(box):
    z = box / "Game (USA).zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("Game (USA).iso", iso(b"PSP GAME"))
    assert detect_system(z) == ("psp", "inside the archive")


@needs_7z
def test_detect_7z_with_ps2_iso(box):
    (box / "Game (USA).iso").write_bytes(iso(b"PLAYSTATION", b"BOOT2 = cdrom0:\\SLUS_1.00;1"))
    subprocess.run(["7z", "a", "Game (USA).7z", "Game (USA).iso"], cwd=box, check=True, capture_output=True)
    (box / "Game (USA).iso").unlink()
    assert detect_system(box / "Game (USA).7z") == ("ps2", "inside the archive")


def test_ambiguous_is_none(box):
    assert detect_system(put(box, "Game (USA).chd")) == (None, "ambiguous")
    assert detect_system(put(box, "plain.iso", b"\0" * 4096)) == (None, "ambiguous")
    z = box / "mixed.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("a.gba", b"1")
        f.writestr("b.sfc", b"2")
    assert detect_system(z) == (None, "ambiguous")
    arcade = box / "sf2.zip"
    with zipfile.ZipFile(arcade, "w") as f:
        f.writestr("sf2.01", b"1")
        f.writestr("sf2.02", b"2")
    assert detect_system(arcade) == (None, "ambiguous")


def test_scan_lists_only_games(box):
    put(box, "Violet (USA).gba")
    put(box, "holiday.jpg")
    put(box, "report.pdf")
    put(box, "Disc (USA).chd")
    docs = box / "documents.zip"
    with zipfile.ZipFile(docs, "w") as f:
        f.writestr("taxes.pdf", b"1")
    aged(docs)
    (box / "subfolder").mkdir()
    put(box / "subfolder", "Hidden (USA).gba")
    items = {i.path.name: i for i in Inbox(box).scan()}
    assert set(items) == {"Violet (USA).gba", "Disc (USA).chd"}
    assert (items["Violet (USA).gba"].system, items["Violet (USA).gba"].state) == ("gba", "ready")
    assert (items["Disc (USA).chd"].system, items["Disc (USA).chd"].state) == (None, "pick_system")
    assert items["Violet (USA).gba"].size == 100


def test_scan_groups_cue_with_its_tracks(box):
    put(box, "Game (USA).cue", b'FILE "Game (USA) (Track 1).bin" BINARY\n')
    put(box, "Game (USA) (Track 1).bin", b"t" * 500)
    put(box, "stray.bin")
    items = Inbox(box).scan()
    assert [i.path.name for i in items] == ["Game (USA).cue"]
    assert {p.name for p in items[0].files} == {"Game (USA).cue", "Game (USA) (Track 1).bin"}


def test_downloading_state(box):
    put(box, "Fresh (USA).gba", age=1)
    put(box, "Chrome (USA).7z.crdownload")
    put(box, "Firefox (USA).gba", b"")
    put(box, "Firefox (USA).gba.part")
    items = {i.path.name: i.state for i in Inbox(box).scan()}
    assert items == {"Fresh (USA).gba": "downloading", "Chrome (USA).7z.crdownload": "downloading",
                     "Firefox (USA).gba": "downloading"}


def test_scan_missing_folder_is_empty(tmp_path):
    assert Inbox(tmp_path / "nope").scan() == []


def test_import_moves_and_ticks(box, lib):
    src = put(box, "Tom's Game & Co (USA).gba", b"rom-bytes" * 50)
    item = Inbox(box).scan()[0]
    import_item(item, "gba", lib, lib.store, "SER1")
    dest = lib.root / "gba" / "Tom's Game & Co (USA).gba"
    assert dest.read_bytes() == b"rom-bytes" * 50 and not src.exists()
    assert not list((lib.root / "gba").glob("*.part"))
    assert lib.cached_md5("gba", dest.name) == hashlib.md5(b"rom-bytes" * 50).hexdigest()
    assert lib.store.wanted("SER1") == {("gba", "Tom's Game & Co (USA)"): True}


def test_import_archive_that_needs_conversion_is_not_ticked(box, lib):
    z = box / "Game (USA).zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Game (USA).iso", iso(b"PLAYSTATION", b"BOOT2 = x"))
    item = Inbox(box).scan(now=time.time() + 60)[0]
    assert item.system == "ps2"
    import_item(item, "ps2", lib, lib.store, "SER1")
    assert (lib.root / "ps2" / "Game (USA).zip").exists()
    assert lib.store.wanted("SER1") == {}


def test_import_without_device_does_not_tick(box, lib):
    put(box, "A (USA).gba")
    import_item(Inbox(box).scan()[0], "gba", lib, lib.store, None)
    assert (lib.root / "gba" / "A (USA).gba").exists()


def test_import_refuses_when_library_full(box, lib, monkeypatch):
    src = put(box, "A (USA).gba", b"x" * 5000)
    monkeypatch.setattr(lib, "free_bytes", lambda: 1000)
    with pytest.raises(InboxError, match="full"):
        import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    assert src.exists() and not (lib.root / "gba" / "A (USA).gba").exists()


def test_import_refuses_when_library_offline(box, lib, tmp_path):
    src = put(box, "A (USA).gba")
    item = Inbox(box).scan()[0]
    lib.root = tmp_path / "unmounted"
    with pytest.raises(InboxError, match="not available"):
        import_item(item, "gba", lib, lib.store, "SER1")
    assert src.exists()


def test_import_bad_archive(box, lib):
    z = box / "Broken (USA).zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("Broken (USA).gba", b"rom" * 5000)
    item = Inbox(box).scan(now=time.time() + 60)[0]
    raw = bytearray(z.read_bytes())
    raw[60:80] = b"\xff" * 20                        # damage the compressed data, keep the listing
    z.write_bytes(bytes(raw))
    with pytest.raises(InboxError, match="damaged"):
        import_item(item, "gba", lib, lib.store, "SER1")
    assert z.exists() and not (lib.root / "gba" / "Broken (USA).zip").exists()


def test_import_same_file_already_in_library(box, lib):
    (lib.root / "gba" / "A (USA).gba").write_bytes(b"same")
    lib.scan()
    src = put(box, "A (USA).gba", b"same")
    import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    assert not src.exists()
    other = put(box, "A (USA).gba", b"different")
    with pytest.raises(InboxError, match="different file"):
        import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    assert other.exists() and (lib.root / "gba" / "A (USA).gba").read_bytes() == b"same"


def test_scan_reuses_detection_until_file_changes(box, monkeypatch):
    put(box, "A (USA).gba")
    inbox = Inbox(box)
    inbox.scan()
    calls = []
    real = inboxmod.detect_system
    monkeypatch.setattr(inboxmod, "detect_system", lambda p: calls.append(p) or real(p))
    inbox.scan()
    assert calls == []


def test_fresh_archive_of_documents_is_not_listed(box):
    with zipfile.ZipFile(box / "documents.zip", "w") as f:
        f.writestr("taxes.pdf", b"1")
    assert Inbox(box).scan() == []


def test_import_refuses_a_file_still_downloading(box, lib):
    put(box, "Fresh (USA).gba", age=1)
    item = Inbox(box).scan()[0]
    with pytest.raises(InboxError, match="still downloading"):
        import_item(item, "gba", lib, lib.store, "SER1")


# ---------------------------------------------------------------- undoing an import
def test_undo_import_moves_the_file_back_and_clears_the_tick(box, lib):
    from jewelcase.core.inbox import undo_import
    src = put(box, "Tom's Game & Co (USA).gba", b"rom-bytes" * 50)
    record = import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    assert record.ticked and not src.exists()
    undo_import(record, lib, lib.store, "SER1")
    assert src.read_bytes() == b"rom-bytes" * 50
    assert not (lib.root / "gba" / src.name).exists() and not lib.has("gba", src.name)
    assert lib.store.wanted("SER1") == {}


def test_undo_import_refused_after_the_file_changed(box, lib):
    from jewelcase.core.inbox import undo_import
    src = put(box, "A (USA).gba", b"original")
    record = import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    (lib.root / "gba" / "A (USA).gba").write_bytes(b"patched!")          # same size, other bytes
    with pytest.raises(InboxError, match="changed since"):
        undo_import(record, lib, lib.store, "SER1")
    assert (lib.root / "gba" / "A (USA).gba").read_bytes() == b"patched!" and not src.exists()


def test_undo_import_refused_when_the_download_folder_has_that_name_again(box, lib):
    from jewelcase.core.inbox import undo_import
    put(box, "A (USA).gba", b"original")
    record = import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    put(box, "A (USA).gba", b"a newer download")
    with pytest.raises(InboxError, match="already"):
        undo_import(record, lib, lib.store, "SER1")
    assert (box / "A (USA).gba").read_bytes() == b"a newer download"
    assert (lib.root / "gba" / "A (USA).gba").exists()


def test_undo_import_of_a_duplicate_restores_the_download_only(box, lib):
    from jewelcase.core.inbox import undo_import
    (lib.root / "gba" / "A (USA).gba").write_bytes(b"same")
    lib.scan()
    src = put(box, "A (USA).gba", b"same")
    record = import_item(Inbox(box).scan()[0], "gba", lib, lib.store, "SER1")
    undo_import(record, lib, lib.store, "SER1")
    assert src.read_bytes() == b"same" and (lib.root / "gba" / "A (USA).gba").exists()


# ------------------------------------------------------------ progress
def _watch(pairs, lib, serial="SER1"):
    from jewelcase.core.inbox import import_items
    seen = []
    records, problems = import_items(pairs, lib, lib.store, serial, seen.append)
    return seen, records, problems


def test_progress_copies_then_verifies_each_file(box, lib):
    body = b"r" * (3 * 1024 * 1024 + 17)
    put(box, "Big (USA).gba", body)
    seen, records, problems = _watch([(Inbox(box).scan()[0], "gba")], lib)
    assert len(records) == 1 and not problems
    phases = [p.phase for p in seen]
    assert phases[0] == "copying" and phases[-1] == "verifying"
    assert phases == sorted(phases)                                   # all the copying, then all the verifying
    assert {(p.name, p.file, p.files, p.total) for p in seen} == {("Big (USA).gba", 1, 1, 2 * len(body))}
    done = [p.done for p in seen]
    assert done == sorted(done) and done[-1] == 2 * len(body)
    assert max(p.done for p in seen if p.phase == "copying") == len(body)
    assert seen[-1].fraction == 1.0


def test_progress_runs_across_a_batch(box, lib):
    a, b = b"a" * 4000, b"b" * 6000
    put(box, "A (USA).gba", a)
    put(box, "B (USA).gba", b)
    items = sorted(Inbox(box).scan(), key=lambda i: i.path.name)
    seen, records, _ = _watch([(i, "gba") for i in items], lib)
    assert len(records) == 2
    assert {p.total for p in seen} == {2 * (len(a) + len(b))} and {p.files for p in seen} == {2}
    second = [p for p in seen if p.name == "B (USA).gba"]
    assert {p.file for p in second} == {2} and min(p.done for p in second) >= 2 * len(a)
    done = [p.done for p in seen]
    assert done == sorted(done) and done[-1] == seen[-1].total


def test_progress_says_when_an_archive_is_being_checked(box, lib):
    z = box / "Game (USA).zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Game (USA).iso", iso(b"PLAYSTATION", b"BOOT2 = x"))
    item = Inbox(box).scan(now=time.time() + 60)[0]
    seen, _, _ = _watch([(item, "ps2")], lib)
    assert (seen[0].phase, seen[0].name, seen[0].done) == ("checking", "Game (USA).zip", 0)
    assert seen[0].fraction is None                                   # that check has no byte count
    assert [p.phase for p in seen if p.phase != "checking"][0] == "copying"


def test_progress_moves_past_an_item_that_fails(box, lib):
    (lib.root / "gba" / "A (USA).gba").write_bytes(b"something else entirely")
    put(box, "A (USA).gba", b"a" * 4000)
    put(box, "B (USA).gba", b"b" * 6000)
    items = sorted(Inbox(box).scan(), key=lambda i: i.path.name)
    seen, records, problems = _watch([(i, "gba") for i in items], lib)
    assert [r.stem for r in records] == ["B (USA)"]
    assert len(problems) == 1 and problems[0].startswith("A (USA).gba: ")
    assert seen[-1].done == seen[-1].total == 2 * 10000


def test_progress_for_a_file_the_library_already_has(box, lib):
    (lib.root / "gba" / "A (USA).gba").write_bytes(b"same" * 1000)
    put(box, "A (USA).gba", b"same" * 1000)
    seen, records, _ = _watch([(Inbox(box).scan()[0], "gba")], lib)
    assert len(records) == 1 and {p.phase for p in seen} == {"verifying"}
    assert seen[-1].done == seen[-1].total == 8000
