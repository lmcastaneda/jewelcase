"""ES-DE's side of an emulator: which one it launches for each system, and changing that.

ES-DE describes every system in es_systems.xml: where its games are and a list of launch commands, each
with a label ("aPS3e ISO (Standalone)") and an emulator name ("%EMULATOR_APS3E%"). es_find_rules.xml
turns that name into Android packages. A file of the same shape in ES-DE/custom_systems replaces the
systems it names. ES-DE launches a system's first command unless the system's game list names another
label, and a game can name its own."""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
import shutil
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from . import gamelist, systems
from .catalog import Profile
from .device import Device, DeviceError, DeviceGone

PACKAGE = "org.es_de.frontend"
HOME = "/sdcard/ES-DE"
CUSTOM_SYSTEMS = HOME + "/custom_systems/es_systems.xml"
CUSTOM_RULES = HOME + "/custom_systems/es_find_rules.xml"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SYSTEM = re.compile(r"[ \t]*<system>.*?</system>[ \t]*\r?\n?", re.S)
_EMULATOR = re.compile(r"%EMULATOR_([^%]+)%")
_SHIPPED = {"es_systems.xml": "assets/systems/android/es_systems.xml",
            "es_find_rules.xml": "assets/systems/android/es_find_rules.xml"}


class EsdeError(Exception):
    """Something about ES-DE that could not be done, in words for the user."""

    def __init__(self, message: str = "", previous: dict[str, str] | None = None):
        super().__init__(message)
        self.previous = previous or {}      # when it stopped part-way: the systems already switched, and what they had


@dataclass(frozen=True)
class Command:
    label: str
    emulator: str           # ES-DE's name for the emulator, "APS3E"; "" when the command names none
    saf: bool = False       # hands the game over as an Android document link (%ROMSAF%)


@dataclass(frozen=True)
class EsSystem:
    name: str               # ES-DE's short name, "ps3"
    fullname: str
    path: str               # where ES-DE looks for its games
    commands: tuple[Command, ...]
    custom: bool            # defined by the handheld's own file, not by what ES-DE ships
    block: str              # the <system> element as written


def _systems(xml: str, custom: bool) -> dict[str, EsSystem]:
    out: dict[str, EsSystem] = {}
    for m in _SYSTEM.finditer(_COMMENT.sub("", xml or "")):     # a commented-out system is not a system
        try:
            node = ET.fromstring(m.group(0).strip())
        except ET.ParseError:
            continue
        name = (node.findtext("name") or "").strip()
        commands = []
        for c in node.findall("command"):
            found = _EMULATOR.search(c.text or "")
            commands.append(Command(c.get("label") or "", found.group(1) if found else "", "%ROMSAF%" in (c.text or "")))
        if name and commands:
            out[name] = EsSystem(name, (node.findtext("fullname") or name).strip(),
                                 (node.findtext("path") or "").strip(), tuple(commands), custom, m.group(0))
    return out


@dataclass(frozen=True)
class Definitions:
    systems: dict[str, EsSystem]
    rules: dict[str, tuple[str, ...]]       # ES-DE's emulator name -> the packages that can be it

    @staticmethod
    def parse(bundled: str, rules: str, custom: str = "", custom_rules: str = "") -> "Definitions":
        found: dict[str, tuple[str, ...]] = {}
        for text in (rules, custom_rules):          # the handheld's own rules replace the shipped ones by name
            try:
                for e in ET.fromstring(text).findall("emulator") if text.strip() else []:
                    entries = [(x.text or "").strip().split("/")[0] for x in e.iter("entry")]
                    found[e.get("name") or ""] = tuple(dict.fromkeys(p for p in entries if p))
            except ET.ParseError:
                pass
        return Definitions({**_systems(bundled, False), **_systems(custom, True)}, found)

    def packages(self, emulator: str) -> tuple[str, ...]:
        return self.rules.get(emulator, ())

    def every_package(self) -> set[str]:
        """Every package ES-DE could launch a game with."""
        return {p for packages in self.rules.values() for p in packages}


@dataclass(frozen=True)
class Choice:
    label: str
    package: str            # the installed package this command would start


