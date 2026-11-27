"""Read play stats from an ES-DE gamelist.xml, and remove entries without disturbing the rest."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime

_GAME = re.compile(r"[ \t]*<game>.*?</game>[ \t]*\r?\n?", re.S)


@dataclass(frozen=True)
class Entry:
    file: str
    name: str
    plays: int
    seconds: int
    last_played: datetime | None


def _tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def _file_of(block: str) -> str:
    return _tag(block, "path").replace("\\", "/").rsplit("/", 1)[-1]


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def _when(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def parse(xml: str) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for m in _GAME.finditer(xml or ""):
        block = m.group(0)
        f = _file_of(block)
        if f:
            out[f] = Entry(f, _tag(block, "name") or f, _int(_tag(block, "playcount")),
                           _int(_tag(block, "playtime")), _when(_tag(block, "lastplayed")))
    return out


def drop(xml: str, files: set[str]) -> tuple[str, list[str]]:
    removed: list[str] = []

    def keep(m: re.Match) -> str:
        f = _file_of(m.group(0))
        if f in files:
            removed.append(f)
            return ""
        return m.group(0)

    return _GAME.sub(keep, xml), removed


# ---------------------------------------------------------------- which emulator ES-DE launches
# ES-DE keeps a system's emulator choice in front of <gameList>, and a game's own choice inside its <game>.
_SYSTEM_EMU = re.compile(r"[ \t]*<alternativeEmulator>.*?</alternativeEmulator>[ \t]*\r?\n?", re.S)
_GAME_EMU = re.compile(r"<altemulator>(.*?)</altemulator>", re.S)


def system_emulator(xml: str) -> str:
    """The label of the emulator chosen for the whole system, or "" for ES-DE's default."""
    m = _SYSTEM_EMU.search(xml or "")
    return _tag(m.group(0), "label") if m else ""


def set_system_emulator(xml: str, label: str) -> str:
    """The same list with the system's emulator set to `label` ("" clears it). Games are not touched."""
    xml = _SYSTEM_EMU.sub("", xml or "", count=1)
    if not label:
        return xml
    block = f"<alternativeEmulator>\n\t<label>{html.escape(label, quote=False)}</label>\n</alternativeEmulator>\n"
    if "<gameList" not in xml:
        return (xml if xml.strip() else '<?xml version="1.0"?>\n') + block + "<gameList>\n</gameList>\n"
    at = xml.index("<gameList")
    return xml[:at] + block + xml[at:]


def game_emulators(xml: str) -> dict[str, int]:
    """How many games carry their own emulator choice, by label."""
    out: dict[str, int] = {}
    for m in _GAME_EMU.finditer(xml or ""):
        label = html.unescape(m.group(1).strip())
        if label:
            out[label] = out.get(label, 0) + 1
    return out
