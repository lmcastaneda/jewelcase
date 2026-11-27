"""Group the files of one system folder into games (a .cue with its tracks, an .m3u with its discs)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Iterable


@dataclass(frozen=True)
class Unit:
    stem: str
    head: str                       # the .m3u, .cue or the single file
    files: tuple[str, ...]          # head first, then everything it owns that exists
    missing: tuple[str, ...] = ()   # referenced but not present

    @property
    def ext(self) -> str:
        return PurePosixPath(self.head).suffix.lower()


UNREADABLE = "(its playlist could not be read)"


def _base(ref: str) -> str:
    return ref.replace("\\", "/").rsplit("/", 1)[-1].strip()


_CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', re.I | re.M)


def cue_files(text: str) -> list[str]:
    text = text.lstrip("\ufeff")                # a byte order mark is not whitespace to the pattern
    return [_base(a or b) for a, b in _CUE_FILE.findall(text)]


def m3u_files(text: str) -> list[str]:
    out = []
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(_base(line))
    return out


def _stem(name: str) -> str:
    return PurePosixPath(name).stem


def group_units(names: Iterable[str], read_text: Callable[[str], str]) -> list[Unit]:
    names = list(dict.fromkeys(names))
    present = set(names)

    def refs(name: str, parser) -> list[str] | None:
        try:
            return parser(read_text(name))
        except (OSError, KeyError, UnicodeError):
            return None

    def build(head: str, wanted: list[str], expand: dict[str, "Unit"] | None = None) -> Unit:
        files, missing = [head], []
        for ref in wanted:
            if ref not in present:
                if ref not in missing:
                    missing.append(ref)
                continue
            members = expand[ref].files if expand and ref in expand else (ref,)
            for m in members:
                if m not in files:
                    files.append(m)
        return Unit(_stem(head), head, tuple(files), tuple(missing))

    unreadable: set[str] = set()
    cue_units: dict[str, Unit] = {}
    for n in names:
        if n.lower().endswith(".cue"):
            r = refs(n, cue_files)
            if r is not None:
                cue_units[n] = build(n, r)
            else:
                unreadable.add(n)

    m3u_units: list[Unit] = []
    for n in names:
        if n.lower().endswith(".m3u"):
            r = refs(n, m3u_files)
            if r is not None:
                m3u_units.append(build(n, r, cue_units))
            else:
                unreadable.add(n)

    owned: set[str] = set()
    for u in m3u_units:
        owned.update(u.files)
    units = list(m3u_units)
    for head, u in cue_units.items():
        if head not in owned:
            units.append(u)
    for u in units:
        owned.update(u.files)
    for n in names:
        if n not in owned:
            # a playlist we could not read is kept visible, but marked so it is never synced half-known
            units.append(Unit(_stem(n), n, (n,), (UNREADABLE,) if n in unreadable else ()))
    units.sort(key=lambda u: (u.stem.lower(), u.head.lower()))
    return units
