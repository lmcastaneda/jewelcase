import shutil
import subprocess

import pytest

from jewelcase.core import plan as planmod
from jewelcase.core.device import DeviceInfo, Storage
from jewelcase.core.plan import build_plan, prove_twin, with_copy_first

from conftest import CARD

GOW = "God of War II (USA).chd"
needs_7z = pytest.mark.skipif(not shutil.which("7z"), reason="7z not installed")


def twin(world, system, name):
    return prove_twin(world.dev, world.library, world.store, world.profile, system, name)


def plan_for(world, **kw):
    info = kw.pop("info", DeviceInfo(world.serial, "m", "14", 80, "ac", "usb"))
    storage = kw.pop("storage", Storage(10 ** 9, 0, 10 ** 9))
    return build_plan(world.rows(), storage, world.library, info, **kw)


def test_twin_by_hash_caches_both_sides(world):
    r = twin(world, "ps2", GOW)
    assert (r.ok, r.how) == (True, "hash")
    mtime = world.dev.stat(f"{CARD}/ps2/{GOW}").mtime
    assert world.store.dev_hash(world.serial, "ps2", GOW, 4000, mtime) == world.library.cached_md5("ps2", GOW)


def test_twin_by_record(world):
    twin(world, "ps2", GOW)
    world.dev.calls.clear()
    r = twin(world, "ps2", GOW)
    assert (r.ok, r.how) == (True, "record")
    assert not any(what == "md5" for what, _ in world.dev.calls)     # no rehash on the device


def test_record_needs_library_file_still_there(world):
    twin(world, "ps2", GOW)
    (world.lib_root / "ps2" / GOW).unlink()
    world.library.scan()
    r = twin(world, "ps2", GOW)
    assert not r.ok and r.reason == "no library copy"


def test_same_name_different_size_is_not_twin(world):
    (world.lib_root / "ps2" / GOW).write_bytes(b"gow2" * 999)
    world.library.scan()
    r = twin(world, "ps2", GOW)
    assert (r.ok, r.reason) == (False, "size differs")


def test_same_name_different_md5_is_not_twin(world):
    (world.lib_root / "ps2" / GOW).write_bytes(b"XXXX" * 1000)
    world.library.scan()
    r = twin(world, "ps2", GOW)
    assert (r.ok, r.reason) == (False, "contents differ")


def test_no_library_copy(world):
    r = twin(world, "ps2", "Device Only (USA).chd")
    assert (r.ok, r.reason) == (False, "no library copy")


def test_twin_library_offline(world, tmp_path):
    world.library.root = tmp_path / "unmounted"
    assert twin(world, "ps2", GOW).reason == "library offline"


def test_twin_library_read_error(world, monkeypatch):
    def boom(*a, **k):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(world.library, "md5", boom)
    r = twin(world, "ps2", GOW)
    assert not r.ok and "read error" in r.reason


@needs_7z
def test_twin_in_archive(world):
    lib_ps2 = world.lib_root / "ps2"
    subprocess.run(["7z", "a", "God of War II (USA).7z", GOW], cwd=lib_ps2, check=True, capture_output=True)
    (lib_ps2 / GOW).unlink()
    world.library.scan()
    r = twin(world, "ps2", GOW)
    assert (r.ok, r.how) == (True, "archive")


def test_plan_adds_and_removes(world):
    world.store.set_wanted(world.serial, "ps2", "God of War II (USA)", False)
    world.store.set_wanted(world.serial, "ps2", "Champions (USA)", True)
    world.store.set_wanted(world.serial, "psx", "MTV (USA)", False)
    p = plan_for(world)
    assert [(i.row.stem, i.files, i.bytes) for i in p.adds] == [("Champions (USA)", ("Champions (USA).chd",), 1600)]
    removes = {i.row.stem: i for i in p.removes}
    assert set(removes) == {"God of War II (USA)", "MTV (USA)"}
    assert removes["God of War II (USA)"].bytes == 4000
    assert set(removes["MTV (USA)"].files) == {"MTV (USA).cue", "MTV (USA) (Track 1).bin", "MTV (USA) (Track 2).bin"}
    mtv = sum(world.dev_path("psx", n).stat().st_size for n in removes["MTV (USA)"].files)
    assert p.free_before == 10 ** 9
    assert p.free_after == 10 ** 9 + 4000 + mtv - 1600
    assert p.fits and p.skipped == [] and not p.empty
    assert plan_for(World_untouched(world)).empty


