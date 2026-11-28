import os
from pathlib import Path

from jewelcase.core import integrate, net


def test_menu_entry_points_at_the_appimage(tmp_path):
    app = tmp_path / "Apps" / "Jewelcase-x86_64.AppImage"
    app.parent.mkdir()
    app.write_bytes(b"x")
    home = tmp_path / "home"
    assert integrate.menu_entry_installed(home) is False
    desktop = integrate.install_menu_entry(app, home)
    assert desktop == home / ".local/share/applications/jewelcase.desktop"
    text = desktop.read_text()
    assert f'Exec="{app}"' in text and "Name=Jewelcase" in text and "Icon=jewelcase" in text
    assert (home / ".local/share/icons/hicolor/scalable/apps/jewelcase.svg").read_text().startswith("<svg")
    assert integrate.menu_entry_installed(home) is True
    integrate.remove_menu_entry(home)
    assert integrate.menu_entry_installed(home) is False


def test_the_icon_cache_refreshed_is_the_one_in_the_home_folder_given_not_the_users(monkeypatch, tmp_path):
    user, given = tmp_path / "user", tmp_path / "given"
    for home in (user, given):
        hicolor = home / ".local/share/icons/hicolor"
        hicolor.mkdir(parents=True)
        (hicolor / "icon-theme.cache").write_bytes(b"cache")
        os.utime(hicolor, (1_000_000_000, 1_000_000_000))
    calls = tmp_path / "calls.txt"
    tool = tmp_path / "bin" / "gtk-update-icon-cache"
    tool.parent.mkdir()
    tool.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent))
    monkeypatch.setenv("HOME", str(user))
    app = tmp_path / "Jewelcase.AppImage"
    app.write_bytes(b"x")
    integrate.install_menu_entry(app, given)
    assert calls.read_text() == f"-f -t {given / '.local/share/icons/hicolor'}\n"
    assert (user / ".local/share/icons/hicolor").stat().st_mtime == 1_000_000_000


def test_running_as_appimage(monkeypatch, tmp_path):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert integrate.appimage_path() is None
    app = tmp_path / "C.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    assert integrate.appimage_path() == app


def test_request_is_anonymous():
    req = net.request("https://example.invalid/x")
    assert req.get_header("User-agent") == "jewelcase"
    assert {k.lower() for k in req.headers} <= {"user-agent", "accept"}


def test_tls_context_verifies():
    import ssl
    ctx = net.context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def _entry(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_desktop_entry_follows_the_formula(tmp_path):
    """HIG: "Name is a(n) GenericName that helps you Comment."""
    from pathlib import Path
    packaged = _entry((Path(__file__).parent.parent / "packaging" / "jewelcase.desktop").read_text())
    app = tmp_path / "Jewelcase.AppImage"
    app.write_bytes(b"x")
    generated = _entry(integrate.install_menu_entry(app, tmp_path / "home").read_text())
    for e in (packaged, generated):
        assert e["Name"] == "Jewelcase"                                   # no descriptive words
        assert e["GenericName"] == "Handheld Manager"                  # title case, no "app"
        assert e["Comment"] == "Sync games and manage emulators on an Android handheld"
        assert e["Comment"][0].isupper() and not e["Comment"].endswith(".")
        assert e["Categories"].endswith(";") and e["Keywords"].endswith(";")
        assert "Emulator" in e["Keywords"].split(";")


def test_icon_has_no_square_base():
    svg = integrate.ICON.read_text()
    assert svg.startswith("<svg") and 'rx="52"' not in svg            # the old rounded-square base is gone


def test_inside_a_flatpak_there_is_no_applications_menu_entry_to_add(monkeypatch, tmp_path):
    app = tmp_path / "C.AppImage"
    app.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(app))
    monkeypatch.setenv("FLATPAK_ID", "dev.luismcastaneda.Jewelcase")
    assert integrate.in_flatpak() is True
    assert integrate.appimage_path() is None


def test_outside_a_flatpak_the_environment_says_so(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(integrate, "FLATPAK_INFO", Path("/nonexistent/.flatpak-info"))
    assert integrate.in_flatpak() is False
    assert integrate.desktop_name() == "jewelcase"


def test_the_flatpak_info_file_also_marks_a_sandbox(monkeypatch, tmp_path):
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\nname=dev.luismcastaneda.Jewelcase\n")
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(integrate, "FLATPAK_INFO", info)
    assert integrate.in_flatpak() is True


def test_in_a_flatpak_the_desktop_file_is_named_after_the_app_id(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "dev.luismcastaneda.Jewelcase")
    assert integrate.desktop_name() == "dev.luismcastaneda.Jewelcase"


def test_a_second_flatpak_launch_finds_the_first_through_the_shared_runtime_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("FLATPAK_ID", "dev.luismcastaneda.Jewelcase")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    name = integrate.instance_name("jewelcase-1000")
    assert name == str(tmp_path / "app/dev.luismcastaneda.Jewelcase/jewelcase-1000")
    monkeypatch.delenv("FLATPAK_ID")
    assert integrate.instance_name("jewelcase-1000") == "jewelcase-1000"
