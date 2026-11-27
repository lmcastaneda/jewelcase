from datetime import datetime

from jewelcase.core.db import LibFile, Stats, Store
from jewelcase.core.device import RemoteFile

S = "SER1"


def test_schema_version_recorded():
    assert Store(":memory:").schema_version() == 3


def test_lib_files_replace_and_list():
    st = Store(":memory:")
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 10, 111, None), LibFile("ps2", "B.chd", 20, 222, None)])
    st.replace_lib_files("psx", [LibFile("psx", "C.chd", 30, 333, None)])
    assert [f.name for f in st.lib_files("ps2")] == ["A.chd", "B.chd"]
    assert len(st.lib_files()) == 3
    st.replace_lib_files("ps2", [LibFile("ps2", "B.chd", 20, 222, None)])
    assert [f.name for f in st.lib_files("ps2")] == ["B.chd"]
    assert st.lib_systems() == ["ps2", "psx"]


def test_replace_keeps_md5_when_unchanged():
    st = Store(":memory:")
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 10, 111, None)])
    st.set_lib_md5("ps2", "A.chd", 10, 111, "aa" * 16)
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 10, 111, None)])
    assert st.lib_files("ps2")[0].md5 == "aa" * 16


def test_replace_drops_md5_when_size_changes():
    st = Store(":memory:")
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 10, 111, None)])
    st.set_lib_md5("ps2", "A.chd", 10, 111, "aa" * 16)
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 11, 111, None)])
    assert st.lib_files("ps2")[0].md5 is None
    st.set_lib_md5("ps2", "A.chd", 11, 111, "bb" * 16)
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 11, 999, None)])
    assert st.lib_files("ps2")[0].md5 is None


def test_set_lib_md5_ignores_stale_identity():
    st = Store(":memory:")
    st.replace_lib_files("ps2", [LibFile("ps2", "A.chd", 10, 111, None)])
    st.set_lib_md5("ps2", "A.chd", 99, 111, "aa" * 16)   # hashed an older version of the file
    assert st.lib_files("ps2")[0].md5 is None


def test_dev_hash():
    st = Store(":memory:")
    assert st.dev_hash(S, "ps2", "A.chd", 10, 500) is None
    st.set_dev_hash(S, "ps2", "A.chd", 10, 500, "aa" * 16)
    assert st.dev_hash(S, "ps2", "A.chd", 10, 500) == "aa" * 16
    assert st.dev_hash(S, "ps2", "A.chd", 11, 500) is None   # size changed: not the same file
    assert st.dev_hash(S, "ps2", "A.chd", 10, 501) is None   # touched since: not trusted either
    assert st.dev_hash("OTHER", "ps2", "A.chd", 10, 500) is None
    st.forget_dev_hash(S, "ps2", "A.chd")
    assert st.dev_hash(S, "ps2", "A.chd", 10, 500) is None


def test_version_1_database_is_upgraded(tmp_path):
    import sqlite3
    f = tmp_path / "old.db"
    db = sqlite3.connect(f)
    db.execute("CREATE TABLE dev_hash (serial TEXT, system TEXT, name TEXT, size INTEGER, md5 TEXT, "
               "PRIMARY KEY (serial, system, name))")
    db.execute("INSERT INTO dev_hash VALUES ('S', 'ps2', 'A.chd', 10, 'aa')")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    st = Store(f)
    assert st.schema_version() == 3
    assert st.dev_hash("S", "ps2", "A.chd", 10, 500) is None     # an old hash has no time: checked again
    st.set_dev_hash("S", "ps2", "A.chd", 10, 500, "bb")
    assert st.dev_hash("S", "ps2", "A.chd", 10, 500) == "bb"


def test_lib_text_is_tied_to_the_file_it_came_from():
    st = Store(":memory:")
    st.set_lib_text("psx", "G.cue", 20, 7, 'FILE "G.bin" BINARY')
    assert st.lib_text("psx", "G.cue", 20, 7) == 'FILE "G.bin" BINARY'
    assert st.lib_text("psx", "G.cue", 21, 7) is None and st.lib_text("psx", "G.cue", 20, 8) is None


def test_snapshot_roundtrip():
    st = Store(":memory:")
    files = {"psx": [RemoteFile("/c/psx/It's (USA).cue", 100, 5), RemoteFile("/c/psx/It's (USA).bin", 9, 6)],
             "ps2": []}
    st.save_snapshot(S, files, {"/c/psx/It's (USA).cue": 'FILE "It\'s (USA).bin" BINARY'})
    assert st.snapshot(S) == {"psx": files["psx"]}
    assert st.snapshot_text(S) == {"/c/psx/It's (USA).cue": 'FILE "It\'s (USA).bin" BINARY'}
    st.save_snapshot(S, {"gba": [RemoteFile("/c/gba/x.zip", 1, 1)]}, {})
    assert list(st.snapshot(S)) == ["gba"] and st.snapshot_text(S) == {}
    assert st.snapshot("OTHER") == {}


def test_meta():
    st = Store(":memory:")
    assert st.meta(S, "profile") is None
    assert st.meta(S, "profile", {"a": 1}) == {"a": 1}
    st.set_meta(S, "profile", {"rom_root": "/c", "n": [1, 2]})
    assert st.meta(S, "profile") == {"rom_root": "/c", "n": [1, 2]}
    assert st.known_serials() == [S]


def test_stats_upsert_and_read():
    st = Store(":memory:")
    when = datetime(2026, 11, 20, 21, 30)
    st.upsert_stats(S, "psx", "FF7", Stats(3, 5400, when, "Final Fantasy VII"))
    assert st.stats(S) == {("psx", "FF7"): Stats(3, 5400, when, "Final Fantasy VII")}