def World_untouched(world):
    world.store.clear_wanted(world.serial)
    return world


def test_plan_excludes_shared_files(world):
    # a second cue on the device that uses the same track file as MTV
    world.dev_path("psx", "Remix (USA).cue").write_text('FILE "MTV (USA) (Track 2).bin" BINARY\n')
    world.refresh()
    world.store.set_wanted(world.serial, "psx", "MTV (USA)", False)
    p = plan_for(world)
    files = p.removes[0].files
    assert "MTV (USA) (Track 2).bin" not in files and "MTV (USA) (Track 1).bin" in files


def test_plan_skips_a_game_in_a_form_nothing_can_be_made_from(world):
    (world.lib_root / "ps2" / "Odd Format (USA).nrg").write_bytes(b"nrg")
    world.refresh()
    world.store.set_wanted(world.serial, "ps2", "Odd Format (USA)", True)
    p = plan_for(world)
    assert p.adds == [] and [(r.stem, why) for r, why in p.skipped] == [("Odd Format (USA)", "needs conversion")]


def test_plan_device_only_needs_a_copy_first(world):
    world.store.set_wanted(world.serial, "ps2", "Device Only (USA)", False)
    p = plan_for(world)
    assert p.removes == [] and [(r.stem, why) for r, why in p.skipped] == [("Device Only (USA)", "no library copy")]
    p2 = with_copy_first(p, {("ps2", "Device Only (USA)")})
    assert [i.row.stem for i in p2.pulls] == ["Device Only (USA)"]
    assert [i.row.stem for i in p2.removes] == ["Device Only (USA)"]
    assert p2.skipped == [] and p2.free_after == p.free_after + 500


def test_plan_blocks_removals_when_library_offline(world, tmp_path):
    world.store.set_wanted(world.serial, "ps2", "God of War II (USA)", False)
    world.store.set_wanted(world.serial, "ps2", "Champions (USA)", True)
    world.library.root = tmp_path / "unmounted"
    p = plan_for(world)
    assert p.removes == [] and p.adds == []
    assert sorted(why for _, why in p.skipped) == ["library offline", "library offline"]


def test_plan_does_not_fit(world):
    world.store.set_wanted(world.serial, "ps2", "Champions (USA)", True)
    p = plan_for(world, storage=Storage(100_000, 99_000, 1000))
    assert p.fits is False and p.free_after < 0
    roomy = plan_for(world, storage=Storage(100_000, 50_000, 50_000))
    assert roomy.fits is True


def test_plan_usb_power_warning(world):
    world.store.set_wanted(world.serial, "ps2", "Champions (USA)", True)
    usb = DeviceInfo(world.serial, "m", "14", 80, "usb", "usb")
    slow = plan_for(world, info=usb, rate=0.5)             # 1600 bytes at 0.5 B/s: 53 minutes
    assert slow.seconds > 1800 and any("wall adapter" in w for w in slow.warnings)
    assert plan_for(world, info=usb).warnings == []
    on_ac = plan_for(world, rate=0.5)
    assert on_ac.warnings == []


def test_plan_without_device_is_empty(world):
    world.store.set_wanted(world.serial, "ps2", "Champions (USA)", True)
    p = build_plan(world.rows(), None, world.library, None)
    assert p.fits is False and p.free_before == 0