@dataclass(frozen=True)
class Launch:
    """How ES-DE launches one system's games right now."""
    esde: str
    fullname: str
    default: str            # the first command
    current: str            # the command in use: the system's choice if it has one, else the default
    working: bool           # the command in use can run: its emulator is installed, or cannot be judged
    choices: tuple[Choice, ...]         # every command whose emulator is installed, in ES-DE's order
    per_game: dict[str, int]            # games that carry their own choice, by label
    saf: bool = False                   # the command in use hands games over as a document link
    package: str = ""                   # the installed package that command would start, "" if none


def launch(defs: Definitions, esde: str, gamelist_xml: str, installed: set[str]) -> Launch | None:
    system = defs.systems.get(esde)
    if system is None:
        return None
    choices = []
    for c in system.commands:
        package = next((p for p in defs.packages(c.emulator) if p in installed), None)
        if package:
            choices.append(Choice(c.label, package))
    by_label = {c.label: c for c in system.commands}
    default = system.commands[0].label
    chosen = gamelist.system_emulator(gamelist_xml)
    current = chosen if chosen in by_label else default     # ES-DE ignores a label it does not have
    # A command that names no emulator, or one ES-DE's rules do not list, cannot be judged from here.
    # It is taken to work, so that Jewelcase never switches a system away from something it does not understand.
    judged = bool(defs.packages(by_label[current].emulator))
    command = by_label[current]
    package = next((p for p in defs.packages(command.emulator) if p in installed), "")
    return Launch(esde, system.fullname, default, current, not judged or any(c.label == current for c in choices),
                  tuple(choices), gamelist.game_emulators(gamelist_xml), command.saf, package)


def suggest(state: Launch, prefer: dict[str, str] | None = None, only: str | None = None) -> str | None:
    """The command to switch to when the one in use cannot work, or None. `prefer` maps a package to the
    label that suits what Jewelcase syncs; `only` limits the answer to one package's commands."""
    if state.working:
        return None
    choices = [c for c in state.choices if only is None or c.package == only]
    if not choices:
        return None
    for c in choices:
        if (prefer or {}).get(c.package) == c.label:
            return c.label
    return choices[0].label


# ---------------------------------------------------------------- on the device
def _version_folder(cache: Path, version: str) -> Path:
    return Path(cache) / "esde" / (re.sub(r"[^\w.\-]", "_", version) or "unknown")


_SYSTEM_ART = re.compile(r"assets/[^/]+/system/systemart/([^/]+\.(?:webp|png|jpg|jpeg))")


def bundled_art(dev: Device, cache: Path) -> dict[str, Path]:
    """The system images of the theme inside ES-DE's app: ES-DE's system name -> file on the PC.
    The app file is copied over once per ES-DE version, and only these images are kept from it."""
    found = dev.packages([PACKAGE])
    if not found:
        return {}
    folder = _version_folder(cache, found[0].version) / "systemart"
    if not folder.is_dir():
        remote = dev.apk_path(PACKAGE)
        if not remote:
            return {}
        folder.parent.mkdir(parents=True, exist_ok=True)
        app, part = folder.parent / "app-art.part", folder.with_name("systemart.part")
        shutil.rmtree(part, ignore_errors=True)
        try:
            part.mkdir()
            dev.pull(remote, app)
            with zipfile.ZipFile(app) as z:
                for member in z.namelist():
                    image = _SYSTEM_ART.fullmatch(member)
                    if image:
                        (part / image.group(1)).write_bytes(z.read(member))
            part.rename(folder)
        except (DeviceError, OSError, zipfile.BadZipFile):
            return {}
        finally:
            app.unlink(missing_ok=True)
            shutil.rmtree(part, ignore_errors=True)
    return {p.stem: p for p in sorted(folder.iterdir()) if p.is_file()}


