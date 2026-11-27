"""Save sources against a real handheld. Read-only. Run with:  uv run pytest -m device tests/device -s"""
import re

import pytest

from jewelcase.core import adb
from jewelcase.core.savesources import load_sources, scan_all

pytestmark = pytest.mark.device

# things that sit beside saves and must never be taken for one
NOT_A_SAVE = re.compile(r"(^|/)(bios|firmware|sysdata|shaders?|cache|covers|cheats|textures|logs?|config|cfg|"
                        r"inputprofiles|gamesettings|screenshots)(/|$)|"
                        r"(aes_keys\.txt|\.cfg|\.ini|\.log|\.png|\.jpg|\.jpeg|\.json|\.xml|\.yml|\.nomedia|\.cache)$",
                        re.IGNORECASE)


@pytest.fixture(scope="module")
def dev():
    exe = adb.find_adb(None)
    if not exe:
        pytest.skip("adb not found")
    targets = [t for t in adb.pick_targets(adb.list_targets(exe)) if t.state == "device"]
    if not targets:
        pytest.skip("no device connected")
    return adb.AdbDevice(exe, targets[0])


@pytest.fixture(scope="module")
def scans(dev):
    found = scan_all(dev, load_sources())
    print()
    for s in found:
        size = sum(f.size for f in s.files)
        state = f"{len(s.files):3d} files {size / 1e6:8.1f} MB" if s.exists else "folder not there"
        extra = f", {len(s.unreadable)} unreadable folders" if s.unreadable else ""
        extra += f", {len(s.blocked)} files that cannot be opened" if s.blocked else ""
        print(f"  {s.source.id:<24} {state}{extra}   {s.root}")
    return found


def test_readonly_some_sources_exist(scans):
    by = {s.source.id: s for s in scans}
    assert by["retroarch-saves"].exists and len(by["retroarch-saves"].files) > 5
    assert sum(1 for s in scans if s.files) >= 6


def test_readonly_only_saves_are_selected(scans):
    wrong = [(s.source.id, f.rel) for s in scans for f in s.files if NOT_A_SAVE.search(f.rel)]
    assert wrong == []


def test_readonly_unreadable_folders_are_seen(scans):
    by = {s.source.id: s for s in scans}
    psp = by["ppsspp-saves"]
    assert psp.exists and len(psp.unreadable) >= 1 and not psp.files      # Android blocks the per-game folders
    assert not by["dolphin-gc-saves"].unreadable                          # its blocked folders hold no saves


def test_readonly_total_is_modest(scans):
    total = sum(f.size for s in scans for f in s.files)
    assert total < 1_000_000_000, f"{total / 1e6:.0f} MB selected: a pattern is too wide"
    assert not [(s.source.id, rel) for s in scans for rel in s.too_large]


def test_readonly_no_file_is_claimed_twice(scans):
    paths = [f"{s.root}/{f.rel}" for s in scans for f in s.files]
    assert len(paths) == len(set(paths))


def test_readonly_every_selected_file_can_be_opened(dev, scans):
    """Files Android lets adb list but not open are reported as blocked, never queued for copying."""
    for s in scans:
        if s.files:
            refused = set(dev.unreadable_paths(s.root))
            assert not [f.rel for f in s.files if f"{s.root}/{f.rel}" in refused], s.source.id
    sample = next(f"{s.root}/{s.files[0].rel}" for s in scans if s.files)
    assert len(dev.md5(sample)) == 32
