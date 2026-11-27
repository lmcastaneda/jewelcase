"""Game covers and system images, copied from ES-DE on the handheld into Jewelcase's cache.

ES-DE already has the pictures: covers its scraper downloaded, and one image per system in its theme.
Jewelcase copies them to the PC to show them; it fetches nothing from the network and changes nothing on
the handheld. The cache can be deleted at any time: it fills again from the handheld.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from . import esde, systems
from .catalog import Profile
from .device import Device, DeviceError, DeviceGone, RemoteFile

IMAGES = (".png", ".jpg", ".jpeg", ".webp")
# where themes keep one image per system, named after ES-DE's name for the system
THEME_FOLDERS = ("_inc/systems/images", "system/systemart")
BATCH = 12                                      # covers between two "something changed" calls


def _home(profile: Profile) -> str | None:
    """ES-DE's folder on the handheld: the one that holds its game lists."""
    if not profile.gamelists_dir:
        return None
    return profile.gamelists_dir.rstrip("/").rsplit("/", 1)[0]


def media_dir(profile: Profile) -> str | None:
    home = _home(profile)
    return f"{home}/downloaded_media" if home else None


def _image(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in IMAGES and not name.startswith(".")


class Art:
    def __init__(self, root: Path):
        self.root = Path(root)
        # covers: "system/file" -> [size, mtime] on the handheld; systems: system -> {file, source};
        # gone: covers the last listing did not have (dropped when the next one does not have them either)
        self._index: dict = {"covers": {}, "systems": {}, "gone": []}
        try:
            loaded = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
            covers, known, gone = loaded.get("covers"), loaded.get("systems"), loaded.get("gone") or []
            if (isinstance(covers, dict) and isinstance(known, dict) and isinstance(gone, list)
                    and all(isinstance(k, str) and "/" in k and isinstance(v, list) for k, v in covers.items())
                    and all(isinstance(v, dict) and isinstance(v.get("file"), str) and "source" in v
                            for v in known.values())):
                self._index = {"covers": covers, "systems": known, "gone": [k for k in gone if k in covers]}
        except (OSError, ValueError, AttributeError):
            pass                                # unreadable or damaged: start over, the handheld has it all
        self._by_game: dict[tuple[str, str], str] | None = None
        self._downloaded: dict[tuple[str, str], str] | None = None      # covers downloaded, see core/covers.py
        self._misses: dict[str, float] = {}                             # "system/stem" -> when nothing was found
        try:
            loaded = json.loads((self.root / "misses.json").read_text(encoding="utf-8"))
            self._misses = {k: float(v) for k, v in loaded.items() if isinstance(k, str)}
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        self._lock = threading.Lock()           # the window reads while a worker fetches

    # ------------------------------------------------------------ reading
    def cover(self, system: str, stem: str) -> Path | None:
        """The cover of a game: ES-DE's when it has one, else a downloaded one, else None."""
        return self.esde_cover(system, stem) or self.downloaded_cover(system, stem)

    def esde_cover(self, system: str, stem: str) -> Path | None:
        """The cover ES-DE has for a game, or None."""
        with self._lock:
            if self._by_game is None:
                self._by_game = {}
                for key in sorted(self._index["covers"], reverse=True):
                    folder, _, name = key.partition("/")
                    self._by_game[(folder, PurePosixPath(name).stem)] = name
            name = self._by_game.get((system, stem))
        path = self.root / "covers" / system / name if name else None
        return path if path and path.is_file() else None

    def downloaded_cover(self, system: str, stem: str) -> Path | None:
        """The cover downloaded for a game, or None. Kept apart from the ones copied from ES-DE."""
        with self._lock:
            if self._downloaded is None:
                self._downloaded = {}
                folder = self.root / "downloaded"
                try:
                    for path in sorted(folder.glob("*/*")):
                        if path.suffix.lower() in IMAGES:      # a game called ".hack ..." is written, so it is read
                            self._downloaded[(path.parent.name, path.stem)] = path.name
                except OSError:
                    pass
            name = self._downloaded.get((system, stem))
        path = self.root / "downloaded" / system / name if name else None
        return path if path and path.is_file() else None

    def store_download(self, system: str, stem: str, data: bytes, ext: str) -> Path | None:
        """Keep a downloaded cover, written under a temporary name first. None when it cannot be written."""
        local = self.root / "downloaded" / system / (stem + ext)
        part = local.with_name(local.name + ".part")
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(data)
            part.replace(local)
        except OSError:
            return None
        finally:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
        with self._lock:
            if self._downloaded is not None:
                self._downloaded[(system, stem)] = local.name
        return local

    def missed_since(self, system: str, stem: str) -> float | None:
        """When a search for this game's cover last found nothing, or None."""
        with self._lock:
            return self._misses.get(f"{system}/{stem}")

    def note_miss(self, system: str, stem: str, when: float) -> None:
        with self._lock:
            self._misses[f"{system}/{stem}"] = when

    def save_misses(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._lock:
                text = json.dumps(self._misses)
            part = self.root / "misses.json.part"
            part.write_text(text, encoding="utf-8")
            part.replace(self.root / "misses.json")
        except OSError:
            pass

    def system(self, system: str) -> Path | None:
        """The image of a system, or None."""
        with self._lock:
            entry = self._index["systems"].get(system)
        path = self.root / "systems" / entry["file"] if entry else None
        return path if path and path.is_file() else None

    # ------------------------------------------------------------ fetching
    def refresh(self, dev: Device, profile: Profile, folders: Iterable[str], esde_cache: Path | None = None,
                changed: Callable[[], None] | None = None) -> int:
        """Bring the cache in line with the handheld for these systems. Returns how many pictures came or
        went. What cannot be read is left as it was."""
        self._count = 0
        folders = list(folders)
        try:
            self._covers(dev, profile, folders, changed)
            self._systems(dev, profile, folders, esde_cache)
        except (DeviceGone, DeviceError, OSError):
            pass
        finally:
            self._save()
        return self._count

    def _save(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            part = self.root / "index.json.part"
            with self._lock:
                text = json.dumps(self._index)
            part.write_text(text, encoding="utf-8")
            part.replace(self.root / "index.json")
        except OSError:
            pass

    @staticmethod
    def _fetch(dev: Device, remote: str, local: Path) -> bool:
        """Copy one picture over under a temporary name first. False when the handheld refuses the file."""
        part = local.with_name(local.name + ".part")
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            dev.pull(remote, part)
            part.replace(local)
            return True
        except DeviceGone:
            raise
        except (DeviceError, OSError):          # the handheld refuses it, or its name cannot be written here
            return False
        finally:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass

    def _covers(self, dev: Device, profile: Profile, folders: list[str], changed) -> None:
        media = media_dir(profile)
        if not media:
            return
        prefix = media.rstrip("/") + "/"
        remote: dict[str, dict[str, RemoteFile]] = {}           # ES-DE system -> file name -> file
        listing = dev.list_files(media, maxdepth=3)             # <system>/covers/<picture>
        if not listing:
            return                                              # nothing there, or it could not be read
        for f in listing:
            parts = f.path[len(prefix):].split("/") if f.path.startswith(prefix) else []
            if len(parts) == 3 and parts[1] == "covers" and _image(parts[2]):
                remote.setdefault(parts[0], {})[parts[2]] = f
        index = self._index["covers"]
        for folder in folders:
            there = remote.get(systems.get(folder).esde, {})
            for name, f in there.items():
                key = f"{folder}/{name}"
                local = self.root / "covers" / folder / name
                if index.get(key) == [f.size, f.mtime] and local.is_file():
                    continue
                if self._fetch(dev, f.path, local):
                    with self._lock:
                        index[key] = [f.size, f.mtime]
                        self._by_game = None
                    self._count += 1
                    if self._count % BATCH == 0:
                        self._save()            # a long first fetch that is cut short keeps what it has
                        if changed:
                            changed()
            # A listing can come back short. A cover is only dropped once two listings in a row lack it.
            absent = [k for k in list(index) if k.startswith(folder + "/") and k.partition("/")[2] not in there]
            before = set(self._index["gone"])
            for key in absent:
                if key not in before:
                    continue
                try:
                    (self.root / "covers" / folder / key.partition("/")[2]).unlink(missing_ok=True)
                except OSError:
                    pass
                with self._lock:
                    del index[key]
                    self._by_game = None
                self._count += 1
            with self._lock:
                self._index["gone"] = ([k for k in self._index["gone"] if not k.startswith(folder + "/")]
                                       + [k for k in absent if k in index])

    def _theme_images(self, dev: Device, profile: Profile) -> dict[str, RemoteFile]:
        """ES-DE's name for a system -> its image in the theme ES-DE is set to."""
        home = _home(profile)
        if not home:
            return {}
        settings = f"{home}/settings/es_settings.xml"
        text = dev.read_bytes(settings).decode("utf-8", "replace") if dev.exists(settings) else ""
        found = re.search(r'<string name="Theme(?:Set)?" value="([^"]+)"', text)      # "ThemeSet" before ES-DE 3
        theme = found.group(1) if found else ""
        if not theme or "/" in theme or theme.startswith("."):
            return {}
        for root in (f"/sdcard/Android/data/{esde.PACKAGE}/files/themes", f"{home}/themes"):
            for folder in THEME_FOLDERS:
                images = {PurePosixPath(f.path).stem: f
                          for f in dev.list_files(f"{root}/{theme}/{folder}", maxdepth=1)
                          if _image(PurePosixPath(f.path).name)}
                if images:
                    return images
        return {}

    def _systems(self, dev: Device, profile: Profile, folders: list[str], esde_cache: Path | None) -> None:
        themed = self._theme_images(dev, profile)
        bundled: dict[str, Path] | None = None
        index = self._index["systems"]
        for folder in folders:
            name = systems.get(folder).esde
            entry = index.get(folder)
            have = entry is not None and (self.root / "systems" / entry["file"]).is_file()
            if name in themed:
                f = themed[name]
                source = ["theme", f.path, f.size, f.mtime]
                local = self.root / "systems" / (folder + PurePosixPath(f.path).suffix.lower())
                if have and entry["source"] == source:
                    continue
                if not self._fetch(dev, f.path, local):
                    continue
            else:
                if bundled is None:                 # ES-DE's app file is only copied over when it is needed
                    bundled = esde.bundled_art(dev, esde_cache) if esde_cache else {}
                if name not in bundled:
                    continue
                st = bundled[name].stat()
                source = ["bundled", str(bundled[name]), st.st_size, st.st_mtime_ns]
                local = self.root / "systems" / (folder + bundled[name].suffix.lower())
                if have and entry["source"] == source:
                    continue
                local.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundled[name], local)
            if entry and entry["file"] != local.name:
                (self.root / "systems" / entry["file"]).unlink(missing_ok=True)
            with self._lock:
                index[folder] = {"file": local.name, "source": source}
            self._count += 1