def load(dev: Device, cache: Path) -> Definitions | None:
    """What the ES-DE on this device knows: the definitions inside its app (kept per version, since the
    app's own copy cannot be read over adb) with the handheld's own systems file on top. None without ES-DE."""
    found = dev.packages([PACKAGE])
    if not found:
        return None
    folder = _version_folder(cache, found[0].version)
    files = {name: folder / name for name in _SHIPPED}
    if not all(f.is_file() for f in files.values()):
        remote = dev.apk_path(PACKAGE)
        if not remote:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        app = folder / "app.part"
        try:
            dev.pull(remote, app)
            with zipfile.ZipFile(app) as z:
                for name, member in _SHIPPED.items():
                    part = files[name].with_name(name + ".part")
                    part.write_bytes(z.read(member))
                    part.replace(files[name])
        except (DeviceError, OSError, KeyError, zipfile.BadZipFile):
            return None
        finally:
            app.unlink(missing_ok=True)             # only the two definition files are kept

    def own(path: str) -> str:
        return dev.read_bytes(path).decode("utf-8", "replace") if dev.exists(path) else ""
    try:
        shipped = [files[name].read_text(encoding="utf-8", errors="replace") for name in _SHIPPED]
    except OSError:
        return None
    return Definitions.parse(shipped[0], shipped[1], own(CUSTOM_SYSTEMS), own(CUSTOM_RULES))


@dataclass(frozen=True)
class SystemState:
    system: str             # Jewelcase's folder name for the system
    name: str
    launch: Launch
    listed: bool            # ES-DE looks for this system's games where Jewelcase puts them
    games: int = 0          # games of this system on the device
    queued: bool = False    # games are ticked to go to the device
    fix: str = ""           # when the command in use cannot work: the one to switch to
    grant: str = ""         # when the emulator was not given the games folder: what to tell the user

    @property
    def matters(self) -> bool:
        """Worth showing: it has games, or a choice was made for it, or something can be fixed."""
        return bool(self.games or self.queued or self.fix or self.launch.current != self.launch.default)


def _list_path(profile: Profile, esde: str) -> str | None:
    return f"{profile.gamelists_dir.rstrip('/')}/{esde}/gamelist.xml" if profile.gamelists_dir else None


def _read(dev: Device, path: str | None) -> bytes:
    return dev.read_bytes(path) if path and dev.exists(path) else b""


def overview(dev: Device, profile: Profile, defs: Definitions, folders: Iterable[str],
             installed: set[str] | None = None) -> list[SystemState]:
    """How ES-DE launches each of these systems, for the ones ES-DE has."""
    installed = dev.installed_packages() if installed is None else installed
    root = profile.rom_root.rstrip("/")
    pinned = _pinned(defs, root)
    out = []
    for folder in folders:
        system = systems.get(folder)
        text = _read(dev, _list_path(profile, system.esde)).decode("utf-8", "replace")
        state = launch(defs, system.esde, text, installed)
        if state is not None:
            listed = not pinned or defs.systems[system.esde].path.rstrip("/") == f"{root}/{folder}"
            out.append(SystemState(folder, system.name, state, listed))
    return out


def _pinned(defs: Definitions, root: str) -> bool:
    """A handheld whose own file points systems at the ROM folder has an ES-DE that looks somewhere else by
    default, so a system that file does not name is not found where Jewelcase puts its games."""
    return any(s.custom and s.path.startswith(root + "/") for s in defs.systems.values())


def ensure_listed(dev: Device, profile: Profile, defs: Definitions, folders: Iterable[str],
                  backups: Path) -> list[str]:
    """Add the systems ES-DE would look for elsewhere to the handheld's own systems file, as ES-DE ships
    them but pointed at Jewelcase's folder. A system that file already names is left as it is. Returns the
    names of the systems added; ES-DE reads the file the next time it starts."""
    root = profile.rom_root.rstrip("/")
    if not _pinned(defs, root):
        return []
    add: list[tuple[str, str]] = []
    for folder in dict.fromkeys(folders):
        system = systems.get(folder)
        known = defs.systems.get(system.esde)
        if known is None or known.custom:
            continue
        path = html.escape(f"{root}/{folder}", quote=False)
        block = re.sub(r"<path>.*?</path>", lambda _m: f"<path>{path}</path>", known.block, count=1, flags=re.S)
        add.append((system.name, block if block.endswith("\n") else block + "\n"))
    if not add:
        return []
    raw = dev.read_bytes(CUSTOM_SYSTEMS)
    text = raw.decode("utf-8", "surrogateescape")
    end = text.rfind("</systemList>")
    if end < 0:
        return []
    at = text.rfind("\n", 0, end) + 1           # the start of the closing tag's line...
    if text[at:end].strip():                    # ...unless other things share that line
        at = end
    copy = Path(backups) / f"esde-{time.strftime('%Y%m%d-%H%M%S')}" / CUSTOM_SYSTEMS.strip("/").replace("/", "__")
    copy.parent.mkdir(parents=True, exist_ok=True)
    if not copy.exists():
        copy.write_bytes(raw)
    dev.write_bytes(CUSTOM_SYSTEMS, (text[:at] + "".join(b for _, b in add) + text[at:]).encode("utf-8", "surrogateescape"))
    return [name for name, _ in add]


