"""A game being added gets its cover copied into ES-DE's folder on the handheld, so it is not bare there."""
import hashlib

import pytest

from jewelcase.core import covers
from jewelcase.core.art import Art
from jewelcase.core.catalog import Profile
from jewelcase.core.device import DeviceError, DeviceGone
from jewelcase.core.plan import build_plan
from jewelcase.core.sync import Runner

from conftest import CARD, GL

MEDIA = "sdcard/ES-DE/downloaded_media"
PNG = b"\x89PNG\r\n\x1a\n" + b"cover-of-champions"
NEW = "Champions (USA)"


class Run:
    def __init__(self, world, tmp_path, with_art=True):
        self.w, self.tmp, self.events = world, tmp_path, []
        self.art = Art(tmp_path / "cache" / "art")
        self.profile = world.profile
        self.runner = self.new_runner(with_art)

    def new_runner(self, with_art=True):
        w = self.w
        return Runner(lambda: w.dev if w.dev.online else None, w.library, w.store, self.profile, w.serial,
                      log_path=self.tmp / "sync.log", data_dir=self.tmp / "data", on_progress=self.events.append,
                      sleep=lambda s: None, art=self.art if with_art else None)

    def give_cover(self, stem=NEW, system="ps2", body=PNG, ext=".png"):
        assert self.art.store_download(system, stem, body, ext)

    def want(self, stem=NEW, system="ps2"):
        self.w.store.set_wanted(self.w.serial, system, stem, True)

    def plan(self):
        w = self.w
        return build_plan(w.rows(), w.dev.storage(CARD), w.library, w.dev.info())

    def sync(self):
        self.job = self.runner.start(self.plan())
        return self.runner.run(self.job)

    def steps(self, kind="cover"):
        return [s for s in self.w.store.job_steps(self.job) if s.kind == kind]

    def on_device(self, name):
        return self.w.dev.root / MEDIA / "ps2" / "covers" / name


@pytest.fixture
def run(world, tmp_path):
    return Run(world, tmp_path / "run")


def test_a_cover_arrives_next_to_the_others_in_es_des_folder(run):
    run.give_cover()
    run.want()
    assert run.sync() == "done"
    assert run.on_device(NEW + ".png").read_bytes() == PNG
    assert run.on_device("God of War II (USA).png").read_bytes() == b"cover"          # ES-DE's own, untouched
    assert [s.state for s in run.steps()] == ["done"]
    assert not list(run.on_device("x").parent.glob("*.part"))
    assert run.w.dev_path("ps2", NEW + ".chd").exists()


def test_the_cover_keeps_its_extension_and_is_checked_with_md5(run):
    run.give_cover(body=b"\xff\xd8\xff" + b"jpeg", ext=".jpg")
    run.want()
    run.sync()
    got = run.on_device(NEW + ".jpg")
    assert hashlib.md5(got.read_bytes()).hexdigest() == hashlib.md5(b"\xff\xd8\xffjpeg").hexdigest()
    assert ("chmod", f"/{MEDIA}/ps2/covers/{NEW}.jpg.part") in run.w.dev.calls
    assert any(c[0] == "rename" or c[0] == "md5" for c in run.w.dev.calls)


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp"])
def test_a_picture_of_any_extension_in_es_des_folder_is_left_alone(run, ext):
    run.give_cover()
    existing = run.on_device(NEW + ext)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"scraped by ES-DE")
    run.want()
    assert run.sync() == "done"
    assert existing.read_bytes() == b"scraped by ES-DE"
    assert sorted(p.name for p in existing.parent.glob(NEW + ".*")) == [NEW + ext]
    assert [(s.state, s.message) for s in run.steps()] == [("skipped", "ES-DE already has a cover")]


def test_a_handheld_without_es_de_gets_no_cover_step(world, tmp_path):
    r = Run(world, tmp_path / "run")
    r.profile = Profile(world.profile.rom_root, None, world.profile.card_root)
    r.runner = r.new_runner()
    r.give_cover()
    r.want()
    assert r.sync() == "done"
    assert r.steps() == [] and not (world.dev.root / MEDIA / "ps2" / "covers" / (NEW + ".png")).exists()


def test_a_game_without_any_cover_gets_no_cover_step(run):
    run.want()
    assert run.sync() == "done" and run.steps() == []


