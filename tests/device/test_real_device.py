"""Checks against a real handheld. Run with:  uv run pytest -m device tests/device -s

`readonly` tests never write to the device. `scratch` tests write only inside
<card>/_jewelcase_scratch and delete that folder afterwards.
"""
import hashlib
import os
import time
from shlex import quote

import pytest

from jewelcase.core import adb, catalog, gamelist
from jewelcase.core.catalog import Profile
from jewelcase.core.db import Store
from jewelcase.core.library import Library
from jewelcase.core.plan import build_plan
from jewelcase.core.sync import Runner

pytestmark = pytest.mark.device
AWKWARD = "Tom's Test & Co (USA)"


@pytest.fixture(scope="module")
def dev():
    exe = adb.find_adb(None)
    if not exe:
        pytest.skip("adb not found")
    targets = [t for t in adb.pick_targets(adb.list_targets(exe)) if t.state == "device"]
    if not targets:
        pytest.skip("no device connected")
    return adb.AdbDevice(exe, targets[0])


def test_readonly_info_and_profile(dev):
    info = dev.info()
    assert info.model and 0 <= info.battery <= 100 and info.power in ("ac", "usb", "none")
    profile = catalog.detect_profile(dev)
    assert profile is not None
    card = dev.storage(profile.card_root)
    assert card.total > card.free > 0
    print(f"\n{info.model} android {info.android} battery {info.battery}% {info.power} via {info.transport}")
    print(f"profile {profile}  card free {card.free / 1e9:.1f} GB of {card.total / 1e9:.1f} GB")


def test_readonly_snapshot_and_stats(dev):
    profile = catalog.detect_profile(dev)
    store = Store(":memory:")
    t0 = time.time()
    catalog.refresh_device(dev, profile, store)
    took = time.time() - t0
    snap = store.snapshot(dev.serial)
    files = sum(len(v) for v in snap.values())
    rows = catalog.build_rows(Library(None, store), store, dev.serial)
    played = [r for r in rows if r.seconds > 0]
    print(f"\n{files} files in {len(snap)} systems, {len(rows)} games, {len(played)} with play time; "
          f"refresh took {took:.1f}s")
    assert files > 0 and rows
    assert all("/images/" not in f.path for v in snap.values() for f in v)
    assert dev.foreground_package() != ""


def test_readonly_packages(dev):
    found = dev.packages(["org.es_de.frontend", "com.retroarch.aarch64", "no.such.package"])
    print("\n" + "\n".join(f"{p.package} {p.version} system={p.system} enabled={p.enabled}" for p in found))
    assert "no.such.package" not in [p.package for p in found]


def test_scratch_add_then_remove(dev, tmp_path):
    real = catalog.detect_profile(dev)
    scratch = real.card_root + "/_jewelcase_scratch"
    profile = Profile(scratch, scratch + "/_esde/gamelists", real.card_root)
    lib_root = tmp_path / "library"
    (lib_root / "ps2").mkdir(parents=True)
    (lib_root / "nes").mkdir()
    big = os.urandom(1 << 20) * 100                          # 100 MB, to time the copy
    (lib_root / "ps2" / f"{AWKWARD}.chd").write_bytes(big)
    (lib_root / "ps2" / "Keep Me (USA).chd").write_bytes(os.urandom(4096))
    (lib_root / "nes" / "Pokémon Test (USA).zip").write_bytes(os.urandom(2048))
    store = Store(":memory:")
    library = Library(lib_root, store)
    library.scan()
    events = []

    def runner():
        return Runner(lambda: dev, library, store, profile, dev.serial, log_path=tmp_path / "sync.log",
                      data_dir=tmp_path / "data", on_progress=events.append)

    def rows():
        catalog.refresh_device(dev, profile, store, library.systems())
        return catalog.build_rows(library, store, dev.serial)

    try:
        dev.mkdir(scratch + "/ps2")
        dev.mkdir(scratch + "/nes")
        # ---- add all three
        for r in rows():
            store.set_wanted(dev.serial, r.system, r.stem, True)
        plan = build_plan(rows(), dev.storage(real.card_root), library, dev.info())
        assert len(plan.adds) == 3 and plan.fits
        r1 = runner()
        t0 = time.time()
        assert r1.run(r1.start(plan)) == "done"
        took = time.time() - t0
        print(f"\nadded 3 games ({len(big) / 1e6:.0f} MB) in {took:.1f}s = {len(big) / 1e6 / took:.1f} MB/s overall")
        remote = f"{scratch}/ps2/{AWKWARD}.chd"
        assert dev.md5(remote) == hashlib.md5(big).hexdigest()
        names = {f.path.rsplit("/", 1)[-1] for f in dev.list_files(scratch, maxdepth=2)}
        assert names == {f"{AWKWARD}.chd", "Keep Me (USA).chd", "Pokémon Test (USA).zip"}
        assert any(e.kind == "push" and 0 < e.bytes_done < e.bytes_total for e in events), "no mid-copy progress"
        # ---- give ES-DE-style gamelists to edit
        xml = ('<?xml version="1.0"?>\n<gameList>\n'
               "\t<game>\n\t\t<path>./Tom&apos;s Test &amp; Co (USA).chd</path>\n\t\t<name>Tom</name>\n"
               "\t\t<playcount>2</playcount>\n\t\t<playtime>300</playtime>\n\t</game>\n"
               "\t<game>\n\t\t<path>./Keep Me (USA).chd</path>\n\t\t<name>Keep</name>\n\t</game>\n</gameList>\n")
        dev.mkdir(profile.gamelists_dir + "/ps2")
        dev.write_bytes(profile.gamelists_dir + "/ps2/gamelist.xml", xml.encode())
        dev.write_bytes(scratch + "/ps2/gamelist.xml", xml.encode())
        # ---- remove the awkward one and the accented one
        current = {r.key: r for r in rows()}
        assert current[("ps2", AWKWARD)].seconds == 300
        store.set_wanted(dev.serial, "ps2", AWKWARD, False)
        store.set_wanted(dev.serial, "nes", "Pokémon Test (USA)", False)
        plan = build_plan(rows(), dev.storage(real.card_root), library, dev.info())
        assert len(plan.removes) == 2 and not plan.skipped
        r2 = runner()
        job = r2.start(plan)
        assert r2.run(job) == "done"
        bad = [(s.kind, s.message) for s in store.job_steps(job) if s.state != "done"]
        assert not bad, bad
        names = {f.path.rsplit("/", 1)[-1] for f in dev.list_files(scratch, maxdepth=2)}
        assert names == {"Keep Me (USA).chd", "gamelist.xml"}
        for path in (profile.gamelists_dir + "/ps2/gamelist.xml", scratch + "/ps2/gamelist.xml"):
            assert set(gamelist.parse(dev.read_bytes(path).decode())) == {"Keep Me (USA).chd"}
        after = {r.key: r for r in catalog.build_rows(library, store, dev.serial)}
        assert after[("ps2", AWKWARD)].state == "library_only" and after[("ps2", AWKWARD)].seconds == 300
        assert len(list((tmp_path / "data" / "backups").rglob("*gamelist.xml"))) == 2
    finally:
        dev._sh("rm -rf " + quote(scratch))
        assert not dev.exists(scratch)
