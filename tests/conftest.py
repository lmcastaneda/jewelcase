import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def write_tree(root: Path, tree: dict) -> Path:
    """{"ps2": {"A.chd": b"bytes" | "text"}} -> files under root."""
    for folder, files in tree.items():
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body.encode() if isinstance(body, str) else body)
    return root


@pytest.fixture
def tree():
    return write_tree


# ---------------------------------------------------------------- a small world: library + fake device
CARD = "/storage/1A2B-3C4D"
GL = "/sdcard/ES-DE/gamelists"


def _game(path, name="", plays=0, seconds=0, last=""):
    import html
    out = f"\t<game>\n\t\t<path>./{html.escape(path, quote=False)}</path>\n"
    if name:
        out += f"\t\t<name>{html.escape(name, quote=False)}</name>\n"
    if plays:
        out += f"\t\t<playcount>{plays}</playcount>\n"
    if last:
        out += f"\t\t<lastplayed>{last}</lastplayed>\n"
    if seconds:
        out += f"\t\t<playtime>{seconds}</playtime>\n"
    return out + "\t</game>\n"


def gamelist_xml(*games):
    return '<?xml version="1.0"?>\n<gameList>\n' + "".join(_game(*g) for g in games) + "</gameList>\n"


FF7_M3U = "FF7 (USA) (Disc 1).chd\nFF7 (USA) (Disc 2).chd\n"
MTV_CUE = 'FILE "MTV (USA) (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\nFILE "MTV (USA) (Track 2).bin" BINARY\n'
BOTH = {   # on the device and in the library, byte-identical
    "ps2": {"God of War II (USA).chd": b"gow2" * 1000, "Baldur's Gate & Co (USA).chd": b"bg" * 700},
    "psx": {"FF7 (USA).m3u": FF7_M3U, "FF7 (USA) (Disc 1).chd": b"d1" * 500, "FF7 (USA) (Disc 2).chd": b"d2" * 600,
            "MTV (USA).cue": MTV_CUE, "MTV (USA) (Track 1).bin": b"t1" * 300, "MTV (USA) (Track 2).bin": b"t2" * 100},
    "gamecube": {"Metroid Prime (USA).rvz": b"mp" * 900},
}
LIB_ONLY = {
    "ps2": {"Champions (USA).chd": b"ch" * 800, "Only Archive (USA).7z": b"7z" * 50},
    "gamecube": {"Pikmin 2 (USA).rvz": b"p2" * 400},
}
DEV_ONLY = {"ps2": {"Device Only (USA).chd": b"do" * 250}}


class World:
    def __init__(self, tmp):
        from jewelcase.core import catalog
        from jewelcase.core.db import Store
        from jewelcase.core.fakedevice import FakeDevice
        from jewelcase.core.library import Library
        self.tmp = tmp
        self.lib_root = tmp / "library"
        for part in (BOTH, LIB_ONLY):
            write_tree(self.lib_root, part)
        self.dev = FakeDevice(tmp / "device")
        self.dev.storage_total = 1_000_000
        card = self.dev.root / CARD.lstrip("/")
        for part in (BOTH, DEV_ONLY):
            write_tree(card, part)
        write_tree(card, {"ps2": {"images/God of War II (USA).png": b"art",
                                  "gamelist.xml": gamelist_xml(("God of War II (USA).chd", "God of War II"),
                                                               ("Champions (USA).chd", "Champions"))},
                          "Music": {"song.mp3": b"not a game"}})
        write_tree(self.dev.root / GL.lstrip("/"), {
            "ps2": {"gamelist.xml": gamelist_xml(
                ("God of War II (USA).chd", "God of War II", 2, 3000, "20261110T200000"),
                ("Baldur's Gate & Co (USA).chd", "Baldur's Gate & Co", 1, 15, "20261101T100000"),
                ("Device Only (USA).chd", "Device Only"))},
            "psx": {"gamelist.xml": gamelist_xml(
                ("FF7 (USA).m3u", "Final Fantasy VII", 3, 5400, "20260801T120000"),
                ("FF7 (USA) (Disc 1).chd", "Final Fantasy VII (Disc 1)", 1, 600, "20260701T120000"),
                ("MTV (USA).cue", "MTV Music Generator"))},
            "gc": {"gamelist.xml": gamelist_xml(("Metroid Prime (USA).rvz", "Metroid Prime"))},
        })
        write_tree(self.dev.root / "sdcard/ES-DE", {"downloaded_media/ps2/covers": {"God of War II (USA).png": b"cover"}})
        self.store = Store(":memory:")
        self.library = Library(self.lib_root, self.store)
        self.serial = self.dev.serial
        self.profile = catalog.Profile(CARD, GL, CARD)
        self.catalog = catalog

    def refresh(self):
        self.library.scan()
        self.catalog.refresh_device(self.dev, self.profile, self.store, self.library.systems())
        return self

    def rows(self, serial="default"):
        return self.catalog.build_rows(self.library, self.store, self.serial if serial == "default" else serial)

    def row(self, system, stem):
        return next(r for r in self.rows() if r.key == (system, stem))

    def dev_path(self, system, name):
        return self.dev.root / CARD.lstrip("/") / system / name


@pytest.fixture
def world(tmp_path):
    return World(tmp_path).refresh()
