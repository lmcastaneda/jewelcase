"""Backup runs: the mirror in the library, its history and its index."""
import os
from datetime import datetime
from pathlib import Path

import pytest

from jewelcase.core.db import Store
from jewelcase.core.fakedevice import FakeDevice
from jewelcase.core.savebackup import SaveBackup, saves_root
from jewelcase.core.savesources import Source, scan_all

RA = "/sdcard/RetroArch/saves"
ST = "/sdcard/RetroArch/states"
EB = RA + "/EarthBound (USA).srm"
EB_REL = "EarthBound (USA).srm"
SOURCES = [
    Source(id="ra-saves", package="com.retroarch.aarch64", label="RetroArch", kind="save", root=RA,
           include=("**/*.srm",), items="name", suffix=r"\.srm$"),
    Source(id="ra-states", package="com.retroarch.aarch64", label="RetroArch", kind="state", root=ST,
           include=("*.state*",), items="name", suffix=r"\.state[^/]*$"),
]
_clock = [1_800_000_000]


def put(dev, path: str, data: bytes) -> None:
    """Write a file on the fake device with a time later than any written before."""
    p = dev._p(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    _clock[0] += 60
    os.utime(p, (_clock[0], _clock[0]))


def delete(dev, path: str) -> None:
    dev._p(path).unlink()


def rows(b, source: str = "ra-saves") -> dict:
    return {row.rel: row for row in b.store.save_files("FAKE0001", source)}


class Harness(SaveBackup):
    sources = SOURCES

    def scans(self):
        return scan_all(self.dev, self.sources)

    def go(self, **kw):
        return self.run(self.scans(), **kw)

    def history(self, source: str, rel: str) -> list[Path]:
        folder = self.root / "history" / source / rel
        return sorted(folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def dev(tmp_path):
    d = FakeDevice(tmp_path / "dev")
    put(d, EB, b"v1")
    put(d, RA + "/core/Zelda.srm", b"z1")
    put(d, ST + "/EarthBound (USA).state", b"s1")
    return d


@pytest.fixture
def b(dev, tmp_path):
    return Harness(dev, Store(tmp_path / "jewelcase.db"), tmp_path / "lib" / "_saves" / "Fake-FAKE0001", "FAKE0001")


def test_saves_root_names_the_device(tmp_path):
    assert saves_root(tmp_path, "RG 477V", "ABC123") == tmp_path / "_saves" / "RG_477V-ABC123"
    (tmp_path / "_saves" / "Old_Name-ABC123").mkdir(parents=True)
    assert saves_root(tmp_path, "RG 477V", "ABC123") == tmp_path / "_saves" / "Old_Name-ABC123"


def test_first_run_copies_and_verifies(b, dev):
    r = b.go()
    assert r.complete and r.copied == 3 and r.unchanged == 0 and r.stopped == "" and r.skipped == []
    assert b.latest_path("ra-saves", EB_REL).read_bytes() == b"v1"
    assert b.latest_path("ra-saves", "core/Zelda.srm").read_bytes() == b"z1"
    assert b.latest_path("ra-states", "EarthBound (USA).state").read_bytes() == b"s1"
    assert b.latest_path("ra-saves", EB_REL) == b.root / "latest" / "ra-saves" / EB_REL
    assert not list(b.root.rglob("*.part"))
    row = rows(b)[EB_REL]
    assert (row.rel, row.size, row.run, row.gone) == (EB_REL, 2, r.run, False) and len(row.md5) == 32


def test_unchanged_files_are_not_read_again(b, dev):
    b.go()
    dev.calls.clear()
    r = b.go()
    assert r.copied == 0 and r.unchanged == 3 and r.complete
    assert not [c for c in dev.calls if c[0] in ("pull", "md5", "read")]


def test_changed_file_retires_the_old_copy(b, dev):
    first = b.go().run
    put(dev, EB, b"v2")
    second = b.go()
    assert second.copied == 1 and second.unchanged == 2
    assert b.latest_path("ra-saves", EB_REL).read_bytes() == b"v2"
    old = b.version_path("ra-saves", EB_REL, first)
    assert old.read_bytes() == b"v1" and old.name == f"{first}-{EB_REL}"
    assert old == b.root / "history" / "ra-saves" / EB_REL / old.name
    versions = b.store.save_versions("FAKE0001", "ra-saves", EB_REL)
    assert [(v.stamp, v.size) for v in versions] == [(first, 2)]


def test_touched_but_same_content_updates_only_the_index(b, dev):
    b.go()
    put(dev, EB, b"v1")
    r = b.go()
    assert r.copied == 0 and r.unchanged == 3 and b.history("ra-saves", EB_REL) == []
    dev.calls.clear()
    b.go()
    assert not [c for c in dev.calls if c[0] in ("pull", "md5")]       # the new time was remembered


def test_gone_from_device_keeps_the_copy(b, dev):
    b.go()
    delete(dev, EB)
    r = b.go()
    assert r.complete and b.latest_path("ra-saves", EB_REL).read_bytes() == b"v1"
    assert rows(b)[EB_REL].gone and not rows(b)["core/Zelda.srm"].gone
    put(dev, EB, b"v1")
    b.go()
    assert not rows(b)[EB_REL].gone


def test_a_vanished_folder_marks_its_saves_gone_but_keeps_them(b, dev):
    b.go()
    delete(dev, ST + "/EarthBound (USA).state")
    dev._p(ST).rmdir()
    r = b.go()
    assert r.complete and b.latest_path("ra-states", "EarthBound (USA).state").exists()
    assert b.store.save_files("FAKE0001", "ra-states")[0].gone


def test_empty_replacement_does_not_lose_the_good_save(b, dev):
    b.go()
    put(dev, EB, b"")
    b.go()
    put(dev, EB, b"x")
    b.go()
    assert sorted(p.read_bytes() for p in b.history("ra-saves", EB_REL)) == [b"", b"v1"]
    assert b.latest_path("ra-saves", EB_REL).read_bytes() == b"x"


def test_every_change_is_kept_even_when_content_returns(b, dev):
    """A date in the history must keep meaning what the save held then, so nothing is merged away."""
    for body in (b"v1", b"v2", b"v1", b"v2"):
        put(dev, EB, body)
        b.go()
    assert b.latest_path("ra-saves", EB_REL).read_bytes() == b"v2"
    assert [p.read_bytes() for p in b.history("ra-saves", EB_REL)] == [b"v1", b"v2", b"v1"]
    assert len(b.store.save_versions("FAKE0001", "ra-saves", EB_REL)) == 3


def test_torn_copy_is_retried_then_skipped(b, dev):
    dev.mutate_on_pull = 2
    r = b.go()
    assert ("ra-saves", EB_REL, "changed while copying") in r.skipped
    assert not b.latest_path("ra-saves", EB_REL).exists() and r.complete and r.copied == 2
    assert not list(b.root.rglob("*.part"))
    assert b.go().copied == 1                                    # the next run takes it


def test_one_torn_pull_is_recovered(b, dev):
    dev.mutate_on_pull = 1
    r = b.go()
    assert r.copied == 3 and r.skipped == [] and b.latest_path("ra-saves", EB_REL).read_bytes() == b"v1"


def test_torn_copy_never_replaces_a_good_one(b, dev):
    b.go()
    put(dev, EB, b"v2")
    dev.mutate_on_pull = 2
    r = b.go()
    assert r.skipped and b.latest_path("ra-saves", EB_REL).read_bytes() == b"v1"
    assert b.history("ra-saves", EB_REL) == []


def test_awkward_names(b, dev):
    names = ["It's #1 – Pokémon.srm", "-dash.srm", "a b/c'd \"q\".srm"]
    for n in names:
        put(dev, f"{RA}/{n}", n.encode())
    first = b.go().run
    for n in names:
        assert b.latest_path("ra-saves", n).read_bytes() == n.encode()
    put(dev, f"{RA}/{names[0]}", b"changed")
    b.go()
    assert b.version_path("ra-saves", names[0], first).read_bytes() == names[0].encode()
    assert b.version_path("ra-saves", names[0], first).name == f"{first}-{names[0]}"


def test_a_name_that_would_leave_the_folder_is_refused(b, dev):
    from jewelcase.core.savesources import SaveFile, SourceScan
    scan = SourceScan(SOURCES[0], RA, True, (SaveFile("ra-saves", "../../escape.srm", 2, 5),), (), ())
    r = b.run([scan])
    assert r.skipped == [("ra-saves", "../../escape.srm", "unsafe name")] and not list(b.root.parent.rglob("escape.srm"))


def test_unplug_stops_and_next_run_carries_on(b, dev):
    dev.drop_on_pull = 2
    r = b.go()
    assert r.stopped == "device" and not r.complete and r.copied == 1
    assert not list(b.root.rglob("*.part"))
    assert b.store.last_save_run("FAKE0001") is None
    dev.online = True
    again = b.go()
    assert again.complete and again.copied == 2 and again.unchanged == 1


def test_library_gone_stops(dev, tmp_path):
    (tmp_path / "plain-file").write_text("not a folder")
    b = Harness(dev, Store(tmp_path / "c.db"), tmp_path / "plain-file" / "_saves" / "x", "FAKE0001")
    r = b.go()
    assert r.stopped == "library" and not r.complete and r.copied == 0


def test_a_file_the_library_cannot_store_is_skipped(b, dev):
    blocker = b.latest_path("ra-saves", EB_REL)
    (blocker / "in-the-way").mkdir(parents=True)
    r = b.go()
    assert r.complete and r.copied == 2
    assert [(s, rel) for s, rel, _ in r.skipped] == [("ra-saves", EB_REL)]
    assert r.skipped[0][2].startswith("could not be stored")


def test_asked_to_stop_after_current_file(b):
    answers = iter([False, True])
    r = b.go(should_stop=lambda: next(answers, True))
    assert r.copied == 1 and r.stopped == "asked" and not r.complete


def test_stamps_never_collide(b, dev):
    b.now = lambda: datetime(2026, 1, 1, 12, 0, 0)
    a = b.go().run
    put(dev, EB, b"v2")
    c = b.go().run
    put(dev, EB, b"v3")
    d = b.go().run
    assert a == "20260101-120000" and a < c < d
    assert b.version_path("ra-saves", EB_REL, a).read_bytes() == b"v1"
    assert b.version_path("ra-saves", EB_REL, c).read_bytes() == b"v2"


def test_clock_set_back_still_moves_forward(b, dev):
    b.now = lambda: datetime(2026, 6, 1, 12, 0, 0)
    a = b.go().run
    b.now = lambda: datetime(2025, 1, 1, 0, 0, 0)
    put(dev, EB, b"v2")
    assert b.go().run > a


def test_runs_that_copy_nothing_still_get_their_own_name(b):
    b.now = lambda: datetime(2026, 1, 1, 12, 0, 0)
    names = [b.go(record=False).run for _ in range(3)]
    assert names == sorted(set(names)) and b.last_stamp() == names[-1]


def test_only_limits_and_is_never_complete(b, dev):
    r = b.go(only={("ra-saves", EB_REL)})
    assert r.copied == 1 and not r.complete
    assert not b.latest_path("ra-saves", "core/Zelda.srm").exists()
    delete(dev, RA + "/core/Zelda.srm")
    b.go(only={("ra-saves", EB_REL)})
    assert all(not row.gone for row in b.store.save_files("FAKE0001"))     # a partial run marks nothing gone


def test_record_false_writes_no_run_row(b):
    b.go(record=False)
    assert b.store.last_save_run("FAKE0001", complete_only=False) is None
    b.go()
    assert b.store.last_save_run("FAKE0001") is not None


def test_run_row_carries_skipped_and_unreadable(b, dev):
    put(dev, RA + "/locked/Hidden.srm", b"h")
    dev.unreadable = {RA + "/locked"}
    dev.mutate_on_pull = 2
    r = b.go()
    assert r.unreadable == {"ra-saves": 1}
    row = b.store.last_save_run("FAKE0001")
    assert row.stamp == r.run and row.complete and row.copied == 2
    assert row.detail["unreadable"] == {"ra-saves": 1}
    assert row.detail["skipped"] == [["ra-saves", EB_REL, "changed while copying"]]


def test_too_large_files_are_reported(b, dev, monkeypatch):
    from jewelcase.core import savesources
    monkeypatch.setattr(savesources, "MAX_FILE", 1)
    r = b.go()
    assert r.copied == 0 and ("ra-saves", EB_REL, "larger than 256 MB") in r.skipped


def test_a_copy_found_without_an_index_row_is_kept(b, dev):
    stray = b.latest_path("ra-saves", EB_REL)
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"from an earlier install")
    b.go()
    assert stray.read_bytes() == b"v1"
    assert [p.read_bytes() for p in b.history("ra-saves", EB_REL)] == [b"from an earlier install"]


def test_devices_do_not_share_an_index(b, dev, tmp_path):
    b.go()
    other = Harness(dev, b.store, tmp_path / "lib" / "_saves" / "Fake-OTHER", "OTHER")
    assert other.go().copied == 3 and len(b.store.save_files("FAKE0001")) == 3


# ---------------------------------------------------------------- how many versions are kept
from datetime import date, timedelta      # noqa: E402

from jewelcase.core.savebackup import keep   # noqa: E402


def test_keep_saves():
    today = date(2026, 11, 26)
    stamps = [f"202611{d:02d}-{h:02d}0000" for d in range(1, 27) for h in (9, 21)]      # 52 versions in November
    stamps += ["20260930-200000", "20260915-100000", "20260831-235900", "20260810-080000"]
    kept = keep(stamps, "save", today)
    assert set(sorted(stamps)[-10:]) <= kept                                   # the 10 newest
    assert {f"202611{d:02d}-210000" for d in range(1, 27)} <= kept             # newest of each day, last 30 days
    assert "20261101-090000" not in kept
    assert {"20260930-200000", "20260831-235900"} <= kept                      # newest of each month
    assert "20260915-100000" not in kept and "20260810-080000" not in kept
    assert len(kept) == 26 + 5 + 2


def test_keep_saves_daily_window_is_thirty_days():
    today = date(2026, 11, 26)
    stamps = ["20261028-090000", "20261028-210000", "20261020-090000", "20261020-210000", "20261005-120000"]
    stamps += [f"20261126-{h:02d}0000" for h in range(10, 22)]                 # 12 today, so old ones are not "newest 10"
    kept = keep(stamps, "save", today)
    assert "20261028-210000" in kept and "20261028-090000" not in kept         # 29 days ago: newest of that day
    assert "20261020-090000" not in kept and "20261020-210000" not in kept     # 37 days ago, and not October's newest
    assert "20261005-120000" not in kept


def test_keep_states():
    assert keep(["20261101-000000", "20261102-000000", "20261103-000000"], "state", date(2026, 11, 26)) \
        == {"20261102-000000", "20261103-000000"}
    assert keep([], "save", date(2026, 11, 26)) == set()


def test_prune_removes_files_and_rows_but_never_latest(b, dev):
    day = [datetime(2026, 3, 1, 8, 0, 0)]
    b.now = lambda: day[0]
    for i in range(14):
        put(dev, EB, f"save {i}".encode())
        put(dev, ST + "/EarthBound (USA).state", f"state {i}".encode())
        b.go()
        day[0] += timedelta(minutes=5)
    assert b.latest_path("ra-saves", EB_REL).read_bytes() == b"save 13"
    assert sorted(p.read_bytes() for p in b.history("ra-saves", EB_REL)) == sorted(
        f"save {i}".encode() for i in range(3, 13))                            # the 10 newest of 13 older ones
    assert len(b.store.save_versions("FAKE0001", "ra-saves", EB_REL)) == 10
    assert sorted(p.read_bytes() for p in b.history("ra-states", "EarthBound (USA).state")) == [b"state 11", b"state 12"]
    assert b.latest_path("ra-states", "EarthBound (USA).state").read_bytes() == b"state 13"


def test_an_interrupted_run_prunes_nothing(b, dev):
    for i in range(4):
        put(dev, ST + "/EarthBound (USA).state", f"state {i}".encode())
        b.go(only={("ra-states", "EarthBound (USA).state")})
    assert len(b.history("ra-states", "EarthBound (USA).state")) == 3


def test_index_rebuilt_from_folder(b, dev, tmp_path):
    b.go()
    put(dev, EB, b"v2")
    b.go()
    (b.latest_path("ra-saves", EB_REL).parent / "stale.srm.part").write_bytes(b"half")
    (b.root / "history" / "ra-saves" / EB_REL / "notes.txt").write_text("mine")
    fresh = Harness(dev, Store(tmp_path / "new.db"), b.root, "FAKE0001")
    assert fresh.rebuild_index() == 4                                          # three newest copies, one version
    assert fresh.rebuild_index() == 0
    r = fresh.go()
    assert r.copied == 0 and r.unchanged == 3 and r.complete
    versions = fresh.store.save_versions("FAKE0001", "ra-saves", EB_REL)
    assert len(versions) == 1 and fresh.version_path("ra-saves", EB_REL, versions[0].stamp).read_bytes() == b"v1"
    dev.calls.clear()
    fresh.go()
    assert not [c for c in dev.calls if c[0] in ("pull", "md5")]


def test_a_run_rebuilds_a_missing_index_by_itself(b, dev, tmp_path):
    b.go()
    fresh = Harness(dev, Store(tmp_path / "new.db"), b.root, "FAKE0001")
    r = fresh.go()
    assert r.copied == 0 and r.unchanged == 3
    assert b.history("ra-saves", EB_REL) == []


def test_hand_deleted_copy_is_copied_again(b, dev):
    b.go()
    b.latest_path("ra-saves", EB_REL).unlink()
    r = b.go()
    assert r.copied == 1 and b.latest_path("ra-saves", EB_REL).read_bytes() == b"v1"
    assert b.history("ra-saves", EB_REL) == []


def test_files_that_cannot_be_opened_are_reported_not_retried(b, dev):
    dev.unreadable_files = {EB}
    r = b.go()
    assert r.blocked == {"ra-saves": 1} and r.skipped == [] and r.copied == 2 and r.complete
    assert not [c for c in dev.calls if c[1] == EB]
    assert b.store.last_save_run("FAKE0001").detail["blocked"] == {"ra-saves": 1}


def test_a_backed_up_file_that_becomes_unreadable_is_not_gone(b, dev):
    b.go()
    dev.unreadable_files = {EB}
    r = b.go()
    assert r.blocked == {"ra-saves": 1} and not rows(b)[EB_REL].gone
    assert b.latest_path("ra-saves", EB_REL).read_bytes() == b"v1"


def test_keep_settings_keeps_five_and_a_daily_one_but_no_monthly_ones():
    today = date(2026, 11, 26)
    stamps = ["20260830-120000", "20260915-120000", "20261110-090000", "20261110-210000", "20261120-120000"]
    stamps += [f"20261125-{h:02d}0000" for h in range(10, 16)]                 # six on one day
    kept = keep(stamps, "settings", today)
    assert {"20261125-150000", "20261125-140000", "20261125-130000", "20261125-120000",
            "20261125-110000"} <= kept                                         # the 5 newest
    assert "20261125-100000" not in kept                                       # the sixth, and not the day's last
    assert {"20261110-210000", "20261120-120000"} <= kept and "20261110-090000" not in kept   # one per day
    assert "20260830-120000" not in kept and "20260915-120000" not in kept     # no monthly ones
    assert len(kept) == 5 + 2


def test_a_settings_file_is_pruned_with_the_settings_rule(tmp_path):
    from jewelcase.core.savesources import Source
    src = Source(id="cfg", package="p", label="P", kind="settings", root="/sdcard/P",
                 include=("*.cfg",), items="file")
    d = FakeDevice(tmp_path / "dev")
    b = SaveBackup(d, Store(tmp_path / "c.db"), tmp_path / "lib" / "_saves" / "Fake-FAKE0001", "FAKE0001")
    day = [datetime(2026, 3, 1, 8, 0, 0)]
    b.now = lambda: day[0]
    for n in range(9):                          # nine versions in a row, all on one day
        put(d, "/sdcard/P/a.cfg", f"v{n}".encode())
        day[0] += timedelta(minutes=5)
        assert b.run(scan_all(d, [src])).complete
    assert len(b.store.save_versions("FAKE0001", "cfg", "a.cfg")) <= 5
    assert b.latest_path("cfg", "a.cfg").read_bytes() == b"v8"
