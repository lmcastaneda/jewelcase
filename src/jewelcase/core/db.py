"""Everything Jewelcase remembers, in one SQLite file."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .device import RemoteFile

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lib_file (
    system TEXT, name TEXT, size INTEGER, mtime_ns INTEGER, md5 TEXT, PRIMARY KEY (system, name));
CREATE TABLE IF NOT EXISTS dev_hash (
    serial TEXT, system TEXT, name TEXT, size INTEGER, md5 TEXT, mtime INTEGER,
    PRIMARY KEY (serial, system, name));
CREATE TABLE IF NOT EXISTS lib_text (
    system TEXT, name TEXT, size INTEGER, mtime_ns INTEGER, body TEXT, PRIMARY KEY (system, name));
CREATE TABLE IF NOT EXISTS dev_file (
    serial TEXT, system TEXT, path TEXT, size INTEGER, mtime INTEGER, PRIMARY KEY (serial, path));
CREATE TABLE IF NOT EXISTS dev_text (
    serial TEXT, path TEXT, body TEXT, PRIMARY KEY (serial, path));
CREATE TABLE IF NOT EXISTS meta (
    serial TEXT, key TEXT, value TEXT, PRIMARY KEY (serial, key));
CREATE TABLE IF NOT EXISTS stats (
    serial TEXT, system TEXT, stem TEXT, plays INTEGER, seconds INTEGER, last_played TEXT, name TEXT,
    PRIMARY KEY (serial, system, stem));
CREATE TABLE IF NOT EXISTS wanted (
    serial TEXT, system TEXT, stem TEXT, want INTEGER, PRIMARY KEY (serial, system, stem));
CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT, created TEXT, finished TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS job_step (
    job_id INTEGER, idx INTEGER, kind TEXT, payload TEXT, state TEXT, message TEXT,
    PRIMARY KEY (job_id, idx));
CREATE TABLE IF NOT EXISTS save_file (
    serial TEXT, source TEXT, rel TEXT, size INTEGER, mtime INTEGER, md5 TEXT, run TEXT, gone INTEGER,
    PRIMARY KEY (serial, source, rel));
CREATE TABLE IF NOT EXISTS save_version (
    serial TEXT, source TEXT, rel TEXT, stamp TEXT, size INTEGER, md5 TEXT,
    PRIMARY KEY (serial, source, rel, stamp));
CREATE TABLE IF NOT EXISTS lib_game_id (
    system TEXT, name TEXT, size INTEGER, mtime_ns INTEGER, game_id TEXT, PRIMARY KEY (system, name));
CREATE TABLE IF NOT EXISTS save_run (
    serial TEXT, stamp TEXT, complete INTEGER, copied INTEGER, detail TEXT, PRIMARY KEY (serial, stamp));
"""


@dataclass(frozen=True)
class LibFile:
    system: str
    name: str
    size: int
    mtime_ns: int
    md5: str | None


@dataclass(frozen=True)
class Stats:
    plays: int = 0
    seconds: int = 0
    last_played: datetime | None = None
    name: str = ""


@dataclass(frozen=True)
class JobStep:
    idx: int
    kind: str
    payload: dict
    state: str        # pending | done | failed | skipped
    message: str


@dataclass(frozen=True)
class SaveRow:
    """The newest copy of one save file: what the device looked like when it was copied."""
    source: str
    rel: str
    size: int | None      # device size and time; None when the row was rebuilt from the folder
    mtime: int | None
    md5: str
    run: str              # the run that copied this content
    gone: bool            # no longer on the device


@dataclass(frozen=True)
class VersionRow:
    stamp: str
    size: int
    md5: str


