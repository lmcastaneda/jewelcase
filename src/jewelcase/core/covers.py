"""Covers for games ES-DE has none for, from the libretro thumbnail repositories on GitHub.

Only used when the owner switched it on or asked for it. Each request names a repository (the system)
and a file name (the game), nothing else. Downloaded covers are kept apart from the ones copied from ES-DE.
"""
from __future__ import annotations

import http.client
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import quote

from pathlib import PurePosixPath

from . import systems
from .art import Art, media_dir, _image
from .catalog import Profile
from .device import Device, DeviceError, md5_file

MAX_BYTES = 5 * 1024 * 1024
RETRY_AFTER = 30 * 86400                        # seconds before a game with no cover is asked for again
BASE = "https://raw.githubusercontent.com/libretro-thumbnails"

REPOS = {
    "nes": "Nintendo_-_Nintendo_Entertainment_System", "snes": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "gb": "Nintendo_-_Game_Boy", "gbc": "Nintendo_-_Game_Boy_Color", "gba": "Nintendo_-_Game_Boy_Advance",
    "n64": "Nintendo_-_Nintendo_64", "nds": "Nintendo_-_Nintendo_DS", "n3ds": "Nintendo_-_Nintendo_3DS",
    "gamecube": "Nintendo_-_GameCube", "wii": "Nintendo_-_Wii", "wiiu": "Nintendo_-_Wii_U",
    "mastersystem": "Sega_-_Master_System_-_Mark_III", "megadrive": "Sega_-_Mega_Drive_-_Genesis",
    "segacd": "Sega_-_Mega-CD_-_Sega_CD", "saturn": "Sega_-_Saturn", "dreamcast": "Sega_-_Dreamcast",
    "psx": "Sony_-_PlayStation", "ps2": "Sony_-_PlayStation_2", "ps3": "Sony_-_PlayStation_3",
    "psp": "Sony_-_PlayStation_Portable", "psvita": "Sony_-_PlayStation_Vita",
    "neogeocd": "SNK_-_Neo_Geo_CD", "pcengine": "NEC_-_PC_Engine_-_TurboGrafx_16",
    "arcade": "FBNeo_-_Arcade_Games", "cps1": "FBNeo_-_Arcade_Games", "cps2": "FBNeo_-_Arcade_Games",
    "mame": "MAME", "atari2600": "Atari_-_2600", "atari7800": "Atari_-_7800", "atarijaguar": "Atari_-_Jaguar",
}
_FORBIDDEN = re.compile(r'[&*/:`<>?\\|"]')
_LANG = r"[A-Z][a-z](?:-[A-Z]{2})?"
# a trailing group that says which revision, version or languages: (Rev 1) (v1.01) (En,Fr)
_REMOVABLE = re.compile(rf"^(?:Rev ?\w+|v\d[\w.]*|{_LANG}(?:,{_LANG})*)$")
_GROUP = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]$")


def thumbnail_name(stem: str) -> str:
    """The name the thumbnail repositories use for a game: characters they do not allow become `_`."""
    return _FORBIDDEN.sub("_", stem)


def url(system: str, name: str) -> str | None:
    """Where the box art called `name` is kept for this system, or None when there is no repository."""
    repo = REPOS.get(system)
    if not repo:
        return None
    return f"{BASE}/{repo}/master/Named_Boxarts/{quote(thumbnail_name(name) + '.png', safe='')}"


def candidates(stem: str) -> list[str]:
    """Names to try, best first: the file name, then with revision/version/language groups taken off from
    the right one at a time (the region group stays)."""
    names, current = [stem], stem
    while True:
        found = _GROUP.search(current)
        if not found or not _REMOVABLE.match(found.group(1)):
            return names
        current = current[:found.start()]
        if not current:
            return names
        names.append(current)


