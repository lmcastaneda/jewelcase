"""Adding the packaged app to the desktop's applications menu."""
from __future__ import annotations

import os
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


def _paths(home: Path) -> tuple[Path, Path]:
    share = Path(home) / ".local/share"
    return share / "applications/jewelcase.desktop", share / "icons/hicolor/scalable/apps/jewelcase.svg"


def menu_entry_installed(home: Path | None = None) -> bool:
    return _paths(home or Path.home())[0].exists()


def install_menu_entry(exe: Path, home: Path | None = None) -> Path:
    desktop, icon = _paths(home or Path.home())
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_text(ICON.read_text(encoding="utf-8"), encoding="utf-8")
    desktop.write_text(_DESKTOP.format(exe=exe), encoding="utf-8")
    return desktop


def remove_menu_entry(home: Path | None = None) -> None:
    for p in _paths(home or Path.home()):
        p.unlink(missing_ok=True)