def game_running(dev: Device, defs: Definitions | None, emulators: set[str]) -> bool:
    """Whether an emulator is in front: one Jewelcase knows, or any package ES-DE could have launched."""
    return dev.foreground_package() in (set(emulators) | (defs.every_package() if defs else set()))


def _write_choice(dev: Device, path: str, want: str, backups: Path) -> tuple[str, bool]:
    """Put `want` ("" for the default) into the game list at `path`, keeping a copy of what was there.
    Returns the choice the list had and whether it had to be written."""
    raw = _read(dev, path)
    text = raw.decode("utf-8", "surrogateescape")
    new = gamelist.set_system_emulator(text, want)
    if new == text:
        return gamelist.system_emulator(text), False
    if raw:
        copy = Path(backups) / f"esde-{time.strftime('%Y%m%d-%H%M%S')}" / path.strip("/").replace("/", "__")
        copy.parent.mkdir(parents=True, exist_ok=True)
        if not copy.exists():
            copy.write_bytes(raw)
    dev.mkdir(path.rsplit("/", 1)[0])
    dev.write_bytes(path, new.encode("utf-8", "surrogateescape"))
    return gamelist.system_emulator(text), True


def use_many(dev: Device, profile: Profile, defs: Definitions, changes: dict[str, str], backups: Path,
             emulators: set[str]) -> dict[str, str]:
    """Have ES-DE launch each system's games (Jewelcase's folder name) with the command given for it, and
    restart ES-DE once. Every change is checked before anything is written. Returns, per system, the choice
    it had before ("" when it was ES-DE's default). The game lists are backed up first."""
    plan: list[tuple[str, str, dict[str, str], str, set[str]]] = []
    for folder, label in changes.items():
        system = systems.get(folder)
        known = defs.systems.get(system.esde)
        if known is None:
            raise EsdeError(f"ES-DE has no system for {system.name}.")
        labels = {c.label for c in known.commands}
        if label not in labels:
            raise EsdeError(f"ES-DE has no \"{label}\" for {system.name}.")
        path = _list_path(profile, system.esde)
        if path is None:
            raise EsdeError("ES-DE's game lists were not found on this device.")
        plan.append((folder, path, "" if label == known.commands[0].label else label, labels))   # default: no entry
    if not plan:
        return {}
    if game_running(dev, defs, emulators):
        raise EsdeError("Close the game on the handheld first.")
    previous: dict[str, str] = {}
    switched: dict[str, str] = {}               # the systems whose list was written, and what they had before
    written = []

    def broke(e: Exception, rest: list[str]) -> EsdeError:
        done = ", ".join(systems.get(f).name for f in switched)
        left = ", ".join(systems.get(f).name for f in rest)
        text = f"The change stopped part-way ({e})."
        if switched:
            text += f" ES-DE was changed for {done}."
        if rest:
            text += f" It was not changed for {left}."
        if not switched:
            text += " Nothing was changed."
        return EsdeError(text, dict(switched))

    for at, (folder, path, want, labels) in enumerate(plan):
        try:
            before, changed = _write_choice(dev, path, want, backups)
        except (DeviceError, DeviceGone) as e:
            raise broke(e, [p[0] for p in plan[at:]]) from e
        previous[folder] = before if before in labels else ""       # a label ES-DE was ignoring cannot be gone back to
        if changed:
            written.append((path, want))
            switched[folder] = previous[folder]
    if not written:
        return previous
    front, failure = "", None
    try:
        front = dev.stop_app(PACKAGE)
        # ES-DE saves a list from memory whenever it likes. If it did so between a write and the stop,
        # the choice is gone again: now that ES-DE is stopped, put it back.
        if [_write_choice(dev, path, want, backups)[1] for path, want in written].count(True):
            front = dev.stop_app(PACKAGE) or front
    except (DeviceError, DeviceGone) as e:
        failure = e
    finally:
        if front:                       # whatever happened, ES-DE is not left closed
            try:
                dev.start_activity(front)
            except (DeviceError, DeviceGone):
                pass
    if failure is not None:
        raise broke(failure, []) from failure
    return previous


