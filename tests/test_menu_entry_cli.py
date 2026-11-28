"""`jewelcase install-menu-entry`: the entry, its icon and the icon cache, always in a temporary home."""
import json
import os
import sys

import pytest

from jewelcase import cli
from jewelcase.core import integrate

ONLY_APPIMAGE = "Only the AppImage can add itself to the applications menu."


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "FLATPAK_ID", "APPIMAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(integrate, "FLATPAK_INFO", tmp_path / "no-flatpak-info")
    monkeypatch.setenv("PATH", "/nonexistent")
    return home


def appimage(tmp_path, monkeypatch, folder="Apps"):
    app = tmp_path / folder / "Jewelcase-x86_64.AppImage"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    return app


def cache_tool(tmp_path, monkeypatch, code=0):
    """A stand-in gtk-update-icon-cache on PATH that writes down each call, and its library path in libs.txt."""
    folder = tmp_path / "bin"
    folder.mkdir()
    calls = tmp_path / "calls.txt"
    tool = folder / "gtk-update-icon-cache"
    tool.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n'
                    f'echo "${{LD_LIBRARY_PATH-unset}}" >> "{tmp_path / "libs.txt"}"\nexit {code}\n')
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    return calls


def hicolor(home):
    return home / ".local/share/icons/hicolor"


def icon_cache(home):
    cache = hicolor(home) / "icon-theme.cache"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")
    return cache


def run(capsys, *args):
    code = cli.main([*args, "install-menu-entry"])
    return code, capsys.readouterr().out


def desktop(home):
    return home / ".local/share/applications/jewelcase.desktop"


def icon(home):
    return hicolor(home) / "scalable/apps/jewelcase.svg"


def test_from_the_appimage_the_command_writes_the_entry_and_the_icon_and_names_them(home, tmp_path, monkeypatch,
                                                                                    capsys):
    app = appimage(tmp_path, monkeypatch)
    code, out = run(capsys)
    assert code == 0
    assert f'Exec="{app}"\n' in desktop(home).read_text()
    assert icon(home).read_text().startswith("<svg")
    assert out == f"Jewelcase was added to the applications menu.\n  {desktop(home)}\n  {icon(home)}\n"


def test_an_appimage_path_with_a_space_ends_up_whole_inside_the_quotes_of_exec(home, tmp_path, monkeypatch, capsys):
    app = appimage(tmp_path, monkeypatch, folder="My Apps")
    assert run(capsys)[0] == 0
    assert f'Exec="{app}"\n' in desktop(home).read_text() and " " in str(app)


