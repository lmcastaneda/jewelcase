import hashlib
import os
from pathlib import Path

import pytest

from jewelcase.core.carddevice import CardDevice, card_is_mounted
from jewelcase.core.device import DeviceError, DeviceGone


@pytest.fixture
def card_dir(tmp_path):
    d = tmp_path / "MYCARD"
    d.mkdir()
    return d


@pytest.fixture
def dev(card_dir):
    return CardDevice(card_dir)


def test_a_card_round_trips_a_file_the_way_a_sync_uses_it(dev, tmp_path):
    src = tmp_path / "Tom's Game & Co (USA).chd"
    src.write_bytes(b"x" * 5000)
    remote = "/roms/psx/Tom's Game & Co (USA).chd"
    seen = []
    dev.mkdir("/roms/psx")
    dev.push(src, remote + ".part", progress=seen.append)
    assert seen and seen[-1] == 5000
    dev.rename(remote + ".part", remote)
    assert dev.exists(remote) and not dev.exists(remote + ".part")
    assert dev.md5(remote) == hashlib.md5(b"x" * 5000).hexdigest()
    assert [(f.path, f.size) for f in dev.list_files("/roms")] == [(remote, 5000)]
    assert dev.stat(remote).size == 5000 and dev.stat("/roms/psx") is None
    back = tmp_path / "back.chd"
    assert dev.pull(remote, back) == dev.md5(remote)
    assert back.read_bytes() == src.read_bytes()
    dev.remove(remote)
    assert not dev.exists(remote)
    dev.remove(remote)                      # removing what is not there is not an error


def test_the_card_root_is_the_folder_and_paths_cannot_leave_it(dev, card_dir, tmp_path):
    dev.write_bytes("/a.txt", b"1")
    assert (card_dir / "a.txt").read_bytes() == b"1"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    with pytest.raises(DeviceError):
        dev.read_bytes("/../outside.txt")
    with pytest.raises(DeviceError):
        dev.write_bytes("/../escaped.txt", b"no")
    assert not (tmp_path / "escaped.txt").exists()
    with pytest.raises(DeviceError):
        dev.remove("/../outside.txt")
    assert outside.exists()


def test_read_write_bytes_and_dirs(dev):
    dev.write_bytes("/ES-DE/gamelists/psx/gamelist.xml", b"<gameList/>")
    assert dev.read_bytes("/ES-DE/gamelists/psx/gamelist.xml") == b"<gameList/>"
    assert dev.list_dirs("/ES-DE/gamelists") == ["psx"]
    assert dev.list_dirs("/nope") == []
    with pytest.raises(DeviceError):
        dev.read_bytes("/nope/file")


def test_maxdepth(dev):
    dev.write_bytes("/r/psx/a.chd", b"1")
    dev.write_bytes("/r/psx/images/a.png", b"1")
    assert [f.path for f in dev.list_files("/r", maxdepth=2)] == ["/r/psx/a.chd"]
    assert len(dev.list_files("/r")) == 2


def test_a_write_goes_through_a_temporary_name_and_leaves_none_behind(dev, card_dir):
    dev.write_bytes("/list.xml", b"new")
    assert [p.name for p in card_dir.iterdir()] == ["list.xml"]


