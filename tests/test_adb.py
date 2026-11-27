import shlex
import subprocess
from pathlib import Path

import pytest

from jewelcase.core import adb
from jewelcase.core.device import DeviceError, DeviceGone

DEVICES_L = """List of devices attached
RG477V00000001         device usb:1-2 product:vext_k6897v1_64 model:RG_477V device:k6897v1_64 transport_id:3
adb-RG477V00000001-aBcDeF._adb-tls-connect._tcp device product:vext_k6897v1_64 model:RG_477V device:k6897v1_64 transport_id:1

"""

BATTERY = """Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  Dock powered: false
  Max charging current: 500000
  status: 2
  level: 81
  scale: 100
"""

PKG_DUMP = """Packages:
  Package [org.ppsspp.ppsspp] (a1b2c3):
    appId=10123
    flags=0x0
    versionCode=120040000 minSdk=21 targetSdk=36
    versionName=v1.20.4
    flags=[ SYSTEM HAS_CODE ALLOW_CLEAR_USER_DATA UPDATED_SYSTEM_APP ALLOW_BACKUP ]
    User 0: ceDataInode=2807 installed=true hidden=false suspended=false stopped=true notLaunched=false enabled=0 instant=false virtual=false

Hidden system packages:
  Package [org.ppsspp.ppsspp] (d4e5f6):
    versionName=1.17.1
"""


class FakeRun:
    """Stands in for subprocess.run: records argv, replies from a queue."""

    def __init__(self, *replies):
        self.calls, self.replies = [], list(replies)

    def __call__(self, argv, **kw):
        if argv[3] == "get-state":          # asked after a failure: yes, the device is still there
            return subprocess.CompletedProcess(argv, 0, b"device\n", b"")
        self.calls.append((argv, kw))
        out, err, rc = self.replies.pop(0) if self.replies else (b"", b"", 0)
        return subprocess.CompletedProcess(argv, rc, out, err)


def device(*replies):
    run = FakeRun(*replies)
    target = adb.AdbTarget("SER1", "device", "RG 477V", "usb")
    return adb.AdbDevice("/bin/adb", target, run=run), run


def test_parse_devices_l():
    targets = adb.parse_targets(DEVICES_L)
    assert [(t.serial, t.state, t.transport) for t in targets] == [
        ("RG477V00000001", "device", "usb"),
        ("adb-RG477V00000001-aBcDeF._adb-tls-connect._tcp", "device", "wifi")]
    assert targets[0].model == "RG 477V"
    picked = adb.pick_targets(targets)
    assert [(t.serial, t.transport) for t in picked] == [("RG477V00000001", "usb")]


def test_pick_keeps_wifi_only_device():
    t = adb.parse_targets("List of devices attached\n192.168.1.9:5555 device model:Odin_2\n")
    assert [(x.serial, x.transport) for x in adb.pick_targets(t)] == [("192.168.1.9:5555", "wifi")]


def test_parse_unauthorized_state():
    t = adb.parse_targets("List of devices attached\nABC123\tunauthorized usb:1-2 transport_id:4\n")
    assert (t[0].serial, t[0].state) == ("ABC123", "unauthorized")
    t = adb.parse_targets("List of devices attached\nABC123\tno permissions (missing udev rules?); see [http://x]\n")
    assert t[0].state == "no permissions"


def test_parse_battery():
    assert adb.parse_battery(BATTERY) == (81, "usb")
    assert adb.parse_battery(BATTERY.replace("AC powered: false", "AC powered: true")) == (81, "ac")
    assert adb.parse_battery(BATTERY.replace("USB powered: true", "USB powered: false")) == (81, "none")


def test_parse_df():
    s = adb.parse_df("Filesystem     1K-blocks     Used Available Use% Mounted on\n"
                     "/dev/fuse      250337152 94965248 155371904  38% /storage/C0DE-CAFE\n")
    assert (s.total, s.used, s.free) == (250337152 * 1024, 94965248 * 1024, 155371904 * 1024)


def test_parse_stat_lines_with_spaces():
    files = adb.parse_stat_lines(
        "4294967296 1795668078 /storage/X/ps2/God of War II (USA).chd\n"
        "garbage line\n"
        "17 1700000000 /storage/X/psx/Baldur's Gate & Co (USA).cue\n")
    assert [(f.size, f.mtime, f.path) for f in files] == [
        (4294967296, 1795668078, "/storage/X/ps2/God of War II (USA).chd"),
        (17, 1700000000, "/storage/X/psx/Baldur's Gate & Co (USA).cue")]


