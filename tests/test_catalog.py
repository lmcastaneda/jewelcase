from datetime import datetime, timedelta

from jewelcase.core import catalog
from jewelcase.core.config import Config
from jewelcase.core.db import Stats, Store
from jewelcase.core.fakedevice import FakeDevice
from jewelcase.core.library import Library

from conftest import CARD, GL, write_tree

NOW = datetime(2026, 11, 25, 12, 0)


def test_detect_profile_card_root(world):
    p = catalog.detect_profile(world.dev)
    assert (p.rom_root, p.gamelists_dir, p.card_root) == (CARD, GL, CARD)


def test_detect_profile_roms_subfolder(tmp_path):
    dev = FakeDevice(tmp_path / "d")
    write_tree(dev.root / "storage/AAAA-BBBB/ROMs", {"psx": {"a.chd": b"1"}, "gba": {"b.zip": b"1"}})
    write_tree(dev.root / "storage/AAAA-BBBB", {"Music": {"s.mp3": b"1"}})
    (dev.root / "storage/emulated").mkdir(parents=True)
    p = catalog.detect_profile(dev)
    assert (p.rom_root, p.gamelists_dir, p.card_root) == ("/storage/AAAA-BBBB/ROMs", None, "/storage/AAAA-BBBB")


def test_detect_profile_internal_storage(tmp_path):
    dev = FakeDevice(tmp_path / "d")
    write_tree(dev.root / "sdcard/ROMs", {"snes": {"a.zip": b"1"}})
    p = catalog.detect_profile(dev)
    assert (p.rom_root, p.card_root) == ("/sdcard/ROMs", "/sdcard")


def test_detect_profile_nothing(tmp_path):
    assert catalog.detect_profile(FakeDevice(tmp_path / "d")) is None


def test_profile_roundtrip():
    p = catalog.Profile("/a", None, "/a")
    assert catalog.Profile.from_dict(p.to_dict()) == p


def test_refresh_saves_snapshot_and_stats(world):
    snap = world.store.snapshot(world.serial)
    assert set(snap) == {"ps2", "psx", "gamecube"}            # not "Music"
    names = {f.path.rsplit("/", 1)[-1] for f in snap["ps2"]}
    assert names == {"God of War II (USA).chd", "Baldur's Gate & Co (USA).chd", "Device Only (USA).chd"}
    assert world.store.meta(world.serial, "info")["battery"] == 80
    assert world.store.meta(world.serial, "storage_card")["total"] == 1_000_000
    s = world.store.stats(world.serial)[("ps2", "God of War II (USA)")]
    assert (s.plays, s.seconds, s.last_played, s.name) == (2, 3000, datetime(2026, 11, 10, 20), "God of War II")


def test_refresh_reuses_cached_playlist_text(world):
    world.dev.calls.clear()
    world.refresh()
    read = [p for what, p in world.dev.calls if what == "read"]
    assert not any(p.endswith((".m3u", ".cue")) for p in read)


def test_stats_use_esde_alias(world):
    assert ("gamecube", "Metroid Prime (USA)") in world.store.stats(world.serial)
    assert not any(k[0] == "gc" for k in world.store.stats(world.serial))


def test_multidisc_stats_sum_over_files(world):
    s = world.store.stats(world.serial)[("psx", "FF7 (USA)")]
    assert (s.plays, s.seconds, s.last_played, s.name) == (4, 6000, datetime(2026, 8, 1, 12), "Final Fantasy VII")


def test_rows_states(world):
    states = {r.key: r.state for r in world.rows()}
    assert states[("ps2", "God of War II (USA)")] == "on_device"
    assert states[("ps2", "Champions (USA)")] == "library_only"
    assert states[("ps2", "Device Only (USA)")] == "device_only"
    assert states[("ps2", "Only Archive (USA)")] == "needs_conversion"
    assert states[("psx", "FF7 (USA)")] == "on_device"
    assert len(states) == 9


def test_row_fields(world):
    r = world.row("psx", "MTV (USA)")
    assert r.title == "MTV Music Generator" and r.on_device and r.want
    assert r.size == len(b"t1" * 300) + len(b"t2" * 100) + world.dev_path("psx", "MTV (USA).cue").stat().st_size
    assert set(r.dev_unit.files) == {"MTV (USA).cue", "MTV (USA) (Track 1).bin", "MTV (USA) (Track 2).bin"}
    lib_only = world.row("gamecube", "Pikmin 2 (USA)")
    assert (lib_only.title, lib_only.size, lib_only.on_device, lib_only.want) == ("Pikmin 2", 800, False, False)
    assert catalog.clean_title("Legend of Zelda, The - The Wind Waker (USA) (Rev 2) [b]") == \
        "Legend of Zelda, The - The Wind Waker"
    assert catalog.clean_title("(USA)") == "(USA)"
    assert lib_only.lib_unit.head == "Pikmin 2 (USA).rvz" and lib_only.dev_unit is None


