"""Everything one run of Jewelcase works with: settings, database, library and the connected device."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import adb as adbmod
from . import catalog, config, demo
from .catalog import GameRow, Profile
from .config import Config
from .db import Store
from .carddevice import CardDevice, card_is_empty, card_is_mounted
from .device import Device, DeviceGone, DeviceInfo, Storage
from .library import Library
from .plan import SyncPlan, build_plan
from .sync import Runner


@dataclass
class Session:
    cfg: Config
    store: Store
    library: Library
    data_dir: Path
    config_path: Path | None = None
    demo_device: Device | None = None
    device: Device | None = None
    profile: Profile | None = None
    serial: str | None = None
    adb_enabled: bool = True          # False: never run adb (a card-only run, tests)
    card_problem: str = ""            # the card is there but has no game folders
    problem: str = ""                 # why no device is usable, in words; "" when fine or simply unplugged
    targets: list = field(default_factory=list)
    emulator_packages: set[str] = field(default_factory=set)
    _art: object = field(default=None, repr=False, compare=False)
    # settings given on the command line for this run only, with what the settings file has for them;
    # saving the settings writes the file's value back, never the override
    _overridden: dict = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------ locations
    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "sync.log"

    @property
    def user_catalog(self) -> Path | None:
        return self.config_path.parent / "emulators.toml" if self.config_path else None

    @property
    def user_bios(self) -> Path | None:
        return self.config_path.parent / "bios.toml" if self.config_path else None

    def bios_data(self):
        """The BIOS files Jewelcase recognises: the shipped list, the user's own file, and in demo mode made-up ones."""
        from . import bios
        return bios.load(self.user_bios, demo.bios_entries() if self.demo_device is not None else None)

    @property
    def cache_dir(self) -> Path:
        return config.paths().cache if self.config_path else self.data_dir / "cache"

    @property
    def art(self):
        """Pictures for the games: ES-DE's covers copied here, and downloaded ones (core/art.py)."""
        if self._art is None:
            from .art import Art
            self._art = Art(self.cache_dir / "art")
        return self._art

    @property
    def saves_root(self) -> Path | None:
        """Where this device's saves are kept in the library; None without a library or a known device."""
        if not self.serial or not self.library.root:
            return None
        from .savebackup import saves_root
        info = self.info()
        return saves_root(self.library.root, info.model if info else "device", self.serial)

    @property
    def adb_path(self) -> str | None:
        return adbmod.find_adb(self.cfg.adb, self.data_dir)

    # ------------------------------------------------------------ device
    def _adb_candidates(self) -> list[Device]:
        """Handhelds adb can reach, the one used last first. Sets `problem` when there is none to use."""
        exe = self.adb_path
        if not exe:
            self.problem = "adb not found"
            return []
        self.targets = adbmod.pick_targets(adbmod.list_targets(exe))
        ready = [t for t in self.targets if t.state == "device"]
        if not ready and self.targets:
            state = self.targets[0].state
            self.problem = {
                "unauthorized": "Tap Allow USB debugging on the device",
                "no permissions": "This user may not use the USB device (udev rule missing)",
                "offline": "The device is offline; unplug it and plug it back in",
            }.get(state, f"Device is {state}")
        if self.serial:
            ready.sort(key=lambda t: t.serial != self.serial)
        found: list[Device] = []
        for t in ready:
            dev = adbmod.AdbDevice(exe, t)
            try:
                dev.use_hardware_serial()       # the same handheld over USB or Wi-Fi is one device
            except DeviceGone:
                continue
            found.append(dev)
        if self.serial:
            found.sort(key=lambda d: d.serial != self.serial)
        return found

    def card_ready(self) -> bool:
        """The configured card or folder is there right now (a missing or empty folder is simply not connected)."""
        return self.cfg.card is not None and card_is_mounted(self.cfg.card)

    @property
    def is_card(self) -> bool:
        """The device in use (or, when none is connected, the one last seen) is a card or folder."""
        if self.device is not None:
            return not self.device.has_apps
        info = self.info()
        return bool(info and info.transport == "card")

    def connect(self) -> bool:
        """Find a usable device. Returns True when one is connected and has a ROM folder."""
        self.device, self.problem, self.card_problem = None, "", ""
        if self.demo_device is not None:
            candidates = [self.demo_device] if getattr(self.demo_device, "online", True) else []
        elif self.adb_enabled:
            if not self.adb_path and self.cfg.card is None:
                self.problem = "adb not found"          # nothing else to look at: as it always was
                return False
            candidates = self._adb_candidates()
        else:
            candidates = []
        if self.demo_device is None and self.card_ready():
            candidates.append(CardDevice(self.cfg.card))        # after the handhelds: a handheld wins
        for dev in candidates:
            try:
                saved = self.store.meta(dev.serial, "profile")
                profile = Profile.from_dict(saved) if saved else catalog.detect_profile(dev)
            except DeviceGone:
                continue
            if profile is None:
                if dev.has_apps:
                    self.problem = "No ROM folders found on the device"
                else:
                    self.card_problem = "No game folders (psx, gba, ps2 …) found on the card"
                    self.problem = self.problem or self.card_problem
                continue
            if not saved:
                self.store.set_meta(dev.serial, "profile", profile.to_dict())
            self.device, self.profile, self.serial, self.problem = dev, profile, dev.serial, ""
            return True
        if not self.problem and self.cfg.card is not None and self.demo_device is None \
                and card_is_empty(self.cfg.card):
            self.problem = "The card is empty. Make a folder named ROMs on it to use it."
        if self.serial is None:
            known = self.store.known_serials()
            if known:
                self.serial = known[0]
        if self.serial and self.profile is None:
            saved = self.store.meta(self.serial, "profile")
            self.profile = Profile.from_dict(saved) if saved else None
        return False

    def live_device(self) -> Device | None:
        """The device if it still answers, reconnecting when it came back."""
        if self.device is None:
            self.connect()
        return self.device

    def refresh(self) -> None:
        """Re-read the library folder and, when connected, the device."""
        self.library.scan()
        if self.device is not None:
            try:
                if self.serial:
                    catalog.seed_from_manifest(self.library, self.store, self.serial)
                catalog.refresh_device(self.device, self.profile, self.store, self.library.systems())
            except DeviceGone:
                self.device = None

    # ------------------------------------------------------------ views
    def rows(self) -> list[GameRow]:
        return catalog.build_rows(self.library, self.store, self.serial)

    def info(self) -> DeviceInfo | None:
        d = self.store.meta(self.serial, "info") if self.serial else None
        return DeviceInfo(**d) if d else None

    def storage(self, which: str = "card") -> Storage | None:
        d = self.store.meta(self.serial, f"storage_{which}") if self.serial else None
        return Storage(**d) if d else None

    def plan(self, rows: list[GameRow] | None = None) -> SyncPlan:
        connected = self.device is not None
        plan = build_plan(rows if rows is not None else self.rows(),
                          self.storage() if connected else None, self.library,
                          self.info() if connected else None)
        if connected and self.profile is not None and self.profile.gamelists_dir:
            # games being added that Jewelcase has a cover for: it goes to ES-DE too, unless ES-DE has one
            from .covers import for_adds
            plan = replace(plan, covers=for_adds(plan, self.art, self.profile))
        return plan

    def runner(self, on_progress=None) -> Runner:
        return Runner(self.live_device, self.library, self.store, self.profile, self.serial,
                      log_path=self.log_path, data_dir=self.data_dir,
                      emulator_packages=self.emulator_packages, on_progress=on_progress,
                      cache_dir=self.cache_dir, art=self.art)

    def set_library(self, path: Path) -> None:
        self._overridden.pop("library", None)           # chosen in the app: that is the setting now
        self.cfg.library = Path(path)
        self.library.root = Path(path)
        self.library.invalidate()
        self.save_config()

    def set_card(self, path: Path | None) -> None:
        """Use a mounted card or folder as the device when no handheld is plugged in; None stops using it."""
        self._overridden.pop("card", None)
        self.cfg.card = Path(path) if path else None
        self.save_config()

    def set_inbox(self, path: Path) -> None:
        self.cfg.inbox = Path(path)
        self.save_config()

    def save_config(self) -> None:
        if self.config_path:
            config.save(replace(self.cfg, **self._overridden) if self._overridden else self.cfg, self.config_path)


def open_session(demo_dir: Path | None = None, use_demo: bool = False,
                 library: Path | None = None, card: Path | None = None) -> Session:
    if use_demo or demo_dir:
        root = Path(demo_dir) if demo_dir else Path(tempfile.mkdtemp(prefix="jewelcase-demo-"))
        cfg, store, dev = demo.open_demo(root)
        if card:        # the demo's library with a card as the device, and no adb
            cfg.card = Path(card)
            s = Session(cfg, store, Library(cfg.library, store), root / "data", adb_enabled=False)
        else:
            s = Session(cfg, store, Library(cfg.library, store), root / "data", demo_device=dev)
    else:
        p = config.paths()
        cfg = config.load(p.config)
        overridden = {}
        if library:
            overridden["library"] = cfg.library
            cfg.library = Path(library)
        if card:
            overridden["card"] = cfg.card
            cfg.card = Path(card)
        store = Store(p.data / "jewelcase.db")
        s = Session(cfg, store, Library(cfg.library, store), p.data, config_path=p.config,
                    _overridden=overridden)
    try:
        from .emulators import emulator_packages, load_catalog
        s.emulator_packages = emulator_packages(load_catalog(s.user_catalog))
    except ImportError:
        pass
    return s