def test_parse_top_activity():
    line = "    topResumedActivity=ActivityRecord{aa03da2 u0 org.ppsspp.ppsspp/.PpssppActivity t63}\n"
    assert adb.parse_top_activity(line) == "org.ppsspp.ppsspp"
    assert adb.parse_top_activity("nothing") == ""


def test_parse_package_dump():
    p = adb.parse_package_dump("org.ppsspp.ppsspp", PKG_DUMP)
    assert (p.version, p.system, p.updated_system, p.enabled) == ("v1.20.4", True, True, True)
    disabled = adb.parse_package_dump("org.ppsspp.ppsspp", PKG_DUMP.replace("enabled=0", "enabled=3"))
    assert disabled.enabled is False
    assert adb.parse_package_dump("x.y", "Unable to find package: x.y\n") is None


def test_adb_quotes_awkward_paths():
    path = "/storage/X/ps2/Baldur's Gate (USA) & More.chd"
    dev, run = device((b"0123456789abcdef0123456789abcdef  " + path.encode() + b"\n", b"", 0))
    assert dev.md5(path) == "0123456789abcdef0123456789abcdef"
    argv = run.calls[0][0]
    assert argv[:4] == ["/bin/adb", "-s", "SER1", "shell"]
    assert argv[4] == "md5sum " + shlex.quote(path)
    assert shlex.split(argv[4]) == ["md5sum", path]


def test_remove_and_rename_quote_paths():
    dev, run = device()
    dev.remove("/c/psx/It's (USA).chd")
    dev.rename("/c/a b.part", "/c/a b")
    assert shlex.split(run.calls[0][0][4]) == ["rm", "-f", "/c/psx/It's (USA).chd"]
    assert shlex.split(run.calls[1][0][4]) == ["mv", "-f", "/c/a b.part", "/c/a b"]


@pytest.mark.parametrize("stderr", [
    b"error: device 'SER1' not found\n", b"error: device offline\n",
    b"adb: device offline\n", b"error: no devices/emulators found\n", b"error: closed\n"])
def test_run_failure_device_offline_raises_gone(stderr):
    dev, _ = device((b"", stderr, 1))
    with pytest.raises(DeviceGone):
        dev.md5("/x")


def test_remote_command_failure_is_device_error():
    dev, _ = device((b"", b"md5sum: /x: No such file or directory\n", 1))
    with pytest.raises(DeviceError):
        dev.md5("/x")


def test_timeout_is_device_gone():
    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)
    dev = adb.AdbDevice("/bin/adb", adb.AdbTarget("SER1", "device", "m", "usb"), run=run)
    with pytest.raises(DeviceGone):
        dev.exists("/x")


def test_list_files_missing_root_is_empty():
    dev, _ = device((b"", b"find: /nope: No such file or directory\n", 1))
    assert dev.list_files("/nope") == []


def test_list_files_passes_maxdepth():
    dev, run = device((b"5 6 /r/psx/a b.chd\n", b"", 0))
    assert dev.list_files("/r", maxdepth=2)[0].path == "/r/psx/a b.chd"
    assert "-maxdepth 2" in run.calls[0][0][4]


def test_write_bytes_verifies_readback():
    import hashlib
    data = b"<gameList/>"
    good = hashlib.md5(data).hexdigest().encode()
    dev, run = device((b"", b"", 0), (good + b"  /g.xml\n", b"", 0))
    dev.write_bytes("/g.xml", data)
    assert run.calls[0][1]["input"] == data
    dev, _ = device((b"", b"", 0), (b"ffffffffffffffffffffffffffffffff  /g.xml\n", b"", 0))
    with pytest.raises(DeviceError):
        dev.write_bytes("/g.xml", data)


def test_install_failure_message():
    dev, _ = device((b"Performing Streamed Install\n",
                     b"adb: failed to install x.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]\n", 1))
    with pytest.raises(DeviceError, match="INSTALL_FAILED_UPDATE_INCOMPATIBLE"):
        dev.install(Path("x.apk"))