def test_a_cover_step_that_fails_leaves_the_game_added_and_the_sync_done(run):
    run.give_cover()
    run.want()
    real = run.w.dev.push

    def push(local, remote, progress=None):
        if "downloaded_media" in remote:
            raise DeviceError("no space left")
        return real(local, remote, progress)
    run.w.dev.push = push
    assert run.sync() == "done"
    assert run.w.dev_path("ps2", NEW + ".chd").read_bytes() == b"ch" * 800
    assert run.w.row("ps2", NEW).state == "on_device"
    assert [s.state for s in run.steps()] == ["skipped"] and "no space" in run.steps()[0].message
    assert not list(run.on_device("x").parent.glob("*.part"))
    assert [s.state for s in run.steps("push")] == ["done"]


def test_a_copy_that_does_not_match_is_not_kept(run):
    run.give_cover()
    run.want()
    real = run.w.dev.md5
    run.w.dev.md5 = lambda path: "0" * 32 if "downloaded_media" in path else real(path)
    assert run.sync() == "done"
    assert not run.on_device(NEW + ".png").exists() and not list(run.on_device("x").parent.glob(NEW + "*"))
    assert [s.state for s in run.steps()] == ["skipped"]


def test_a_sync_that_paused_carries_the_cover_on_when_resumed(run):
    run.give_cover()
    run.want()
    job = run.runner.start(run.plan())
    run.job = job

    def pause_at_cover(event):
        run.events.append(event)
        if event.kind == "cover":
            run.runner.stop("pause")
    run.runner.on_progress = pause_at_cover
    assert run.runner.run(job) == "paused"
    assert run.w.dev_path("ps2", NEW + ".chd").exists() and not run.on_device(NEW + ".png").exists()
    second = run.new_runner()
    assert second.run(job) == "done"
    assert run.on_device(NEW + ".png").read_bytes() == PNG
    assert run.steps()[0].state == "done"


def test_a_game_unticked_while_paused_gets_no_cover_either(run):
    other = ("gamecube", "Pikmin 2 (USA)")
    run.give_cover()
    run.give_cover(other[1], other[0])
    run.want()
    run.want(other[1], other[0])
    job = run.runner.start(run.plan())
    run.job = job
    run.runner.on_progress = lambda e: run.runner.stop("pause") if e.kind == "cover" else None
    assert run.runner.run(job) == "paused"                  # the first game is added, its cover not yet
    first = run.steps()[0].payload
    second = next(k for k in (("ps2", NEW), other) if k != (first["system"], first["stem"]))
    run.w.store.set_wanted(run.w.serial, *second, False)
    assert run.new_runner().run(job) == "done"
    states = {(s.payload["system"], s.payload["stem"]): s.state for s in run.steps()}
    assert states[second] == "skipped" and states[(first["system"], first["stem"])] == "done"
    assert not list((run.w.dev.root / MEDIA).glob(f"*/covers/{second[1]}.*"))


def test_the_review_plan_names_the_games_whose_cover_goes_along(world, tmp_path):
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=tmp_path / "demo")
    s.connect()
    s.refresh()
    row = next(r for r in s.rows() if r.state == "library_only")
    s.store.set_wanted(s.serial, row.system, row.stem, True)
    assert s.plan().covers == frozenset()
    assert s.art.store_download(row.system, row.stem, PNG, ".png")
    assert s.plan().covers == {row.key}


# ---------------------------------------------------------------- games already on the handheld
def test_push_missing_covers_copies_only_where_es_de_has_none(world, tmp_path):
    art = Art(tmp_path / "art")
    art.store_download("ps2", "Baldur's Gate & Co (USA)", PNG, ".png")
    art.store_download("ps2", "God of War II (USA)", b"mine", ".png")          # ES-DE has one on the handheld
    art.store_download("psx", "Nothing (USA)", PNG, ".png")                     # not on the handheld
    copied, left = covers.push_missing(world.dev, world.profile, art, world.rows())
    assert copied == 1
    assert (world.dev.root / MEDIA / "ps2/covers/Baldur's Gate & Co (USA).png").read_bytes() == PNG
    assert (world.dev.root / MEDIA / "ps2/covers/God of War II (USA).png").read_bytes() == b"cover"
    assert covers.push_missing(world.dev, world.profile, art, world.rows())[0] == 0


