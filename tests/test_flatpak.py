"""The Flatpak packaging files: checked for shape without flatpak installed."""
import json
import subprocess
import xml.etree.ElementTree as ET
from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLATPAK = ROOT / "packaging/flatpak"
APP_ID = "local.jewelcase.Jewelcase"


def manifest() -> dict:
    return json.loads((FLATPAK / f"{APP_ID}.json").read_text())


def test_the_manifest_names_the_app_runtime_and_command():
    m = manifest()
    assert m["app-id"] == APP_ID
    assert (m["runtime"], m["runtime-version"], m["sdk"]) == ("org.freedesktop.Platform", "26.08", "org.freedesktop.Sdk")
    assert m["command"] == "jewelcase"


def test_the_manifest_asks_for_exactly_the_permissions_the_app_needs():
    args = set(manifest()["finish-args"])
    assert args == {
        "--share=ipc", "--socket=wayland", "--socket=fallback-x11", "--device=all", "--share=network",
        "--filesystem=home", "--filesystem=/mnt", "--filesystem=/media", "--filesystem=/run/media",
        "--talk-name=org.freedesktop.Notifications",
    }


def test_the_manifest_installs_the_appimage_tree_without_stripping_it():
    module = manifest()["modules"][0]
    assert module["build-options"]["strip"] is False
    sources = module["sources"]
    assert any(s.get("path") == "../../dist/Jewelcase-x86_64.AppImage" for s in sources)
    commands = " ".join(module["build-commands"])
    assert "--appimage-extract" in commands and "/app/bin/jewelcase" in commands
    for s in sources:
        if "path" in s:
            assert (FLATPAK / s["path"]).exists() or s["path"].startswith("../../dist/"), s["path"]


def test_the_desktop_file_and_metainfo_agree_on_the_id():
    desktop = ConfigParser(interpolation=None)
    desktop.optionxform = str
    desktop.read(FLATPAK / f"{APP_ID}.desktop")
    entry = desktop["Desktop Entry"]
    assert entry["Exec"] == "jewelcase" and entry["Icon"] == APP_ID
    meta = ET.parse(FLATPAK / f"{APP_ID}.metainfo.xml").getroot()
    assert meta.findtext("id") == APP_ID
    assert meta.findtext("launchable") == f"{APP_ID}.desktop"
    assert meta.find("provides/binary").text == entry["Exec"]
    assert meta.findtext("name") == entry["Name"]


def test_the_icon_is_the_apps_own_svg_installed_under_the_app_id():
    icon = [s for s in manifest()["modules"][0]["sources"] if s.get("dest-filename") == f"{APP_ID}.svg"]
    assert icon and (FLATPAK / icon[0]["path"]).resolve() == ROOT / "src/jewelcase/data/jewelcase.svg"


def test_the_build_script_is_valid_shell_and_names_the_same_id():
    script = ROOT / "packaging/build-flatpak.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    assert f"APP_ID={APP_ID}" in script.read_text()


def test_the_window_class_in_the_desktop_file_is_the_id_the_app_sets_inside_the_flatpak(monkeypatch):
    from jewelcase.core import integrate
    monkeypatch.setenv("FLATPAK_ID", APP_ID)
    desktop = ConfigParser(interpolation=None)
    desktop.optionxform = str
    desktop.read(FLATPAK / f"{APP_ID}.desktop")
    assert desktop["Desktop Entry"]["StartupWMClass"] == integrate.desktop_name()
