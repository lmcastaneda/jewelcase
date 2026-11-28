import io
import json

import pytest

from jewelcase.core import emulators as emu
from jewelcase.core.device import PackageInfo
from jewelcase.core.fakedevice import FakeDevice

ON_RG477V = ["com.armsx2", "com.dsemu.drastic", "com.explusalpha.GbcEmu", "com.flycast.emulator",
             "com.github.stenzek.duckstation", "com.retroarch.aarch64", "io.github.lime3ds.android",
             "io.recompiled.redream", "org.citra.emu", "org.devmiyax.yabasanshioro2.pro",
             "org.dolphinemu.dolphinemu", "org.dolphinemu.mmjr", "org.es_de.frontend",
             "org.mupen64plusae.v3.fzurita.pro", "org.ppsspp.ppsspp", "org.vita3k.emulator",
             "xyz.aethersx2.android"]


def release(tag, *assets):
    return {"tag_name": tag, "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}"}
                                        for n in assets]}


FEEDS = {
    "ARMSX2/ARMSX2": release("2.9.7", "ARMSX2-2.9.7-a11-armv8.2-sdk30.apk", "ARMSX2-2.9.7-a13-armv8.2-sdk33.apk",
                             "ARMSX2-2.9.7-a15-armv8.2-sdk35.apk", "ARMSX2-2.9.7-legacy-armv8.0-sdk26.apk",
                             "notes.txt"),
    "flyinghead/flycast": release("v2.7", "flycast-2.7.apk", "flycast-win64-2.7.zip"),
}


def fetch_json(url):
    for repo, body in FEEDS.items():
        if url == f"https://api.github.com/repos/{repo}/releases/latest":
            return body
    raise OSError("HTTP 404")


@pytest.fixture
def dev(tmp_path):
    d = FakeDevice(tmp_path / "dev")
    d.fake_packages = {
        "com.armsx2": PackageInfo("com.armsx2", "2.9.5", False, False, True),
        "com.flycast.emulator": PackageInfo("com.flycast.emulator", "2.3", True, False, False),
        "org.ppsspp.ppsspp": PackageInfo("org.ppsspp.ppsspp", "v1.20.4", True, True, True),
        "org.vita3k.emulator": PackageInfo("org.vita3k.emulator", "0.2.0", True, False, True),
    }
    return d


def rows_by_pkg(dev):
    return {r.entry.package: r for r in emu.list_emulators(dev, emu.load_catalog())}


def test_catalog_loads_and_has_device_packages():
    cat = emu.load_catalog()
    assert set(ON_RG477V) <= set(cat)
    with_source = {p for p, e in cat.items() if e.source}
    assert with_source == {"com.armsx2", "com.flycast.emulator", "org.vita3k.emulator",
                           "io.github.lime3ds.android", "org.azahar_emu.azahar", "me.magnum.melonds",
                           "aenu.aps3e", "com.armsx3"}
    assert cat["com.armsx2"].name == "ARMSX2" and cat["com.armsx2"].systems == ("ps2",)
    assert all(e.source.kind == "github" for e in cat.values() if e.source)


def test_user_catalog_extends_and_overrides(tmp_path):
    extra = tmp_path / "emulators.toml"
    extra.write_text('[[emulator]]\npackage = "a.b.c"\nname = "Mine"\nsystems = ["nes"]\n'
                     '[[emulator]]\npackage = "com.armsx2"\nname = "ARMSX2 (mine)"\nsystems = ["ps2"]\n')
    cat = emu.load_catalog(extra)
    assert cat["a.b.c"].name == "Mine" and cat["com.armsx2"].name == "ARMSX2 (mine)"
    assert emu.load_catalog(tmp_path / "missing.toml")["com.armsx2"].name == "ARMSX2"


def test_emulator_packages_excludes_frontend():
    pk = emu.emulator_packages(emu.load_catalog())
    assert "org.ppsspp.ppsspp" in pk and "org.es_de.frontend" not in pk


