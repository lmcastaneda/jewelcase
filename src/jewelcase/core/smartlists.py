"""The owner's own smart lists: a name plus conditions that must all hold, kept in config.toml."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .catalog import GameRow
    from .config import Config

PLAY_TIMES = ("any", "never", "under", "over")        # never played | under N minutes | at least N hours
LAST_PLAYED = ("any", "before", "within")             # more than N days ago | within the last N days
SIZES = ("any", "at_least", "at_most")
SORTS = ("", "title", "size", "played", "last")
PREFIX = "user:"
MB = 1024 * 1024


@dataclass(frozen=True)
class UserList:
    id: str
    name: str
    systems: tuple[str, ...] = ()           # empty: any system
    on_device: bool | None = None           # None: either
    in_library: bool | None = None
    play_time: str = "any"
    play_amount: int = 0                    # minutes for "under", hours for "over"
    last_played: str = "any"
    last_days: int = 0
    size: str = "any"
    size_mb: int = 0
    title_has: str = ""
    sort: str = ""                          # "" keeps the order the games come in
    descending: bool = False


def make_id(name: str, taken: Iterable[str]) -> str:
    """'user:<slug of the name>', with -2, -3 ... added when another list already has it."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "list"
    taken = set(taken)
    candidate, n = PREFIX + slug, 1
    while candidate in taken:
        n += 1
        candidate = f"{PREFIX}{slug}-{n}"
    return candidate


def apply(rows: list["GameRow"], ul: UserList, now: datetime | None = None, never_seconds: int = 60,
          ) -> list["GameRow"]:
    """The games that meet every condition of the list, in the list's own order."""
    now = now or datetime.now()
    text = ul.title_has.strip().casefold()
    systems = set(ul.systems)
    chosen = []
    for r in rows:
        if systems and r.system not in systems:
            continue
        if ul.on_device is not None and r.on_device != ul.on_device:
            continue
        if ul.in_library is not None and r.in_library != ul.in_library:
            continue
        if ul.play_time == "never" and r.seconds >= never_seconds:
            continue
        if ul.play_time == "under" and r.seconds >= ul.play_amount * 60:
            continue
        if ul.play_time == "over" and r.seconds < ul.play_amount * 3600:
            continue
        if ul.last_played == "before" and r.last_played is not None \
                and r.last_played >= now - timedelta(days=ul.last_days):
            continue                        # a game never played has no date and counts as long ago
        if ul.last_played == "within" and (r.last_played is None
                                           or r.last_played < now - timedelta(days=ul.last_days)):
            continue
        if ul.size == "at_least" and r.size < ul.size_mb * MB:
            continue
        if ul.size == "at_most" and r.size > ul.size_mb * MB:
            continue
        if text and text not in r.title.casefold():
            continue
        chosen.append(r)
    key = {"title": lambda r: r.title.casefold(), "size": lambda r: r.size, "played": lambda r: r.seconds,
           "last": lambda r: r.last_played.timestamp() if r.last_played else 0.0}.get(ul.sort)
    return sorted(chosen, key=key, reverse=ul.descending) if key else chosen


def resolve(cfg: "Config", text: str) -> str | None:
    """A list id from an id or a name (any case), built-in or the owner's; None when there is no such list."""
    from .catalog import SMART_LISTS
    wanted = text.strip().casefold()
    if text in SMART_LISTS or any(u.id == text for u in cfg.user_lists):
        return text
    for sid, sl in SMART_LISTS.items():
        if sl.title.casefold() == wanted:
            return sid
    return next((u.id for u in cfg.user_lists if u.name.strip().casefold() == wanted), None)


# ---------------------------------------------------------------- the settings file
def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse(raw) -> list[UserList]:
    """The lists in `[[lists]]` of the settings file. An entry that cannot be understood is skipped, and a
    value that cannot be understood is left out, so a hand-edited file never stops Jewelcase from starting."""
    out: list[UserList] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue
        name = entry["name"].strip()
        given = entry.get("id")
        taken = [u.id for u in out]
        ident = given if isinstance(given, str) and given.startswith(PREFIX) and len(given) > len(PREFIX) \
            and given not in taken else make_id(name, taken)
        kw: dict = {}
        syst = entry.get("systems")
        if isinstance(syst, list):
            kw["systems"] = tuple(s for s in syst if isinstance(s, str) and s)
        for flag in ("on_device", "in_library"):
            if isinstance(entry.get(flag), bool):
                kw[flag] = entry[flag]
        play = entry.get("play_time")
        amount = _count(entry.get("play_minutes" if play == "under" else "play_hours"))
        if play == "never":
            kw["play_time"] = "never"
        elif play in ("under", "over") and amount:
            kw.update(play_time=play, play_amount=amount)
        days = _count(entry.get("last_days"))
        if entry.get("last_played") in ("before", "within") and days:
            kw.update(last_played=entry["last_played"], last_days=days)
        mb = _count(entry.get("size_mb"))
        if entry.get("size") in ("at_least", "at_most") and mb is not None:
            kw.update(size=entry["size"], size_mb=mb)
        if isinstance(entry.get("title_has"), str):
            kw["title_has"] = entry["title_has"].strip()
        if entry.get("sort") in SORTS[1:]:
            kw["sort"] = entry["sort"]
            kw["descending"] = entry.get("descending") is True
        out.append(UserList(ident, name, **kw))
    return out


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def to_toml(lists: Iterable[UserList]) -> list[str]:
    """The `[[lists]]` blocks, one per list, with only the conditions that are set."""
    lines: list[str] = []
    for ul in lists:
        lines += ["", "[[lists]]", f"id = {_q(ul.id)}", f"name = {_q(ul.name)}"]
        if ul.systems:
            lines.append(f"systems = [{', '.join(_q(s) for s in ul.systems)}]")
        for flag in ("on_device", "in_library"):
            if getattr(ul, flag) is not None:
                lines.append(f"{flag} = {'true' if getattr(ul, flag) else 'false'}")
        if ul.play_time != "any":
            lines.append(f"play_time = {_q(ul.play_time)}")
            if ul.play_time != "never":
                lines.append(f"{'play_minutes' if ul.play_time == 'under' else 'play_hours'} = {ul.play_amount}")
        if ul.last_played != "any":
            lines += [f"last_played = {_q(ul.last_played)}", f"last_days = {ul.last_days}"]
        if ul.size != "any":
            lines += [f"size = {_q(ul.size)}", f"size_mb = {ul.size_mb}"]
        if ul.title_has:
            lines.append(f"title_has = {_q(ul.title_has)}")
        if ul.sort:
            lines.append(f"sort = {_q(ul.sort)}")
            if ul.descending:
                lines.append("descending = true")
    return lines