def picture_ext(data: bytes) -> str | None:
    """'.png' or '.jpg' when the bytes start like one, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    return None


def complete(data: bytes, ext: str) -> bool:
    """Whether a picture that starts right also ends right (a PNG with its IEND chunk, a JPEG with FFD9):
    the most that can be told without an image library. A body cut short fails this."""
    if ext == ".png":
        return data.endswith(b"IEND\xaeB`\x82")
    return data.rstrip(b"\0").endswith(b"\xff\xd9")


@dataclass
class Result:
    arrived: int = 0            # covers that came this time
    none: int = 0               # games asked about (or skipped) that still have no cover
    failed: str = ""            # the reason a network problem ended the run, or ""


def fetch_missing(art: Art, games: Iterable[tuple[str, str]], get: Callable[[str], bytes | None],
                  now: float | None = None, changed: Callable[[], None] | None = None,
                  stop: Callable[[], bool] | None = None) -> Result:
    """Download covers for the games that have none. `get(url)` returns the bytes, None for "not found",
    and raises OSError when the network fails (the run then ends; nothing is remembered as a miss)."""
    when = time.time() if now is None else now
    result = Result()
    try:
        for system, stem in games:
            if stop and stop():
                break
            if art.cover(system, stem) is not None:
                continue
            if url(system, stem) is None:
                result.none += 1
                continue
            asked = art.missed_since(system, stem)
            if asked is not None and when - asked < RETRY_AFTER:
                result.none += 1
                continue
            for name in candidates(stem):
                data = get(url(system, name))
                ext = picture_ext(data) if data and len(data) <= MAX_BYTES else None
                if ext and not complete(data, ext):
                    raise OSError("a download was cut short")
                if ext and art.store_download(system, stem, data, ext):
                    result.arrived += 1
                    if changed:
                        changed()
                    break
            else:
                art.note_miss(system, stem, when)
                result.none += 1
    except (OSError, http.client.HTTPException) as e:
        result.failed = str(e) or type(e).__name__
    finally:
        art.save_misses()
    return result


def counts(art: Art, games: Iterable[tuple[str, str]]) -> tuple[int, int, int]:
    """How many games have a cover from ES-DE, a downloaded one, or none."""
    esde = down = none = 0
    for system, stem in games:
        if art.esde_cover(system, stem):
            esde += 1
        elif art.downloaded_cover(system, stem):
            down += 1
        else:
            none += 1
    return esde, down, none


# ---------------------------------------------------------------- onto the handheld
def device_covers_dir(profile: Profile, system: str) -> str | None:
    """Where ES-DE keeps the covers of a system on the handheld, or None when it has no ES-DE."""
    media = media_dir(profile)
    return f"{media}/{systems.get(system).esde}/covers" if media else None


def for_adds(plan, art: Art, profile: Profile) -> frozenset:
    """Keys of the games a sync adds that Jewelcase has a cover for (ES-DE may have one already: the sync checks)."""
    if not profile.gamelists_dir:
        return frozenset()
    return frozenset(i.row.key for i in plan.adds if art.cover(i.row.system, i.row.stem))


def has_device_cover(dev: Device, profile: Profile, system: str, stem: str,
                     known: dict[str, set[str]] | None = None) -> bool:
    """Whether any picture for the game is in ES-DE's covers folder, whatever its extension. `known` keeps
    what was listed per folder, so a run lists each system's folder once."""
    folder = device_covers_dir(profile, system)
    if not folder:
        return False
    if known is None or folder not in known:
        stems = {PurePosixPath(f.path).stem for f in dev.list_files(folder, maxdepth=1)
                 if _image(PurePosixPath(f.path).name)}
        if known is None:
            return stem in stems
        known[folder] = stems
    return stem in known[folder]


def part_path(profile: Profile, art: Art, system: str, stem: str) -> str | None:
    """The temporary name copy_cover writes under, or None when there is nothing to copy."""
    folder, source = device_covers_dir(profile, system), art.cover(system, stem)
    return f"{folder}/{stem}{source.suffix.lower()}.part" if folder and source else None