def test_list_marks_system_and_disabled(dev):
    rows = emu.list_emulators(dev, emu.load_catalog())
    by = {r.entry.package: r for r in rows}
    assert by["com.armsx2"].installed.system is False
    assert by["com.flycast.emulator"].installed.enabled is False
    assert by["org.ppsspp.ppsspp"].installed.updated_system is True
    # installed ones first, then those that could be added from a confirmed source
    installed = [r for r in rows if r.installed]
    assert rows[:len(installed)] == installed and len(installed) == 4
    rest = {r.entry.package for r in rows[len(installed):]}
    assert rest == {"io.github.lime3ds.android", "org.azahar_emu.azahar", "me.magnum.melonds",
                    "aenu.aps3e", "com.armsx3"}


def test_check_updates_parses_release(dev):
    rows = emu.check_updates([rows_by_pkg(dev)["com.armsx2"], rows_by_pkg(dev)["com.flycast.emulator"]],
                             sdk=34, fetch_json=fetch_json)
    armsx2, flycast = rows
    assert armsx2.latest == "2.9.7" and armsx2.apk_url.endswith("ARMSX2-2.9.7-a13-armv8.2-sdk33.apk")
    assert armsx2.update_available is True
    assert flycast.latest == "2.7" and flycast.apk_url.endswith("flycast-2.7.apk")
    newer_phone = emu.check_updates([rows_by_pkg(dev)["com.armsx2"]], sdk=36, fetch_json=fetch_json)[0]
    assert newer_phone.apk_url.endswith("sdk35.apk")
    old_phone = emu.check_updates([rows_by_pkg(dev)["com.armsx2"]], sdk=24, fetch_json=fetch_json)[0]
    assert old_phone.apk_url is None and "no build" in old_phone.error


def test_update_check_failure_is_per_row(dev):
    by = rows_by_pkg(dev)
    rows = emu.check_updates([by["org.vita3k.emulator"], by["com.flycast.emulator"], by["org.ppsspp.ppsspp"]],
                             sdk=34, fetch_json=fetch_json)
    assert rows[0].latest is None and "404" in rows[0].error
    assert rows[1].latest == "2.7" and rows[1].error == ""
    assert rows[2].latest is None and rows[2].error == ""        # no source: nothing to check


def test_is_newer():
    assert emu.is_newer("2.9.5", "2.9.7") and emu.is_newer("v1.20.4", "1.21")
    assert not emu.is_newer("2.9.7", "2.9.7") and not emu.is_newer("2.10", "2.9.7")
    assert not emu.is_newer("0.1-6759", "latest") and not emu.is_newer("", "1.0")
    assert not emu.is_newer("0.2.0-12", "12")          # Vita3K: the release tag is the build number
    assert emu.is_newer("0.2.0-12", "13")


def test_install_signature_mismatch_message(dev, tmp_path):
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"apk")
    dev.install_error = "INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match"
    with pytest.raises(emu.EmulatorError) as e:
        emu.install_from_file(dev, apk)
    assert str(e.value) == emu.SIGNATURE_MESSAGE and "will not uninstall" in emu.SIGNATURE_MESSAGE
    dev.install_error = "INSTALL_FAILED_VERSION_DOWNGRADE"
    with pytest.raises(emu.EmulatorError, match="newer"):
        emu.install_from_file(dev, apk)
    dev.install_error = None
    emu.install_from_file(dev, apk)
    assert dev.installed == [apk]


def test_install_latest_downloads_then_installs(dev, tmp_path):
    row = emu.check_updates([rows_by_pkg(dev)["com.flycast.emulator"]], sdk=34, fetch_json=fetch_json)[0]
    seen = []

    def fetch(url):
        seen.append(url)
        return io.BytesIO(b"apk-bytes")
    emu.install_latest(dev, row, tmp_path / "cache", fetch=fetch)
    assert seen == [row.apk_url]
    assert dev.installed[0].name == "flycast-2.7.apk" and dev.installed[0].read_bytes() == b"apk-bytes"
    with pytest.raises(emu.EmulatorError, match="Check for updates"):
        emu.install_latest(dev, rows_by_pkg(dev)["com.armsx2"], tmp_path / "cache", fetch=fetch)