def test_stats_never_decrease():
    st = Store(":memory:")
    old, new = datetime(2026, 11, 1), datetime(2026, 11, 20)
    st.upsert_stats(S, "psx", "FF7", Stats(3, 5400, new, "Final Fantasy VII"))
    st.upsert_stats(S, "psx", "FF7", Stats(0, 0, None, ""))          # entry was wiped on the device
    st.upsert_stats(S, "psx", "FF7", Stats(1, 60, old, "FF7"))
    assert st.stats(S)[("psx", "FF7")] == Stats(3, 5400, new, "FF7")
    st.upsert_stats(S, "psx", "FF7", Stats(4, 6000, datetime(2026, 11, 25), "FF7"))
    assert st.stats(S)[("psx", "FF7")].seconds == 6000


def test_wanted():
    st = Store(":memory:")
    st.set_wanted(S, "psx", "FF7", False)
    st.set_wanted(S, "ps2", "GoW", True)
    assert st.wanted(S) == {("psx", "FF7"): False, ("ps2", "GoW"): True}
    st.set_wanted(S, "psx", "FF7", None)
    assert st.wanted(S) == {("ps2", "GoW"): True}
    st.clear_wanted(S)
    assert st.wanted(S) == {}


def test_jobs():
    st = Store(":memory:")
    assert st.open_job(S) is None
    job = st.create_job(S, [{"kind": "push", "system": "ps2", "name": "A.chd"},
                            {"kind": "refresh"}])
    assert st.open_job(S) == job
    steps = st.job_steps(job)
    assert [(s.idx, s.kind, s.state) for s in steps] == [(0, "push", "pending"), (1, "refresh", "pending")]
    assert steps[0].payload == {"system": "ps2", "name": "A.chd"}
    st.set_step(job, 0, "failed", "md5 mismatch")
    assert st.job_steps(job)[0].message == "md5 mismatch"
    st.close_job(job, "done")
    assert st.open_job(S) is None
    last = st.last_job(S)
    assert last["id"] == job and last["state"] == "done" and last["finished"]


def test_reopen_persists(tmp_path):
    f = tmp_path / "sub" / "jewelcase.db"
    st = Store(f)
    st.set_meta(S, "k", "v")
    st.close()
    assert Store(f).meta(S, "k") == "v"


def test_usable_from_another_thread(tmp_path):
    import threading
    st = Store(tmp_path / "t.db")
    t = threading.Thread(target=lambda: st.set_meta(S, "k", 1))
    t.start()
    t.join()
    assert st.meta(S, "k") == 1


def test_reopen_job():
    st = Store(":memory:")
    job = st.create_job(S, [{"kind": "refresh"}])
    st.close_job(job, "cancelled")
    assert st.open_job(S) is None
    st.reopen_job(job)
    assert st.open_job(S) == job and st.last_job(S)["state"] == "open"


def test_version_2_database_gains_the_save_tables(tmp_path):
    import sqlite3
    f = tmp_path / "v2.db"
    Store(f).upsert_stats(S, "ps2", "A", Stats(3, 100, None, "A"))
    db = sqlite3.connect(f)
    for table in ("save_file", "save_version", "save_run"):
        db.execute(f"DROP TABLE {table}")
    db.execute("PRAGMA user_version = 2")
    db.commit()
    db.close()
    st = Store(f)
    assert st.schema_version() == 3 and st.stats(S)[("ps2", "A")].plays == 3
    assert st.save_files(S) == [] and st.last_save_run(S) is None


def test_save_rows_roundtrip():
    st = Store(":memory:")
    st.put_save_file(S, "ra-saves", "a.srm", 10, 500, "aa", "20260101-000000")
    st.put_save_file(S, "ra-saves", "b.srm", 11, 501, "bb", "20260101-000000")
    st.mark_saves_gone(S, "ra-saves", ["a.srm"])
    rows = st.save_files(S, "ra-saves")
    assert [(r.rel, r.size, r.mtime, r.md5, r.run, r.gone) for r in rows] == [
        ("a.srm", 10, 500, "aa", "20260101-000000", True), ("b.srm", 11, 501, "bb", "20260101-000000", False)]
    st.put_save_file(S, "ra-saves", "a.srm", 12, 600, "cc", "20260102-000000")
    assert not st.save_files(S, "ra-saves")[0].gone
    st.put_save_version(S, "ra-saves", "a.srm", "20260101-000000", 10, "aa")
    st.put_save_version(S, "ra-saves", "a.srm", "20251231-000000", 9, "zz")
    assert [v.stamp for v in st.save_versions(S, "ra-saves", "a.srm")] == ["20251231-000000", "20260101-000000"]
    st.drop_save_version(S, "ra-saves", "a.srm", "20251231-000000")
    assert [(v.stamp, v.size, v.md5) for v in st.save_versions(S, "ra-saves", "a.srm")] == [("20260101-000000", 10, "aa")]
    st.add_save_run(S, "20260102-000000", False, 1, {"stopped": "device"})
    assert st.last_save_run(S) is None and st.last_save_run(S, complete_only=False).detail == {"stopped": "device"}
    st.add_save_run(S, "20260103-000000", True, 2, {"skipped": []})
    assert st.last_save_run(S).stamp == "20260103-000000" and st.last_save_run(S).copied == 2
    assert st.max_save_stamp(S) == "20260103-000000" and st.max_save_stamp("OTHER") == ""
