import hashlib

import pytest

from jewelcase.core.device import DeviceError, DeviceGone, PackageInfo
from jewelcase.core.fakedevice import FakeDevice


@pytest.fixture
def dev(tmp_path):
    return FakeDevice(tmp_path / "dev")


def test_fake_roundtrip(dev, tmp_path):
    src = tmp_path / "Tom's Game & Co (USA).chd"
    src.write_bytes(b"x" * 5000)
    remote = "/card/psx/Tom's Game & Co (USA).chd"
    seen = []
    dev.mkdir("/card/psx")
    dev.push(src, remote + ".part", progress=seen.append)
    assert seen and seen[-1] == 5000
    dev.rename(remote + ".part", remote)
    assert dev.exists(remote) and not dev.exists(remote + ".part")
    assert dev.md5(remote) == hashlib.md5(b"x" * 5000).hexdigest()
    files = dev.list_files("/card")
    assert [(f.path, f.size) for f in files] == [(remote, 5000)]
    back = tmp_path / "back.chd"
    assert dev.pull(remote, back) == dev.md5(remote)
    assert back.read_bytes() == src.read_bytes()
    dev.remove(remote)
    assert not dev.exists(remote)
    dev.remove(remote)  # removing a missing file is not an error


def test_fake_read_write_bytes_and_dirs(dev):
    dev.write_bytes("/sdcard/ES-DE/gamelists/psx/gamelist.xml", b"<gameList/>")
    assert dev.read_bytes("/sdcard/ES-DE/gamelists/psx/gamelist.xml") == b"<gameList/>"
    assert dev.list_dirs("/sdcard/ES-DE/gamelists") == ["psx"]
    assert dev.list_dirs("/nope") == []
    with pytest.raises(DeviceError):
        dev.read_bytes("/nope/file")


def test_fake_maxdepth(dev):
    dev.write_bytes("/r/psx/a.chd", b"1")
    dev.write_bytes("/r/psx/images/a.png", b"1")
    assert [f.path for f in dev.list_files("/r", maxdepth=2)] == ["/r/psx/a.chd"]
    assert len(dev.list_files("/r")) == 2


def test_fake_storage_tracks_usage(dev):
    dev.storage_total = 10_000
    dev.write_bytes("/card/a", b"x" * 4000)
    s = dev.storage("/card")
    assert (s.total, s.used, s.free) == (10_000, 4000, 6000)


def test_fake_offline_raises(dev):
    dev.online = False
    for call in (lambda: dev.info(), lambda: dev.exists("/x"), lambda: dev.list_files("/"),
                 lambda: dev.md5("/x"), lambda: dev.remove("/x")):
        with pytest.raises(DeviceGone):
            call()


def test_fake_push_fail_leaves_partial(dev, tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(b"y" * 10_000)
    dev.fail_after_bytes = 3000
    with pytest.raises(DeviceGone):
        dev.push(src, "/card/big.bin.part")
    assert dev.online is False
    dev.online = True
    assert dev.list_files("/card")[0].size == 3000
    assert dev.fail_after_bytes is None  # one-shot


def test_fake_corrupt_push(dev, tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"z" * 100)
    dev.corrupt_pushes = 1
    dev.push(src, "/card/a.bin")
    assert dev.md5("/card/a.bin") != hashlib.md5(b"z" * 100).hexdigest()
    dev.push(src, "/card/a.bin")
    assert dev.md5("/card/a.bin") == hashlib.md5(b"z" * 100).hexdigest()


def test_fake_info_and_foreground(dev):
    dev.battery, dev.power, dev.foreground = 42, "ac", "org.ppsspp.ppsspp"
    i = dev.info()
    assert (i.serial, i.battery, i.power, i.transport) == ("FAKE0001", 42, "ac", "usb")
    assert dev.foreground_package() == "org.ppsspp.ppsspp"
    dev.broadcast("some.ACTION")
    assert dev.broadcasts == ["some.ACTION"]


def test_fake_packages(dev, tmp_path):
    dev.fake_packages = {
        "a.user": PackageInfo("a.user", "1.0", False, False, True),
        "b.system": PackageInfo("b.system", "2.0", True, False, True)}
    assert [p.package for p in dev.packages(["a.user", "zzz", "b.system"])] == ["a.user", "b.system"]
    dev.set_enabled("b.system", False)
    assert dev.packages(["b.system"])[0].enabled is False
    with pytest.raises(DeviceError):
        dev.uninstall("b.system", keep_data=False)
    dev.uninstall("a.user", keep_data=True)
    assert dev.packages(["a.user"]) == []
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"apk")
    dev.install(apk)
    assert dev.installed == [apk]
    dev.install_error = "INSTALL_FAILED_UPDATE_INCOMPATIBLE"
    with pytest.raises(DeviceError, match="INCOMPATIBLE"):
        dev.install(apk)