def test_remove_system_app_refused(dev):
    by = rows_by_pkg(dev)
    with pytest.raises(emu.EmulatorError, match="Disable"):
        emu.remove(dev, by["com.flycast.emulator"], keep_data=False)
    assert "com.flycast.emulator" in dev.fake_packages
    emu.remove(dev, by["com.armsx2"], keep_data=True)
    assert "com.armsx2" not in dev.fake_packages


def test_revert_updated_system_app(dev):
    by = rows_by_pkg(dev)
    emu.revert_to_factory(dev, by["org.ppsspp.ppsspp"])
    assert dev.fake_packages["org.ppsspp.ppsspp"].updated_system is False
    with pytest.raises(emu.EmulatorError):
        emu.revert_to_factory(dev, by["com.armsx2"])


def test_disable_enable(dev):
    by = rows_by_pkg(dev)
    emu.set_enabled(dev, by["org.ppsspp.ppsspp"], False)
    assert dev.fake_packages["org.ppsspp.ppsspp"].enabled is False
    emu.set_enabled(dev, by["org.ppsspp.ppsspp"], True)
    assert dev.fake_packages["org.ppsspp.ppsspp"].enabled is True


def test_requests_carry_only_a_generic_user_agent():
    req = emu.request("https://api.github.com/repos/a/b/releases/latest")
    assert req.get_header("User-agent") == "jewelcase"
    assert set(k.lower() for k in req.headers) <= {"user-agent", "accept"}


def test_aps3e_entry_picks_its_release_apk():
    entry = emu.load_catalog()["aenu.aps3e"]
    assert entry.name == "aPS3e" and entry.systems == ("ps3",)
    assert entry.source.repo == "aenu1/aps3e"
    assets = [{"name": "Source code.zip", "browser_download_url": "https://x/src.zip"},
              {"name": "2.43.apk.sha256", "browser_download_url": "https://x/2.43.apk.sha256"},
              {"name": "2.43.apk", "browser_download_url": "https://x/2.43.apk"}]
    assert emu._choose_asset(entry.source, assets, 34) == ("https://x/2.43.apk", "")


def test_armsx3_entry_picks_the_build_for_the_handhelds_android_level():
    entry = emu.load_catalog()["com.armsx3"]
    assert entry.name == "ARMSX3" and entry.systems == ("ps3",)
    assert entry.source.repo == "ARMSX2/ARMSX3"
    assets = [{"name": n, "browser_download_url": f"https://x/{n}"}
              for n in ("ARMSX3-1.7.3-a11-armv8.2-sdk30.apk", "ARMSX3-1.7.3-a13-armv8.2-sdk33.apk",
                        "ARMSX3-1.7.3-a15-armv8.2-sdk35.apk", "ARMSX3-1.7.3-legacy-armv8.1-sdk30.apk")]
    assert emu._choose_asset(entry.source, assets, 34) == ("https://x/ARMSX3-1.7.3-a13-armv8.2-sdk33.apk", "")
    a11 = ("https://x/ARMSX3-1.7.3-a11-armv8.2-sdk30.apk", "")
    assert emu._choose_asset(entry.source, assets, 30) == a11
    assert emu._choose_asset(entry.source, assets[::-1], 30) == a11      # never the legacy build, in any order
    assert emu._choose_asset(entry.source, assets, 29) == (
        None, "the latest release has no build for this Android version")


def test_an_armsx3_release_with_only_an_iphone_build_offers_nothing_to_install():
    source = emu.load_catalog()["com.armsx3"].source
    assets = [{"name": "ARMSX3-0.0.4.ipa", "browser_download_url": "https://x/ARMSX3-0.0.4.ipa"}]
    assert emu._choose_asset(source, assets, 34) == (None, "the latest release has no Android build")