def test_rows_available_offline(world):
    online = [(r.key, r.state, r.size, r.plays) for r in world.rows()]
    world.dev.online = False
    assert [(r.key, r.state, r.size, r.plays) for r in world.rows()] == online


def test_rows_without_any_device(world):
    rows = world.rows(serial=None)
    assert {r.state for r in rows} == {"library_only", "needs_conversion"}
    assert len(rows) == 8


def test_want_override(world):
    world.store.set_wanted(world.serial, "ps2", "God of War II (USA)", False)
    world.store.set_wanted(world.serial, "ps2", "Champions (USA)", True)
    assert world.row("ps2", "God of War II (USA)").want is False
    assert world.row("ps2", "Champions (USA)").want is True
    assert world.row("psx", "FF7 (USA)").want is True


def test_smart_never_played_threshold(world):
    cfg = Config()
    keys = {r.key for r in catalog.smart(world.rows(), "never_played", cfg, NOW)}
    assert ("ps2", "Baldur's Gate & Co (USA)") in keys          # 15 s is a test launch
    assert ("gamecube", "Metroid Prime (USA)") in keys
    assert ("ps2", "God of War II (USA)") not in keys
    assert ("ps2", "Champions (USA)") not in keys               # not on the device
    world.store.upsert_stats(world.serial, "ps2", "Baldur's Gate & Co (USA)", Stats(2, 61, NOW, ""))
    keys = {r.key for r in catalog.smart(world.rows(), "never_played", cfg, NOW)}
    assert ("ps2", "Baldur's Gate & Co (USA)") not in keys


def test_smart_not_played_window(world):
    cfg = Config()
    keys = [r.key for r in catalog.smart(world.rows(), "not_played", cfg, NOW)]
    assert keys == [("psx", "FF7 (USA)")]                       # Aug 1 is more than 90 days ago
    cfg.not_played_days = 10
    keys = {r.key for r in catalog.smart(world.rows(), "not_played", cfg, NOW)}
    assert keys == {("psx", "FF7 (USA)"), ("ps2", "God of War II (USA)")}


def test_smart_most_played_largest_device_only(world):
    cfg = Config()
    most = [r.key for r in catalog.smart(world.rows(), "most_played", cfg, NOW)]
    assert most[:2] == [("psx", "FF7 (USA)"), ("ps2", "God of War II (USA)")]
    largest = catalog.smart(world.rows(), "largest", cfg, NOW)
    assert largest[0].key == ("ps2", "God of War II (USA)") and all(r.on_device for r in largest)
    assert [r.key for r in catalog.smart(world.rows(), "device_only", cfg, NOW)] == [("ps2", "Device Only (USA)")]
    assert set(catalog.SMART_LISTS) == {"never_played", "not_played", "most_played", "largest", "device_only"}


def test_stats_survive_game_leaving_device(world):
    world.dev_path("ps2", "God of War II (USA).chd").unlink()
    write_tree(world.dev.root / GL.lstrip("/"), {"ps2": {"gamelist.xml": "<gameList>\n</gameList>\n"}})
    world.refresh()
    r = world.row("ps2", "God of War II (USA)")
    assert (r.state, r.plays, r.seconds, r.title) == ("library_only", 2, 3000, "God of War II")


def test_usage_by_system(world):
    usage = catalog.usage_by_system(world.rows())
    assert [s.folder for s, _ in usage] == ["ps2", "psx", "gamecube"]
    assert usage[0][1] == 4000 + 1400 + 500


def test_manifest_hashes_seed_the_device(world):
    good = "a" * 32
    (world.lib_root / "card-pull-manifest.tsv").write_text(
        "system\tfile\tbytes\tmd5_card\tmd5_venus\tsize_venus\tstate\n"
        f"ps2\tGod of War II (USA).chd\t4000\t{good}\t{good}\t4000\tok\n")
    assert catalog.seed_from_manifest(world.library, world.store, world.serial) == 1
    mtime = world.dev.stat(CARD + "/ps2/God of War II (USA).chd").mtime
    assert world.store.dev_hash(world.serial, "ps2", "God of War II (USA).chd", 4000, mtime) == good
    assert world.store.dev_hash(world.serial, "ps2", "God of War II (USA).chd", 4000, mtime + 1) is None
    assert world.library.cached_md5("ps2", "God of War II (USA).chd") == good


def test_demo_world_builds(tmp_path):
    from jewelcase.core import demo
    cfg, store, dev = demo.make_demo(tmp_path)
    library = Library(cfg.library, store)
    library.scan()
    profile = catalog.detect_profile(dev)
    catalog.refresh_device(dev, profile, store, library.systems())
    rows = catalog.build_rows(library, store, dev.serial)
    assert len(rows) >= 35
    assert {r.state for r in rows} == {"on_device", "library_only", "device_only", "needs_conversion"}
    assert len({r.system for r in rows}) >= 6
    assert any(r.seconds > 3600 for r in rows) and any("'" in r.stem for r in rows)