def use(dev: Device, profile: Profile, defs: Definitions, folder: str, label: str, backups: Path,
        emulators: set[str]) -> str:
    """Have ES-DE launch the system's games with the command `label`. Returns the choice it had before
    ("" when it was ES-DE's default). The game list is backed up first, and ES-DE is restarted."""
    return use_many(dev, profile, defs, {folder: label}, backups, emulators)[folder]


# ---------------------------------------------------------------- the games folder an emulator was given
def _same_place(path: str) -> str:
    """The internal storage has two names, /sdcard and /storage/emulated/0; use one."""
    path = path.rstrip("/")
    for alias in ("/sdcard", "/storage/self/primary"):
        if path == alias or path.startswith(alias + "/"):
            return "/storage/emulated/0" + path[len(alias):]
    return path


def grant_note(state: Launch, name: str, folder: str, grants: dict[str, tuple[str, ...]], app: str) -> str:
    """What to tell the user when the command in use passes games as a document link and its emulator was
    never given the folder the games are in (`folder`, a device path), else "". `name` is the system's name,
    `app` the emulator's. Without grants to go by nothing is claimed."""
    if not (state.saf and state.package and grants):
        return ""
    want = _same_place(folder)
    for held in grants.get(state.package, ()):
        held = _same_place(held)
        if want == held or want.startswith(held + "/"):
            return ""
    return (f"{app} has not been given the {name} folder yet. "
            f"Open {app} on the handheld and choose that folder in the app.")


# ---------------------------------------------------------------- for a session
def _catalog(session):
    from .emulators import load_catalog
    return load_catalog(session.user_catalog)


def for_session(session) -> list[SystemState] | None:
    """Every system Jewelcase and ES-DE both know, with its games on the device and what could be fixed.
    None when no device is connected or it has no ES-DE."""
    dev = session.device
    if dev is None or session.profile is None:
        return None
    defs = load(dev, session.cache_dir)
    if defs is None:
        return None
    catalog = _catalog(session)
    prefer = {e.package: e.esde for e in catalog.values() if e.esde}
    games: dict[str, int] = {}
    queued: set[str] = set()
    for row in session.rows():
        if row.on_device:
            games[row.system] = games.get(row.system, 0) + 1
        elif row.want:
            queued.add(row.system)
    folders = dict.fromkeys([s.folder for s in systems.all_systems()] + sorted(games) + sorted(queued))
    installed = dev.installed_packages()
    grants = dev.uri_grants()
    root = session.profile.rom_root.rstrip("/")
    out = []
    for state in overview(dev, session.profile, defs, folders, installed):
        count, waiting, fix = games.get(state.system, 0), state.system in queued, ""
        if not state.launch.working:
            if count or waiting:
                fix = suggest(state.launch, prefer) or ""
            else:       # no games yet: only an emulator Jewelcase knows is for this system counts
                for e in catalog.values():
                    if e.package in installed and state.system in e.systems:
                        fix = suggest(state.launch, prefer, only=e.package) or ""
                        if fix:
                            break
        grant = ""
        if count or waiting:
            entry = catalog.get(state.launch.package)
            grant = grant_note(state.launch, state.name, f"{root}/{state.system}", grants,
                               entry.name if entry else state.launch.current)
        out.append(replace(state, games=count, queued=waiting, fix=fix, grant=grant))
    return sorted(out, key=lambda s: s.name.lower())


def choose_many(session, changes: dict[str, str]) -> dict[str, str]:
    """Set what ES-DE launches several systems' games with, restarting ES-DE once. `changes` maps a system
    (Jewelcase's folder name or ES-DE's name) to a command label. Returns, by Jewelcase's folder name, the choice
    each had before or ""."""
    from .sync import job_lock
    dev = session.device
    if dev is None or session.profile is None:
        raise EsdeError("No device connected.")
    defs = load(dev, session.cache_dir)
    if defs is None:
        raise EsdeError("ES-DE is not installed on this device.")
    by_folder = {(system if system in systems.known_folders() else systems.by_esde(system).folder): label
                 for system, label in changes.items()}
    with job_lock(session.data_dir):
        return use_many(dev, session.profile, defs, by_folder,
                        Path(session.data_dir) / "backups" / (session.serial or "device"), session.emulator_packages)


