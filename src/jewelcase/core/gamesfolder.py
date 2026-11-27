"""Correcting the games folder: which folder on the device Jewelcase treats as the one that holds the systems.

Detection picks one by itself and keeps it in the device's profile. A folder set by hand replaces that
profile's games folder and is marked, so "Detect Again" knows to go back to detection."""
from __future__ import annotations

from dataclasses import replace

from . import catalog, systems
from .catalog import Candidate, FolderError, Profile
from .device import DeviceGone

SET_KEY = "games_folder_set"         # per-device meta: the folder you chose, or absent when detected

__all__ = ["FolderError", "choices", "detect_again", "set_folder", "source"]


def source(session) -> str:
    """'set' when you chose the games folder, 'detected' when Jewelcase found it."""
    return "set" if session.serial and session.store.meta(session.serial, SET_KEY) else "detected"


def _writable(session) -> None:
    """The reasons the folder cannot be changed right now, as FolderError."""
    if session.device is None or session.serial is None or session.profile is None:
        raise FolderError("Connect the device first, so the folder can be checked on it.")
    if session.store.open_job(session.serial) is not None:
        raise FolderError("A sync has not finished yet. Resume or discard it first: it was planned with "
                          "the current games folder.")


def choices(session) -> list[Candidate]:
    """The folders detection considered, with their system folders and games counted; the current one is
    always among them. Reads the device."""
    dev, current = session.device, session.profile
    known = session.library.systems()
    found = catalog.candidates(dev, known)
    twin = {c.path.lower() for c in found} if not dev.has_apps else set()      # FAT: /roms is /ROMs
    if current and current.rom_root not in {c.path for c in found} and current.rom_root.lower() not in twin:
        n = len((systems.known_folders() | set(known)) & set(dev.list_dirs(current.rom_root)))
        found.insert(0, Candidate(current.rom_root, current.card_root, n))
    return [replace(c, games=catalog.count_games(dev, c.path, known)) for c in found]


def set_folder(session, path: str) -> Profile:
    """Use `path` on the device as the games folder, and remember it for this device.
    Raises FolderError, with the reason in words, when it cannot be used."""
    _writable(session)
    try:
        clean = catalog.check_games_folder(session.device, path, session.library.systems())
    except DeviceGone:
        raise FolderError("The device was disconnected. Connect it and try again.") from None
    old = session.profile
    profile = Profile(clean, old.gamelists_dir, catalog.card_root_of(clean, old.card_root))
    session.store.set_meta(session.serial, "profile", profile.to_dict())
    session.store.set_meta(session.serial, SET_KEY, clean)
    session.profile = profile
    return profile


def detect_again(session) -> Profile:
    """Forget the folder you chose and let Jewelcase find it again."""
    _writable(session)
    try:
        profile = catalog.detect_profile(session.device)
    except DeviceGone:
        raise FolderError("The device was disconnected. Connect it and try again.") from None
    if profile is None:
        raise FolderError("Jewelcase could not find a games folder on the device, so the one in use is kept.")
    session.store.set_meta(session.serial, "profile", profile.to_dict())
    session.store.set_meta(session.serial, SET_KEY, None)
    session.profile = profile
    return profile