def test_the_command_creates_nothing_else_under_the_home_folder(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    run(capsys)
    made = {p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()}
    assert made == {".local/share/applications/jewelcase.desktop",
                    ".local/share/icons/hicolor/scalable/apps/jewelcase.svg"}
    assert not (home / ".config/jewelcase").exists() and not (home / ".local/share/jewelcase").exists()


def test_with_an_icon_cache_and_the_tool_on_path_the_tool_is_run_once_on_the_hicolor_folder(home, tmp_path,
                                                                                            monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    icon_cache(home)
    calls = cache_tool(tmp_path, monkeypatch)
    assert run(capsys)[0] == 0
    assert calls.read_text() == f"-f -t {hicolor(home)}\n"


def test_with_an_icon_cache_and_no_tool_the_hicolor_folder_ends_up_newer_than_the_cache(home, tmp_path, monkeypatch,
                                                                                       capsys):
    appimage(tmp_path, monkeypatch)
    cache = icon_cache(home)
    icon(home).parent.mkdir(parents=True)              # an earlier install: writing the icon leaves hicolor alone
    for p in (cache, hicolor(home)):
        os.utime(p, (1_000_000_000, 1_000_000_000))
    assert run(capsys)[0] == 0
    assert desktop(home).exists() and icon(home).exists()
    assert hicolor(home).stat().st_mtime > cache.stat().st_mtime


def test_started_from_the_packaged_app_the_cache_tool_gets_the_hosts_library_path_back(home, tmp_path, monkeypatch,
                                                                                      capsys):
    appimage(tmp_path, monkeypatch)
    icon_cache(home)
    cache_tool(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path / "bundle/_internal"))     # as the packaged app has it
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert run(capsys)[0] == 0
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")                     # the user had one of their own
    assert run(capsys)[0] == 0
    assert (tmp_path / "libs.txt").read_text() == "unset\n/host/lib\n"


def test_run_from_source_the_cache_tool_gets_the_library_path_as_it_is(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    icon_cache(home)
    cache_tool(tmp_path, monkeypatch)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/user/lib")
    assert run(capsys)[0] == 0
    assert (tmp_path / "libs.txt").read_text() == "/user/lib\n"


def test_with_no_icon_cache_the_tool_is_not_run_even_though_it_is_on_path(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    calls = cache_tool(tmp_path, monkeypatch)
    assert run(capsys)[0] == 0
    assert not calls.exists() and not (hicolor(home) / "icon-theme.cache").exists()


def test_a_cache_tool_that_fails_does_not_stop_the_install(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    icon_cache(home)
    calls = cache_tool(tmp_path, monkeypatch, code=1)
    assert run(capsys)[0] == 0
    assert calls.exists() and desktop(home).exists() and icon(home).exists()


def test_outside_an_appimage_the_command_writes_nothing_and_says_why(home, capsys):
    code, out = run(capsys)
    assert code == 2 and out == ONLY_APPIMAGE + "\n"
    assert list(home.iterdir()) == []


def test_in_the_demo_the_command_writes_nothing_runs_nothing_and_names_the_two_paths(home, tmp_path, monkeypatch,
                                                                                    capsys):
    appimage(tmp_path, monkeypatch)
    icon_cache(home)
    calls = cache_tool(tmp_path, monkeypatch)
    for flags in (["--demo"], ["--demo-dir", str(tmp_path / "demo")]):
        code, out = run(capsys, *flags)
        assert code == 0
        assert out == f"Demo: nothing was written.\n  {desktop(home)}\n  {icon(home)}\n"
    assert not calls.exists() and not desktop(home).exists() and not icon(home).exists()
    assert not (tmp_path / "demo").exists()


def test_run_a_second_time_after_the_appimage_moved_the_entry_holds_the_new_path(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch, folder="Old")
    run(capsys)
    moved = appimage(tmp_path, monkeypatch, folder="New")
    assert run(capsys)[0] == 0
    text = desktop(home).read_text()
    assert f'Exec="{moved}"\n' in text and "Old" not in text


def test_a_second_install_puts_new_files_in_place_and_leaves_the_old_ones_as_they_were(home, tmp_path,
                                                                                       monkeypatch, capsys):
    old_app = appimage(tmp_path, monkeypatch, folder="Old")
    run(capsys)
    old_entry, old_icon = tmp_path / "old.desktop", tmp_path / "old.svg"
    os.link(desktop(home), old_entry)                  # a second name for each file the first install wrote
    os.link(icon(home), old_icon)
    appimage(tmp_path, monkeypatch, folder="New")
    assert run(capsys)[0] == 0
    assert f'Exec="{old_app}"\n' in old_entry.read_text()   # renamed into place, never rewritten where it lay
    assert not os.path.samefile(desktop(home), old_entry) and not os.path.samefile(icon(home), old_icon)


def test_json_gives_what_was_written_or_why_not(home, tmp_path, monkeypatch, capsys):
    paths = {"desktop": str(desktop(home)), "icon": str(icon(home))}
    code, out = run(capsys, "--json")
    assert code == 2 and json.loads(out) == {"written": False, "error": ONLY_APPIMAGE}
    appimage(tmp_path, monkeypatch)
    code, out = run(capsys, "--json", "--demo")
    assert code == 0 and json.loads(out) == {"written": False, **paths}
    code, out = run(capsys, "--json")
    assert code == 0 and json.loads(out) == {"written": True, **paths}


@pytest.fixture
def unwritable():
    """Make folders read-only for one test and give them back their mode afterwards, so pytest can clean up."""
    made = []

    def lock(folder):
        folder.mkdir(parents=True, exist_ok=True)
        made.append(folder)
        folder.chmod(0o555)
        return folder

    yield lock
    for folder in made:
        folder.chmod(0o755)


def not_added(folder, reason="Permission denied"):
    return f"Jewelcase could not be added to the applications menu: {folder} could not be written ({reason})."


def test_when_the_applications_folder_cannot_be_written_the_command_says_so_in_one_sentence_and_exits_2(
        home, tmp_path, monkeypatch, capsys, unwritable):
    appimage(tmp_path, monkeypatch)
    folder = unwritable(desktop(home).parent)
    code = cli.main(["install-menu-entry"])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == not_added(folder) + "\n" and err == ""
    assert icon(home).exists() and not desktop(home).exists()     # the icon written first stays


def test_with_json_a_menu_entry_that_cannot_be_written_gives_written_false_and_the_sentence(home, tmp_path,
                                                                                            monkeypatch, capsys,
                                                                                            unwritable):
    appimage(tmp_path, monkeypatch)
    folder = unwritable(desktop(home).parent)
    code, out = run(capsys, "--json")
    assert code == 2 and json.loads(out) == {"written": False, "error": not_added(folder)}


def test_when_the_icon_folder_cannot_be_written_the_sentence_names_that_folder(home, tmp_path, monkeypatch, capsys,
                                                                               unwritable):
    appimage(tmp_path, monkeypatch)
    folder = unwritable(icon(home).parent)
    code, out = run(capsys)
    assert code == 2 and out == not_added(folder) + "\n"
    assert not desktop(home).exists()


def test_a_file_where_the_applications_folder_should_be_is_named_in_the_sentence(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    desktop(home).parent.parent.mkdir(parents=True)
    desktop(home).parent.write_text("not a folder")
    code, out = run(capsys)
    assert code == 2 and out == not_added(desktop(home).parent, "File exists") + "\n"


def test_no_temporary_file_is_left_beside_the_entry_or_the_icon_after_a_failed_install(home, tmp_path, monkeypatch,
                                                                                       capsys):
    appimage(tmp_path, monkeypatch)
    (desktop(home) / "in the way").mkdir(parents=True)     # a folder where the entry goes: the rename fails
    code, out = run(capsys)
    assert code == 2 and out.startswith("Jewelcase could not be added to the applications menu: ")
    assert [p.name for p in desktop(home).parent.iterdir()] == ["jewelcase.desktop"]
    assert [p.name for p in icon(home).parent.iterdir()] == ["jewelcase.svg"]


def test_no_temporary_file_is_left_beside_the_entry_or_the_icon(home, tmp_path, monkeypatch, capsys):
    appimage(tmp_path, monkeypatch)
    run(capsys)
    run(capsys)
    assert [p.name for p in desktop(home).parent.iterdir()] == ["jewelcase.desktop"]
    assert [p.name for p in icon(home).parent.iterdir()] == ["jewelcase.svg"]