def test_find_adb_prefers_configured(tmp_path, monkeypatch):
    exe = tmp_path / "adb"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert adb.find_adb(str(exe)) == str(exe)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert adb.find_adb(None) == str(exe)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert adb.find_adb(None, data_dir=tmp_path / "none") is None
    (tmp_path / "data" / "platform-tools").mkdir(parents=True)
    bundled = tmp_path / "data" / "platform-tools" / "adb"
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    assert adb.find_adb(None, data_dir=tmp_path / "data") == str(bundled)


ASLEEP = """      mLastPausedActivity: ActivityRecord{36e3f1d u0 com.android.launcher3/.uioverrides.QuickstepLauncher t35}
      isSleeping=true
  ResumedActivity: ActivityRecord{aa03da2 u0 org.ppsspp.ppsspp/.PpssppActivity t63}
  mFocusedApp=ActivityRecord{aa03da2 u0 org.ppsspp.ppsspp/.PpssppActivity t63}
"""


def test_foreground_app_is_known_while_the_device_sleeps():
    """Asleep, Android reports no topResumedActivity; a game suspended in an emulator must still count."""
    assert adb.parse_top_activity(ASLEEP) == "org.ppsspp.ppsspp"
    awake = ("    topResumedActivity=ActivityRecord{1 u0 org.es_de.frontend/.MainActivity t9}\n"
             "  ResumedActivity: ActivityRecord{2 u0 com.other.app/.Main t3}\n")
    assert adb.parse_top_activity(awake) == "org.es_de.frontend"
    assert adb.parse_top_activity("  mLastPausedActivity: ActivityRecord{3 u0 a.b.c/.X t1}\n") == ""
    dev, run = device((ASLEEP.encode(), b"", 0))
    assert dev.foreground_package() == "org.ppsspp.ppsspp"
    assert "ResumedActivity" in run.calls[0][0][4] and "mFocusedApp" in run.calls[0][0][4]


GRANTS = """ACTIVITY MANAGER URI PERMISSIONS (dumpsys activity permissions)
  Granted Uri Permissions:
  * UID 10076 holds:
    UriPermission{ce0a2be content://com.android.externalstorage.documents/tree/C0DE-CAFE%3Abios-extras%2Farmsx2-bios [user 0] [prefix]}
      targetUserId=0 sourcePkg=com.android.externalstorage targetPkg=com.armsx2
      mode=0x1 owned=0x0 global=0x0 persistable=0x1 persisted=0x1 persistedCreate=1794241461760
    UriPermission{a2f5f1f content://com.android.externalstorage.documents/tree/C0DE-CAFE%3Aps2 [user 0] [prefix]}
      targetUserId=0 sourcePkg=com.android.externalstorage targetPkg=com.armsx2
      mode=0x3 owned=0x0 global=0x0 persistable=0x3 persisted=0x3 persistedCreate=1794241480356
  * UID 10166 holds:
    UriPermission{621226c content://com.android.externalstorage.documents/tree/primary%3AROMs [user 0] [prefix]}
      targetUserId=0 sourcePkg=com.android.externalstorage targetPkg=org.es_de.frontend
      mode=0x3 owned=0x0 global=0x0 persistable=0x3 persisted=0x3 persistedCreate=1793664288236
"""


def test_the_folders_each_app_was_granted_are_read_from_the_permissions_dump():
    assert adb.parse_uri_grants(GRANTS) == {
        "com.armsx2": ("/storage/C0DE-CAFE/bios-extras/armsx2-bios", "/storage/C0DE-CAFE/ps2"),
        "org.es_de.frontend": ("/storage/emulated/0/ROMs",)}


def test_a_grant_that_was_not_kept_and_other_providers_are_not_folders_the_app_holds():
    text = GRANTS.replace("persisted=0x3 persistedCreate=1794241480356", "persisted=0x0 persistedCreate=0") \
        + "    UriPermission{1 content://media/external/images/1 [user 0]}\n      targetPkg=com.other\n"
    assert adb.parse_uri_grants(text)["com.armsx2"] == ("/storage/C0DE-CAFE/bios-extras/armsx2-bios",)
    assert "com.other" not in adb.parse_uri_grants(text)


def test_a_dump_that_cannot_be_read_gives_no_grants():
    assert adb.parse_uri_grants("") == {} and adb.parse_uri_grants("Permission Denial: nope") == {}