def copy_cover(dev: Device, profile: Profile, art: Art, system: str, stem: str,
               listed: bool | None = None, known: dict[str, set[str]] | None = None) -> tuple[str, str]:
    """Put Jewelcase's cover of a game into ES-DE's covers folder on the handheld, unless ES-DE has any picture
    for the game already. Never replaces or deletes a file. Returns ("done" | "skipped", why); a problem with
    the cover is "skipped", never a failure. `listed`: the caller already knows the handheld has a cover.
    `known`: folder -> stems listed earlier in this run (see has_device_cover); a copy is added to it."""
    folder = device_covers_dir(profile, system)
    if not folder:
        return "skipped", "the handheld has no ES-DE"
    source = art.cover(system, stem)
    if source is None:
        return "skipped", "Jewelcase has no cover for it"
    remote = f"{folder}/{stem}{source.suffix.lower()}"
    part = remote + ".part"
    try:
        if listed or has_device_cover(dev, profile, system, stem, known):
            return "skipped", "ES-DE already has a cover"
        want = md5_file(source)
        dev.mkdir(folder)
        for _ in range(2):
            dev.push(source, part)
            dev.chmod(part, "666")
            if dev.md5(part) == want:
                if dev.exists(remote):                          # appeared meanwhile: never replace
                    dev.remove(part)
                    return "skipped", "ES-DE already has a cover"
                dev.rename(part, remote)
                if known is not None:
                    known.setdefault(folder, set()).add(stem)
                return "done", ""
            dev.remove(part)
        return "skipped", "the copy on the handheld did not match, so it was not kept"
    except DeviceError as e:                                    # DeviceGone is not caught: the sync waits for it
        try:
            dev.remove(part)
        except DeviceError:
            pass
        return "skipped", str(e)
    except OSError as e:
        return "skipped", f"read error: {e.strerror or e}"


def push_missing(dev: Device, profile: Profile, art: Art, rows) -> tuple[int, int]:
    """For games already on the handheld: copy Jewelcase's cover where ES-DE has none. Returns (copied, left)."""
    copied = left = 0
    media = media_dir(profile)
    if not media:
        return 0, 0
    have: dict[str, set[str]] = {}                              # ES-DE system -> stems with a picture
    prefix = media.rstrip("/") + "/"
    for f in dev.list_files(media, maxdepth=3):
        parts = f.path[len(prefix):].split("/") if f.path.startswith(prefix) else []
        if len(parts) == 3 and parts[1] == "covers" and _image(parts[2]):
            have.setdefault(parts[0], set()).add(PurePosixPath(parts[2]).stem)
    known = {f"{media}/{esde}/covers": stems for esde, stems in have.items()}
    for r in rows:
        folder = device_covers_dir(profile, r.system)
        if not r.on_device or r.stem in have.get(systems.get(r.system).esde, ()):
            continue
        if art.cover(r.system, r.stem) is None:
            left += 1
            continue
        known.setdefault(folder, set())                         # the listing above covered every folder
        state, _why = copy_cover(dev, profile, art, r.system, r.stem, known=known)
        copied += state == "done"
        left += state != "done"
    return copied, left


def net_get(url: str) -> bytes | None:
    """The real network: the bytes, None for 404, OSError when it fails."""
    from . import net
    return net.fetch_bytes(url, MAX_BYTES)


def games_of(rows) -> list[tuple[str, str]]:
    return [(r.system, r.stem) for r in rows]


def cmd_covers(s, args, out, get=None) -> int:
    """`jewelcase covers [--download]`: where each game's cover comes from, and fetch the missing ones."""
    art = s.art
    if s.device is not None and s.profile is not None:                 # ES-DE's covers, copied the way the window does
        art.refresh(s.device, s.profile, sorted({r.system for r in s.rows()}), s.cache_dir)
    rows = s.rows()
    games = games_of(rows)
    arrived, failed = 0, ""
    if args.download:
        result = fetch_missing(art, games, get or net_get)
        arrived, failed = result.arrived, result.failed
    pushed = None
    if getattr(args, "push", False):
        if s.device is None or s.profile is None:
            out(args, {"error": "no device"}, "No device connected.")
            return 2
        from .sync import Busy, job_lock
        try:
            with job_lock(s.data_dir):                              # the lock a sync takes
                pushed = push_missing(s.device, s.profile, art, rows)
        except Busy as e:
            out(args, {"error": str(e)}, str(e))
            return 2
    esde, down, none = counts(art, games)
    data = {"from_esde": esde, "downloaded": down, "none": none}
    if pushed is not None:
        data["copied_to_handheld"] = pushed[0]
    if args.download:
        data.update({"arrived": arrived, "failed": failed or None})
    text = f"From ES-DE: {esde}\nDownloaded: {down}\nNone: {none}"
    if args.download:
        text += f"\nArrived now: {arrived}" + (f"\nStopped: {failed}" if failed else "")
    if pushed is not None:
        text += f"\nCopied to the handheld: {pushed[0]}"
    out(args, data, text)
    return 1 if failed else 0