@dataclass(frozen=True)
class RunRow:
    stamp: str
    complete: bool
    copied: int
    detail: dict


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                columns = [r[1] for r in self._db.execute("PRAGMA table_info(dev_hash)")]
                if "mtime" not in columns:      # a database from version 1: old hashes get rechecked
                    self._db.execute("ALTER TABLE dev_hash ADD COLUMN mtime INTEGER")
            if version != SCHEMA_VERSION:
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _q(self, sql: str, args=()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _tx(self):
        return _Tx(self._db, self._lock)

    def schema_version(self) -> int:
        return self._q("PRAGMA user_version")[0][0]

    # ------------------------------------------------------------ library index
    def lib_files(self, system: str | None = None) -> list[LibFile]:
        if system is None:
            rows = self._q("SELECT system, name, size, mtime_ns, md5 FROM lib_file ORDER BY system, name")
        else:
            rows = self._q("SELECT system, name, size, mtime_ns, md5 FROM lib_file WHERE system=? ORDER BY name",
                           (system,))
        return [LibFile(*r) for r in rows]

    def lib_systems(self) -> list[str]:
        return [r[0] for r in self._q("SELECT DISTINCT system FROM lib_file ORDER BY system")]

    def replace_lib_files(self, system: str, files: list[LibFile]) -> None:
        with self._tx() as db:
            old = {r[0]: r[1:] for r in db.execute(
                "SELECT name, size, mtime_ns, md5 FROM lib_file WHERE system=?", (system,))}
            db.execute("DELETE FROM lib_file WHERE system=?", (system,))
            for f in files:
                prev = old.get(f.name)
                md5 = f.md5 or (prev[2] if prev and prev[0] == f.size and prev[1] == f.mtime_ns else None)
                db.execute("INSERT INTO lib_file VALUES (?,?,?,?,?)", (system, f.name, f.size, f.mtime_ns, md5))
            for name in db.execute("SELECT name FROM lib_game_id WHERE system=?", (system,)).fetchall():
                if name[0] not in {f.name for f in files}:      # the file left the library: its ID goes too
                    db.execute("DELETE FROM lib_game_id WHERE system=? AND name=?", (system, name[0]))

    def drop_lib_system(self, system: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM lib_file WHERE system=?", (system,))
            db.execute("DELETE FROM lib_text WHERE system=?", (system,))
            db.execute("DELETE FROM lib_game_id WHERE system=?", (system,))

    def lib_text(self, system: str, name: str, size: int, mtime_ns: int) -> str | None:
        """The remembered contents of a .cue/.m3u, if the file has not changed since."""
        rows = self._q("SELECT body FROM lib_text WHERE system=? AND name=? AND size=? AND mtime_ns=?",
                       (system, name, size, mtime_ns))
        return rows[0][0] if rows else None

    def set_lib_text(self, system: str, name: str, size: int, mtime_ns: int, body: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO lib_text VALUES (?,?,?,?,?)", (system, name, size, mtime_ns, body))

    def set_lib_md5(self, system: str, name: str, size: int, mtime_ns: int, md5: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE lib_file SET md5=? WHERE system=? AND name=? AND size=? AND mtime_ns=?",
                       (md5, system, name, size, mtime_ns))

    def lib_game_id(self, system: str, name: str, size: int, mtime_ns: int) -> str | None:
        """The game ID read from a library file ("" = none found), only if the file is unchanged since."""
        rows = self._q("SELECT game_id FROM lib_game_id WHERE system=? AND name=? AND size=? AND mtime_ns=?",
                       (system, name, size, mtime_ns))
        return rows[0][0] if rows else None

    def set_lib_game_id(self, system: str, name: str, size: int, mtime_ns: int, game_id: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO lib_game_id VALUES (?,?,?,?,?)", (system, name, size, mtime_ns, game_id))

    # ------------------------------------------------------------ device hashes
    def dev_hash(self, serial: str, system: str, name: str, size: int, mtime: int) -> str | None:
        """The hash recorded for a device file, only if it still has that size and modification time."""
        rows = self._q("SELECT md5 FROM dev_hash WHERE serial=? AND system=? AND name=? AND size=? AND mtime=?",
                       (serial, system, name, size, mtime))
        return rows[0][0] if rows else None

    def set_dev_hash(self, serial: str, system: str, name: str, size: int, mtime: int, md5: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO dev_hash (serial, system, name, size, md5, mtime) "
                       "VALUES (?,?,?,?,?,?)", (serial, system, name, size, md5, mtime))

    def forget_dev_hash(self, serial: str, system: str, name: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM dev_hash WHERE serial=? AND system=? AND name=?", (serial, system, name))

    # ------------------------------------------------------------ device snapshot
    def save_snapshot(self, serial: str, files: dict[str, list[RemoteFile]], texts: dict[str, str]) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM dev_file WHERE serial=?", (serial,))
            db.execute("DELETE FROM dev_text WHERE serial=?", (serial,))
            for system, items in files.items():
                db.executemany("INSERT OR REPLACE INTO dev_file VALUES (?,?,?,?,?)",
                               [(serial, system, f.path, f.size, f.mtime) for f in items])
            db.executemany("INSERT OR REPLACE INTO dev_text VALUES (?,?,?)",
                           [(serial, p, body) for p, body in texts.items()])

    def snapshot(self, serial: str) -> dict[str, list[RemoteFile]]:
        out: dict[str, list[RemoteFile]] = {}
        for system, path, size, mtime in self._q(
                "SELECT system, path, size, mtime FROM dev_file WHERE serial=? ORDER BY rowid", (serial,)):
            out.setdefault(system, []).append(RemoteFile(path, size, mtime))
        return out

    def snapshot_text(self, serial: str) -> dict[str, str]:
        return dict(self._q("SELECT path, body FROM dev_text WHERE serial=?", (serial,)))

    # ------------------------------------------------------------ per-device values
    def meta(self, serial: str, key: str, default=None):
        rows = self._q("SELECT value FROM meta WHERE serial=? AND key=?", (serial, key))
        return json.loads(rows[0][0]) if rows else default

    def set_meta(self, serial: str, key: str, value) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?)", (serial, key, json.dumps(value)))

    def known_serials(self) -> list[str]:
        return [r[0] for r in self._q("SELECT DISTINCT serial FROM meta ORDER BY serial")]

    # ------------------------------------------------------------ play stats
    def stats(self, serial: str) -> dict[tuple[str, str], Stats]:
        out = {}
        for system, stem, plays, seconds, last, name in self._q(
                "SELECT system, stem, plays, seconds, last_played, name FROM stats WHERE serial=?", (serial,)):
            out[(system, stem)] = Stats(plays, seconds, datetime.fromisoformat(last) if last else None, name)
        return out

    def upsert_stats(self, serial: str, system: str, stem: str, s: Stats) -> None:
        with self._tx() as db:
            row = db.execute("SELECT plays, seconds, last_played, name FROM stats "
                             "WHERE serial=? AND system=? AND stem=?", (serial, system, stem)).fetchone()
            plays, seconds, last, name = s.plays, s.seconds, s.last_played, s.name
            if row:
                plays, seconds = max(plays, row[0]), max(seconds, row[1])
                old_last = datetime.fromisoformat(row[2]) if row[2] else None
                last = max([d for d in (last, old_last) if d], default=None)
                name = name or row[3]
            db.execute("INSERT OR REPLACE INTO stats VALUES (?,?,?,?,?,?,?)",
                       (serial, system, stem, plays, seconds, last.isoformat() if last else None, name))

    # ------------------------------------------------------------ ticks
    def wanted(self, serial: str) -> dict[tuple[str, str], bool]:
        return {(a, b): bool(w) for a, b, w in
                self._q("SELECT system, stem, want FROM wanted WHERE serial=?", (serial,))}

    def set_wanted(self, serial: str, system: str, stem: str, want: bool | None) -> None:
        with self._tx() as db:
            if want is None:
                db.execute("DELETE FROM wanted WHERE serial=? AND system=? AND stem=?", (serial, system, stem))
            else:
                db.execute("INSERT OR REPLACE INTO wanted VALUES (?,?,?,?)", (serial, system, stem, int(want)))

    def clear_wanted(self, serial: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM wanted WHERE serial=?", (serial,))

    # ------------------------------------------------------------ sync journal
    def create_job(self, serial: str, steps: list[dict]) -> int:
        with self._tx() as db:
            job = db.execute("INSERT INTO job (serial, created, state) VALUES (?,?, 'open')",
                             (serial, _now())).lastrowid
            for i, step in enumerate(steps):
                payload = {k: v for k, v in step.items() if k != "kind"}
                db.execute("INSERT INTO job_step VALUES (?,?,?,?, 'pending', '')",
                           (job, i, step["kind"], json.dumps(payload)))
            return job

    def job_steps(self, job_id: int) -> list[JobStep]:
        return [JobStep(i, kind, json.loads(payload), state, msg) for i, kind, payload, state, msg in self._q(
            "SELECT idx, kind, payload, state, message FROM job_step WHERE job_id=? ORDER BY idx", (job_id,))]

    def set_step(self, job_id: int, idx: int, state: str, message: str = "") -> None:
        with self._tx() as db:
            db.execute("UPDATE job_step SET state=?, message=? WHERE job_id=? AND idx=?",
                       (state, message, job_id, idx))

    def open_job(self, serial: str) -> int | None:
        rows = self._q("SELECT id FROM job WHERE serial=? AND state='open' ORDER BY id DESC LIMIT 1", (serial,))
        return rows[0][0] if rows else None

    def close_job(self, job_id: int, state: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE job SET state=?, finished=? WHERE id=?", (state, _now(), job_id))

    def reopen_job(self, job_id: int) -> None:
        with self._tx() as db:
            db.execute("UPDATE job SET state='open', finished=NULL WHERE id=?", (job_id,))

    def last_job(self, serial: str) -> dict | None:
        rows = self._q("SELECT id, state, created, finished FROM job WHERE serial=? ORDER BY id DESC LIMIT 1",
                       (serial,))
        return dict(zip(("id", "state", "created", "finished"), rows[0])) if rows else None


    # ------------------------------------------------------------ save backups
    def save_files(self, serial: str, source: str | None = None) -> list[SaveRow]:
        sql = "SELECT source, rel, size, mtime, md5, run, gone FROM save_file WHERE serial=?"
        args: tuple = (serial,)
        if source is not None:
            sql, args = sql + " AND source=?", (serial, source)
        return [SaveRow(s, rel, size, mtime, md5, run, bool(gone))
                for s, rel, size, mtime, md5, run, gone in self._q(sql + " ORDER BY source, rel", args)]

    def put_save_file(self, serial: str, source: str, rel: str, size: int | None, mtime: int | None,
                      md5: str, run: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO save_file VALUES (?,?,?,?,?,?,?,0)",
                       (serial, source, rel, size, mtime, md5, run))

    def mark_saves_gone(self, serial: str, source: str, rels) -> None:
        with self._tx() as db:
            db.executemany("UPDATE save_file SET gone=1 WHERE serial=? AND source=? AND rel=?",
                           [(serial, source, rel) for rel in rels])

    def save_versions(self, serial: str, source: str, rel: str) -> list[VersionRow]:
        return [VersionRow(*r) for r in self._q(
            "SELECT stamp, size, md5 FROM save_version WHERE serial=? AND source=? AND rel=? ORDER BY stamp",
            (serial, source, rel))]

    def all_save_versions(self, serial: str) -> dict[tuple[str, str], list[VersionRow]]:
        out: dict[tuple[str, str], list[VersionRow]] = {}
        for source, rel, stamp, size, md5 in self._q(
                "SELECT source, rel, stamp, size, md5 FROM save_version WHERE serial=? ORDER BY stamp", (serial,)):
            out.setdefault((source, rel), []).append(VersionRow(stamp, size, md5))
        return out

    def put_save_version(self, serial: str, source: str, rel: str, stamp: str, size: int, md5: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO save_version VALUES (?,?,?,?,?,?)",
                       (serial, source, rel, stamp, size, md5))

    def drop_save_version(self, serial: str, source: str, rel: str, stamp: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM save_version WHERE serial=? AND source=? AND rel=? AND stamp=?",
                       (serial, source, rel, stamp))

    def add_save_run(self, serial: str, stamp: str, complete: bool, copied: int, detail: dict) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO save_run VALUES (?,?,?,?,?)",
                       (serial, stamp, int(complete), copied, json.dumps(detail)))

    def last_save_run(self, serial: str, complete_only: bool = True) -> RunRow | None:
        rows = self._q("SELECT stamp, complete, copied, detail FROM save_run WHERE serial=?"
                       + (" AND complete=1" if complete_only else "") + " ORDER BY stamp DESC LIMIT 1", (serial,))
        return RunRow(rows[0][0], bool(rows[0][1]), rows[0][2], json.loads(rows[0][3])) if rows else None

    def save_runs(self, serial: str, limit: int = 100) -> list[RunRow]:
        """Runs that copied something, newest first."""
        return [RunRow(stamp, bool(complete), copied, json.loads(detail)) for stamp, complete, copied, detail in
                self._q("SELECT stamp, complete, copied, detail FROM save_run WHERE serial=? AND copied>0 "
                        "ORDER BY stamp DESC LIMIT ?", (serial, limit))]

    def max_save_stamp(self, serial: str) -> str:
        """The latest stamp any run, copy or version of this device carries."""
        found = [r[0] for r in self._q(
            "SELECT MAX(stamp) FROM save_run WHERE serial=? UNION ALL "
            "SELECT MAX(run) FROM save_file WHERE serial=? UNION ALL "
            "SELECT MAX(stamp) FROM save_version WHERE serial=?", (serial, serial, serial)) if r[0]]
        return max(found, default="")


class _Tx:
    def __init__(self, db: sqlite3.Connection, lock: threading.RLock):
        self.db, self.lock = db, lock

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        self.db.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.lock.release()