def test_the_covers_command_pushes_under_the_sync_lock(capsys, tmp_path):
    import json
    from jewelcase import cli
    from jewelcase.core.sync import job_lock
    demo = tmp_path / "demo"
    cli.main(["--demo-dir", str(demo), "covers"])
    capsys.readouterr()
    from jewelcase.core.session import open_session
    s = open_session(demo_dir=demo)
    s.connect()
    s.refresh()
    row = next(r for r in s.rows() if r.on_device and s.art.cover(r.system, r.stem) is None)
    s.art.store_download(row.system, row.stem, PNG, ".png")
    with job_lock(s.data_dir):
        code = cli.main(["--demo-dir", str(demo), "--json", "covers", "--push"])
    assert code == 2 and "syncing" in capsys.readouterr().out
    code = cli.main(["--demo-dir", str(demo), "--json", "covers", "--push"])
    assert code == 0 and json.loads(capsys.readouterr().out)["copied_to_handheld"] >= 1


# ---------------------------------------------------------------- review fixes
def count_covers_listings(dev):
    real, seen = dev.list_files, []

    def list_files(root, maxdepth=None):
        if root.endswith("/covers"):
            seen.append(root)
        return real(root, maxdepth)
    dev.list_files = list_files
    return seen


def test_a_covers_folder_that_cannot_be_listed_never_stops_the_sync(run):
    run.give_cover()
    run.want()
    real = run.w.dev.list_files

    def list_files(root, maxdepth=None):
        if root.endswith("/covers"):
            raise DeviceError("Permission denied")
        return real(root, maxdepth)
    run.w.dev.list_files = list_files
    assert run.sync() == "done"
    assert [s.state for s in run.steps()] == ["skipped"] and "Permission denied" in run.steps()[0].message
    assert run.w.dev_path("ps2", NEW + ".chd").exists()


def test_copy_cover_returns_skipped_when_the_folder_cannot_be_listed(world, tmp_path):
    art = Art(tmp_path / "art")
    art.store_download("ps2", NEW, PNG, ".png")
    world.dev.list_files = lambda root, maxdepth=None: (_ for _ in ()).throw(DeviceError("denied"))
    state, why = covers.copy_cover(world.dev, world.profile, art, "ps2", NEW)
    assert (state, why) == ("skipped", "denied")


def test_a_game_whose_own_copy_failed_gets_no_cover(run):
    run.give_cover()
    run.want()
    other = run.w.dev_path("ps2", NEW + ".chd")
    other.write_bytes(b"a different file")
    assert run.sync() == "done"
    assert [s.state for s in run.steps("push")] == ["failed"]
    assert [s.state for s in run.steps()] == ["skipped"]
    assert not run.on_device(NEW + ".png").exists()


def test_the_covers_folder_of_a_system_is_listed_once_per_sync(run):
    third = "Third Game (USA)"
    (run.w.lib_root / "ps2" / (third + ".chd")).write_bytes(b"3" * 300)
    run.w.refresh()
    for stem in (NEW, third):
        run.give_cover(stem)
        run.want(stem)
    seen = count_covers_listings(run.w.dev)
    assert run.sync() == "done"
    assert [s.state for s in run.steps()] == ["done", "done"]
    assert len(seen) == 1


def test_push_missing_lists_the_covers_folder_of_no_game_again(world, tmp_path):
    art = Art(tmp_path / "art")
    art.store_download("ps2", "Baldur's Gate & Co (USA)", PNG, ".png")
    art.store_download("ps2", "Device Only (USA)", PNG, ".png")
    seen = count_covers_listings(world.dev)
    assert covers.push_missing(world.dev, world.profile, art, world.rows())[0] == 2
    assert seen == []


def test_a_cancelled_sync_leaves_no_half_cover_in_es_des_folder(run):
    run.give_cover()
    run.want()
    real = run.w.dev.push

    def push(local, remote, progress=None):
        if remote.endswith(".png.part"):
            (run.w.dev.root / remote.lstrip("/")).write_bytes(b"half")
            run.runner.stop("cancel")
            raise DeviceGone("unplugged")
        return real(local, remote, progress)
    run.w.dev.push = push
    assert run.sync() == "cancelled"
    assert not list(run.on_device("x").parent.glob("*.part"))


def test_what_is_left_of_an_interrupted_sync_names_the_covers_still_to_copy(run):
    from jewelcase.core.sync import remaining
    run.give_cover()
    run.want()
    run.job = run.runner.start(run.plan())
    steps = run.w.store.job_steps(run.job)
    for s in steps:
        if s.kind != "cover":
            run.w.store.set_step(run.job, s.idx, "done", "")
    assert remaining(run.w.store.job_steps(run.job))["cover"] == [f"ps2/{NEW}"]
