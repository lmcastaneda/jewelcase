"""Settings file and XDG locations."""
from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP = "jewelcase"


@dataclass(frozen=True)
class Paths:
    data: Path
    config: Path
    cache: Path


def paths() -> Paths:
    home = Path(os.environ.get("HOME") or Path.home())

    def base(var: str, default: str) -> Path:
        v = os.environ.get(var)
        return Path(v) if v else home / default

    return Paths(
        data=base("XDG_DATA_HOME", ".local/share") / APP,
        config=base("XDG_CONFIG_HOME", ".config") / APP / "config.toml",
        cache=base("XDG_CACHE_HOME", ".cache") / APP,
    )


@dataclass
class Config:
    library: Path | None = None
    inbox: Path = field(default_factory=lambda: Path.home() / "Downloads")
    adb: str | None = None
    card: Path | None = None        # a mounted SD card or folder to use as the device when no handheld is plugged in
    never_played_seconds: int = 60
    not_played_days: int = 90
    backup_save_states: bool = True
    backup_settings: bool = True
    download_covers: bool = False       # fetch covers for games ES-DE has none for; only when switched on
    convert_on_import: bool = True      # an archive is made into the playable game when it is imported
    extra_systems: dict = field(default_factory=dict)
    user_lists: list = field(default_factory=list)      # the owner's smart lists (core.smartlists.UserList)
    unreadable: bool = field(default=False, compare=False)  # the file could not be read: these are the defaults
    kept: bool = field(default=False, compare=False)        # ... and the unreadable file has been set aside

    UNREADABLE_TEXT = ("The settings file could not be read, so the defaults are in use. "
                       "The unreadable file was kept as config.toml.broken.")


def _text(data: dict, key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _number(data: dict, key: str, default: int) -> int:
    value = data.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _flag(data: dict, key: str, default: bool) -> bool:
    value = data.get(key, default)
    return value if isinstance(value, bool) else default


def load(path: Path) -> Config:
    """The settings. A file that cannot be read gives the defaults with `unreadable` set (the file itself
    is left alone until the next save); a value of the wrong type gives that value's default."""
    cfg = Config()
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        cfg.unreadable = True
        return cfg
    if _text(data, "library"):
        cfg.library = Path(data["library"])
    if _text(data, "inbox"):
        cfg.inbox = Path(data["inbox"])
    cfg.adb = _text(data, "adb")
    if _text(data, "card"):
        cfg.card = Path(data["card"])
    cfg.never_played_seconds = _number(data, "never_played_seconds", cfg.never_played_seconds)
    cfg.not_played_days = _number(data, "not_played_days", cfg.not_played_days)
    cfg.backup_save_states = _flag(data, "backup_save_states", True)
    cfg.backup_settings = _flag(data, "backup_settings", True)
    cfg.download_covers = data.get("download_covers", False) is True
    cfg.convert_on_import = _flag(data, "convert_on_import", True)
    from . import smartlists
    cfg.user_lists = smartlists.parse(data.get("lists"))
    extra = data.get("systems") or {}
    if isinstance(extra, dict):
        from . import systems
        for folder, spec in extra.items():
            if isinstance(spec, dict):
                cfg.extra_systems[folder] = dict(spec)
                systems.register(folder, spec.get("name"), spec.get("esde"),
                                 spec.get("device_exts") or (), spec.get("color"))
    return cfg


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _keep_unreadable(path: Path) -> None:
    """Set the unreadable file aside as config.toml.broken (or .broken-2 ...): an earlier copy is never replaced."""
    if not path.is_file():
        return
    copy, n = path.with_name(path.name + ".broken"), 1
    while copy.exists():
        n += 1
        copy = path.with_name(f"{path.name}.broken-{n}")
    shutil.copy2(path, copy)


def save(cfg: Config, path: Path) -> None:
    if cfg.unreadable and not cfg.kept:
        _keep_unreadable(Path(path))
        cfg.kept = True
    lines = []
    if cfg.library:
        lines.append(f"library = {_q(str(cfg.library))}")
    lines.append(f"inbox = {_q(str(cfg.inbox))}")
    if cfg.adb:
        lines.append(f"adb = {_q(cfg.adb)}")
    if cfg.card:
        lines.append(f"card = {_q(str(cfg.card))}")
    lines.append(f"never_played_seconds = {cfg.never_played_seconds}")
    lines.append(f"not_played_days = {cfg.not_played_days}")
    lines.append(f"backup_save_states = {'true' if cfg.backup_save_states else 'false'}")
    lines.append(f"backup_settings = {'true' if cfg.backup_settings else 'false'}")
    if cfg.download_covers:
        lines.append("download_covers = true")
    lines.append(f"convert_on_import = {'true' if cfg.convert_on_import else 'false'}")
    from . import smartlists
    lines += smartlists.to_toml(cfg.user_lists)
    for folder, spec in cfg.extra_systems.items():
        lines.append(f"\n[systems.{_q(folder)}]")
        for k, v in spec.items():
            if isinstance(v, (list, tuple)):
                lines.append(f"{k} = [{', '.join(_q(str(x)) for x in v)}]")
            else:
                lines.append(f"{k} = {_q(str(v))}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
