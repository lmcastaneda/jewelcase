"""Adding the packaged app to the desktop's applications menu."""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
ICON = DATA / "jewelcase.svg"

_DESKTOP = """[Desktop Entry]
Type=Application
Name=Jewelcase
GenericName=Handheld Manager
Comment=Sync games and manage emulators on an Android handheld
Exec="{exe}"
Icon=jewelcase
Categories=Utility;Game;
Keywords=Emulator;Games;Sync;Android;Handheld;ROM;
Terminal=false
StartupWMClass=jewelcase
"""


FLATPAK_INFO = Path("/.flatpak-info")


def in_flatpak() -> bool:
    """True when running inside a Flatpak sandbox."""
    return bool(os.environ.get("FLATPAK_ID")) or FLATPAK_INFO.exists()


def desktop_name() -> str:
    """The desktop file's name without `.desktop`: the app id in a Flatpak, which the desktop matches on."""
    return os.environ.get("FLATPAK_ID") or "jewelcase"


def instance_name(base: str) -> str:
    """The local socket that tells a second launch a copy is running.

    A Flatpak gets its own private /tmp, so the socket goes in the run folder every launch of the same
    app shares.
    """
    run = os.environ.get("XDG_RUNTIME_DIR")
    flatpak_id = os.environ.get("FLATPAK_ID")
    if flatpak_id and run:
        return str(Path(run) / "app" / flatpak_id / base)
    return base


def appimage_path() -> Path | None:
    """Where the running AppImage lives, or None when running from source or in a Flatpak."""
    if in_flatpak():
        return None
    value = os.environ.get("APPIMAGE")
    return Path(value) if value and Path(value).is_file() else None


def menu_entry_paths(home: Path | None = None) -> tuple[Path, Path]:
    """The applications menu entry and its icon, under the home folder."""
    share = Path(home or Path.home()) / ".local/share"
    return share / "applications/jewelcase.desktop", share / "icons/hicolor/scalable/apps/jewelcase.svg"


def menu_entry_installed(home: Path | None = None) -> bool:
    return menu_entry_paths(home)[0].exists()


def install_menu_entry(exe: Path, home: Path | None = None) -> Path:
    desktop, icon = menu_entry_paths(home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    _write_in_place(icon, ICON.read_text(encoding="utf-8"))
    _write_in_place(desktop, _DESKTOP.format(exe=exe))
    refresh_icon_cache(home)
    return desktop


def _write_in_place(path: Path, text: str) -> None:
    """Write under a temporary name in the same folder, check it, then give it its real name."""
    tmp = path.with_name(path.name + ".jewelcase-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if tmp.read_text(encoding="utf-8") != text:
            raise OSError(f"{tmp} did not read back as it was written")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def refresh_icon_cache(home: Path | None = None) -> None:
    """Make the desktop see the icon. Best effort: it never fails.

    GTK ignores an icon-theme.cache older than its folder, so touching the folder is enough where
    gtk-update-icon-cache is missing. A cache the user did not have is not created.
    """
    hicolor = Path(home or Path.home()) / ".local/share/icons/hicolor"
    with contextlib.suppress(OSError):
        os.utime(hicolor)
    tool = shutil.which("gtk-update-icon-cache")
    if tool is None or not (hicolor / "icon-theme.cache").exists():
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):     # a non-zero exit is ignored too
        subprocess.run([tool, "-f", "-t", str(hicolor)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10, env=_host_env())


def _host_env() -> dict[str, str] | None:
    """The environment to start a host program with; None (unchanged) when running from source.

    The packaged app puts its own library folder on LD_LIBRARY_PATH, and a host program loading those
    libraries can fail to start. PyInstaller keeps the user's value in LD_LIBRARY_PATH_ORIG.
    """
    if not getattr(sys, "frozen", False):
        return None
    env = dict(os.environ)
    orig = env.get("LD_LIBRARY_PATH_ORIG")
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def remove_menu_entry(home: Path | None = None) -> None:
    for p in menu_entry_paths(home):
        p.unlink(missing_ok=True)