def test_pushed_data_is_flushed_to_the_card_before_it_is_trusted(dev, tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    src = tmp_path / "g.bin"
    src.write_bytes(b"y" * 3000)
    dev.push(src, "/g.bin.part")
    assert synced
    synced.clear()
    dev.write_bytes("/h.bin", b"z")
    assert synced


def test_storage_numbers_come_from_the_filesystem(dev, card_dir):
    import shutil
    s = dev.storage("/")
    real = shutil.disk_usage(card_dir)
    assert s.total == real.total and s.free > 0 and s.used == s.total - s.free
    assert dev.storage("/no/such/folder").total == real.total   # a path inside the card that is not there yet


def test_info_names_the_card_and_says_it_has_no_battery(dev):
    i = dev.info()
    assert i.model == "MYCARD" and i.battery == -1 and i.power == "none" and i.transport == "card"
    assert i.serial == dev.serial and i.serial.startswith("card-")


def test_the_same_card_has_the_same_serial_on_every_mount(card_dir, tmp_path, monkeypatch):
    again = tmp_path / "elsewhere" / "MYCARD"
    again.mkdir(parents=True)
    monkeypatch.setattr("jewelcase.core.carddevice.filesystem_uuid", lambda path: None)   # the fallback: path and size
    assert CardDevice(card_dir).serial == CardDevice(card_dir).serial
    assert CardDevice(card_dir).serial != CardDevice(again).serial      # no id: another folder is another device
    other = tmp_path / "OTHER"
    other.mkdir()
    assert CardDevice(other).serial != CardDevice(card_dir).serial


def test_the_filesystem_uuid_is_the_serial_when_it_can_be_found(card_dir, monkeypatch):
    monkeypatch.setattr("jewelcase.core.carddevice.filesystem_uuid", lambda path: "1A2B-3C4D")
    monkeypatch.setattr("jewelcase.core.carddevice.mount_point", lambda path: card_dir)
    assert CardDevice(card_dir).serial == "card-1a2b-3c4d"


def test_a_folder_inside_a_card_is_told_apart_from_the_whole_card(card_dir, monkeypatch):
    monkeypatch.setattr("jewelcase.core.carddevice.filesystem_uuid", lambda path: "1A2B-3C4D")
    monkeypatch.setattr("jewelcase.core.carddevice.mount_point", lambda path: card_dir)
    inner = card_dir / "Games"
    inner.mkdir()
    assert CardDevice(inner).serial.startswith("card-1a2b-3c4d-")
    assert CardDevice(inner).serial != CardDevice(card_dir).serial


def test_nothing_is_written_to_the_card_to_identify_it(card_dir):
    CardDevice(card_dir).info()
    assert list(card_dir.iterdir()) == []


def test_there_are_no_apps_on_a_card(dev):
    assert dev.has_apps is False
    assert dev.foreground_package() == ""
    assert dev.packages(["a.b"]) == [] and dev.installed_packages() == set()
    assert dev.apk_path("a.b") is None
    assert dev.stop_app("a.b") == ""            # nothing to stop: harmless
    dev.broadcast("some.ACTION")                # nobody listens: harmless
    dev.chmod("/x", "666")                      # a card has no owners: harmless
    for call in (lambda: dev.install(__file__), lambda: dev.uninstall("a.b", False),
                 lambda: dev.set_enabled("a.b", True), lambda: dev.start_activity("a.b/.Main")):
        with pytest.raises(DeviceError, match="not available on a card"):
            call()


def test_a_card_that_is_taken_out_is_a_device_that_is_gone(card_dir, tmp_path):
    dev = CardDevice(card_dir)
    dev.write_bytes("/a", b"1")
    card_dir.rename(tmp_path / "moved")         # unmounted: the folder is not there any more
    for call in (lambda: dev.info(), lambda: dev.exists("/a"), lambda: dev.list_files("/"),
                 lambda: dev.md5("/a"), lambda: dev.write_bytes("/b", b"2")):
        with pytest.raises(DeviceGone):
            call()


def test_put_in_place_gives_the_destination_the_contents(dev):
    dev.write_bytes("/s.tmp", b"new")
    dev.write_bytes("/d", b"old")
    dev.put_in_place("/s.tmp", "/d")
    assert dev.read_bytes("/d") == b"new" and not dev.exists("/s.tmp")


def test_pull_of_a_file_that_is_not_there_is_an_error(dev, tmp_path):
    with pytest.raises(DeviceError):
        dev.pull("/nope", tmp_path / "x")


def test_a_card_is_mounted_when_the_folder_exists_and_has_something_in_it(tmp_path):
    missing = tmp_path / "nothing"
    assert not card_is_mounted(missing)
    missing.mkdir()
    assert not card_is_mounted(missing)         # a mount point nothing is mounted on is empty
    (missing / "roms").mkdir()
    assert card_is_mounted(missing)


# ---------------------------------------------------------------- review fixes: what is at the path
def _pretend_mounted(monkeypatch, where, uuid="AAAA-1111", dev_no=77):
    """Make `where` look like a mount point of a filesystem with this id."""
    monkeypatch.setattr("jewelcase.core.carddevice._identity", lambda root: (dev_no, str(where), uuid))


def test_a_mount_folder_left_behind_after_the_card_is_pulled_is_not_the_card(card_dir, tmp_path, monkeypatch):
    _pretend_mounted(monkeypatch, card_dir)
    dev = CardDevice(card_dir)
    dev.write_bytes("/a", b"1")
    for f in card_dir.iterdir():                # unmounted: the empty folder stays, now on the PC's own disk
        f.unlink()
    _pretend_mounted(monkeypatch, tmp_path, uuid="BBBB-2222", dev_no=1)
    src = tmp_path / "g.bin"
    src.write_bytes(b"x")
    for call in (lambda: dev.info(), lambda: dev.mkdir("/roms"), lambda: dev.push(src, "/roms/g.bin"),
                 lambda: dev.write_bytes("/b", b"2"), lambda: dev.list_files("/"), lambda: dev.exists("/a")):
        with pytest.raises(DeviceGone):
            call()
    assert list(card_dir.iterdir()) == []       # nothing was written into the folder


def test_a_different_card_at_the_same_path_is_a_device_that_is_gone(card_dir, monkeypatch):
    _pretend_mounted(monkeypatch, card_dir, uuid="AAAA-1111")
    dev = CardDevice(card_dir)
    dev.write_bytes("/a", b"1")
    _pretend_mounted(monkeypatch, card_dir, uuid="CCCC-3333")      # card B, same mount point
    with pytest.raises(DeviceGone):
        dev.remove("/a")
    assert (card_dir / "a").exists()
    _pretend_mounted(monkeypatch, card_dir, uuid="AAAA-1111", dev_no=99)   # same id, other device number
    with pytest.raises(DeviceGone):
        dev.exists("/a")


# ---------------------------------------------------------------- review fixes: paths cannot leave the card
def test_a_symlinked_folder_on_the_card_is_never_followed_out_of_it(dev, card_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.bin").write_bytes(b"k")
    (card_dir / "ROMs").mkdir()
    (card_dir / "ROMs" / "psx").symlink_to(outside)
    src = tmp_path / "g.bin"
    src.write_bytes(b"x")
    with pytest.raises(DeviceError):
        dev.push(src, "/ROMs/psx/g.bin")
    with pytest.raises(DeviceError):
        dev.write_bytes("/ROMs/psx/h.bin", b"x")
    with pytest.raises(DeviceError):
        dev.remove("/ROMs/psx/keep.bin")
    with pytest.raises(DeviceError):
        dev.rename("/ROMs/psx/keep.bin", "/ROMs/psx/other.bin")
    assert sorted(p.name for p in outside.iterdir()) == ["keep.bin"]
    assert dev.list_dirs("/ROMs") == []                 # the link leads out of the card: not a folder here
    assert dev.list_files("/") == []


def test_a_symlinked_file_that_points_outside_the_card_is_not_a_rom(dev, card_dir, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"s")
    (card_dir / "link.chd").symlink_to(secret)
    dev.write_bytes("/real.chd", b"1")
    assert [f.path for f in dev.list_files("/")] == ["/real.chd"]
    with pytest.raises(DeviceError):
        dev.read_bytes("/link.chd")


def test_a_relative_card_root_cannot_reach_the_parent_folder(card_dir, tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_bytes(b"secret")
    monkeypatch.chdir(card_dir)
    dev = CardDevice(Path("."))
    dev.write_bytes("/a.txt", b"1")
    assert dev.read_bytes("/a.txt") == b"1"
    with pytest.raises(DeviceError):
        dev.read_bytes("/../secret.txt")
    with pytest.raises(DeviceError):
        dev.write_bytes("/../escaped.txt", b"no")
    assert not (tmp_path / "escaped.txt").exists()


def test_a_card_root_with_dots_or_a_symlink_in_it_still_works(card_dir, tmp_path):
    (card_dir / "sub").mkdir()
    via_dots = CardDevice(card_dir / "sub" / "..")
    via_dots.write_bytes("/a.txt", b"1")
    link = tmp_path / "link"
    link.symlink_to(card_dir)
    via_link = CardDevice(link)
    assert via_link.read_bytes("/a.txt") == b"1" and via_link.exists("/a.txt")
    with pytest.raises(DeviceError):
        via_link.read_bytes("/../x")


# ---------------------------------------------------------------- review fixes: the serial
def test_two_folders_with_the_same_name_on_one_filesystem_are_two_devices(tmp_path, monkeypatch):
    monkeypatch.setattr("jewelcase.core.carddevice.filesystem_uuid", lambda path: None)
    a, b = tmp_path / "x" / "roms", tmp_path / "y" / "roms"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert CardDevice(a).serial != CardDevice(b).serial
    assert CardDevice(a).serial == CardDevice(a).serial


# ---------------------------------------------------------------- review fixes: errors
def test_a_listing_that_could_not_be_completed_is_never_a_short_list(dev, card_dir, monkeypatch):
    dev.write_bytes("/roms/psx/a.chd", b"1")
    real_walk = os.walk

    def vanishing_walk(top, onerror=None, **kw):
        (card_dir / "roms" / "psx" / "a.chd").unlink()
        (card_dir / "roms" / "psx").rmdir()
        (card_dir / "roms").rmdir()
        card_dir.rmdir()                        # the card is gone under the walk
        return real_walk(top, onerror=onerror, **kw)
    monkeypatch.setattr("jewelcase.core.carddevice.os.walk", vanishing_walk)
    with pytest.raises(DeviceGone):
        dev.list_files("/roms")


def test_one_unreadable_folder_is_skipped_and_the_rest_listed(dev, card_dir):
    dev.write_bytes("/roms/psx/a.chd", b"1")
    dev.write_bytes("/roms/gba/b.gba", b"1")
    locked = card_dir / "roms" / "psx"
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("permissions are not enforced here")
        assert [f.path for f in dev.list_files("/roms")] == ["/roms/gba/b.gba"]
    finally:
        locked.chmod(0o755)


def test_stat_and_exists_turn_an_io_error_into_a_device_error(dev, monkeypatch):
    dev.write_bytes("/bad", b"1")
    real = os.stat

    def flaky(path, *a, **kw):
        if str(path).endswith("/bad"):
            raise OSError(5, "Input/output error")
        return real(path, *a, **kw)
    monkeypatch.setattr("jewelcase.core.carddevice.os.stat", flaky)
    for call in (lambda: dev.stat("/bad"), lambda: dev.exists("/bad")):
        with pytest.raises(DeviceError) as e:
            call()
        assert not isinstance(e.value, DeviceGone)


def test_an_io_error_on_one_file_fails_that_step_instead_of_waiting_for_the_card(dev, monkeypatch, tmp_path):
    dev.write_bytes("/bad.chd", b"1")
    import builtins
    real_open = builtins.open

    def flaky(file, *a, **kw):
        if str(file).endswith("bad.chd"):
            raise OSError(5, "Input/output error")
        return real_open(file, *a, **kw)
    monkeypatch.setattr("jewelcase.core.carddevice.open", flaky, raising=False)
    with pytest.raises(DeviceError) as e:
        dev.md5("/bad.chd")
    assert not isinstance(e.value, DeviceGone) and "/bad.chd" in str(e.value)


def test_a_missing_library_file_is_reported_as_the_library_not_the_card(dev, tmp_path):
    with pytest.raises(DeviceError) as e:
        dev.push(tmp_path / "gone" / "g.chd", "/roms/g.chd")
    assert not isinstance(e.value, DeviceGone)
    assert "gone/g.chd" in str(e.value) and "/roms/g.chd" not in str(e.value)
    with pytest.raises(DeviceError) as e:
        dev.write_bytes("/f", b"1") or dev.pull("/f", tmp_path / "nodir" / "f")
    assert not isinstance(e.value, DeviceGone) and "nodir" in str(e.value)


def test_an_io_error_reading_the_library_while_pushing_is_not_a_missing_card(dev, tmp_path, monkeypatch):
    src = tmp_path / "g.chd"
    src.write_bytes(b"x")
    import builtins
    real_open = builtins.open

    def flaky(file, *a, **kw):
        if str(file) == str(src):
            raise OSError(5, "Input/output error")
        return real_open(file, *a, **kw)
    monkeypatch.setattr("jewelcase.core.carddevice.open", flaky, raising=False)
    with pytest.raises(DeviceError) as e:
        dev.push(src, "/g.chd")
    assert not isinstance(e.value, DeviceGone) and str(src) in str(e.value)