def choose(session, system: str, label: str) -> tuple[str, str]:
    """Set what ES-DE launches a system's games with. `system` is Jewelcase's folder name or ES-DE's name.
    Returns (the system's name, the choice it had before or "")."""
    ((folder, previous),) = choose_many(session, {system: label}).items()
    return systems.get(folder).name, previous


def apply_fixes(session, only: str | None = None,
                problems: list[str] | None = None) -> list[tuple[SystemState, str, str]]:
    """Switch every system whose emulator is not installed to one that is, all in one go (ES-DE restarts
    once). With `only`, just the systems that package can take over: those with games, and those Jewelcase's
    catalogue says it is for. If any of them cannot be changed nothing is, and the reason goes to
    `problems`. Returns (system, the new choice, the choice before) for what was changed."""
    from .sync import Busy
    entry = _catalog(session).get(only) if only else None
    wanted = []
    for state in for_session(session) or []:
        label = state.fix
        if only is not None:
            mine = state.games or state.queued or (entry is not None and state.system in entry.systems)
            label = (suggest(state.launch, _prefer(session), only=only) or "") if mine else ""
        if label:
            wanted.append((state, label))
    if not wanted:
        return []
    try:
        before = choose_many(session, {state.system: label for state, label in wanted})
    except (EsdeError, Busy) as e:
        if problems is not None:
            problems.append(str(e))
        done = getattr(e, "previous", {})       # an interrupted change: what was switched before it stopped
        return [(state, label, done[state.system]) for state, label in wanted if state.system in done]
    return [(state, label, before[state.system]) for state, label in wanted]


def _prefer(session) -> dict[str, str]:
    return {e.package: e.esde for e in _catalog(session).values() if e.esde}


def describe(state: SystemState) -> str:
    """One line about a system, for a list."""
    use_ = state.launch
    if use_.working:
        text = use_.current
    elif state.fix:
        text = f"{use_.current} is not installed"
    else:
        text = f"{use_.current} is not installed, and no installed emulator can open these games"
    others = own_choices(state)
    return text + (f" · {others}" if others else "")


def own_choices(state: SystemState) -> str:
    """"24 games use ARMSX2 (Standalone)": games that carry a choice other than the system's."""
    return ", ".join(f"{n} {'game uses' if n == 1 else 'games use'} {label}"
                     for label, n in sorted(state.launch.per_game.items()) if label != state.launch.current)


def cmd_esde(session, args, out) -> int:
    from .sync import Busy
    sub = getattr(args, "esde_cmd", None)
    try:
        if sub == "use":
            name, _ = choose(session, args.system, args.label)
            print(f"{name} games now open with {args.label}.")
            return 0
        if sub == "fix":
            problems: list[str] = []
            done = apply_fixes(session, problems=problems)
            lines = [f"{s.name} games now open with {label}." for s, label, _ in done] + problems
            print("\n".join(lines) or "Nothing to fix.")
            return 2 if problems else 0
        states = for_session(session)
    except (EsdeError, Busy) as e:
        print(e)
        return 2
    except DeviceGone:
        print("The handheld was disconnected.")
        return 2
    if states is None:
        print("No device connected, or it has no ES-DE.")
        return 2
    shown = [s for s in states if s.matters]
    data = [{"system": s.system, "name": s.name, "esde": s.launch.esde, "current": s.launch.current,
             "default": s.launch.default, "working": s.launch.working,
             "choices": [c.label for c in s.launch.choices], "per_game": s.launch.per_game,
             "games": s.games, "listed": s.listed, "fix": s.fix, "grant": s.grant} for s in shown]
    lines = []
    for s in shown:
        line = f"{s.name:<22} {describe(s)}"
        if s.fix:
            line += f'\n{"":<22} Fix: jewelcase esde use {s.system} "{s.fix}"'
        if s.grant:
            line += f'\n{"":<22} {s.grant}'
        lines.append(line)
    out(args, data, "\n".join(lines) or "ES-DE has none of the systems on this device.")
    return 0
