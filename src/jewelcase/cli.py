"""Command line: the same engine the window uses, for scripts and for agents."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .core import catalog, integrate, smartlists
from .core.catalog import GameRow
from .core.fmt import human_size, human_time
from .core.plan import SyncPlan, with_copy_first
from .core.session import Session, open_session


def row_dict(r: GameRow) -> dict:
    return {"key": f"{r.system}/{r.stem}", "title": r.title, "system": r.system, "state": r.state,
            "size": r.size, "plays": r.plays, "seconds": r.seconds,
            "last_played": r.last_played.isoformat() if r.last_played else None,
            "on_device": r.on_device, "want": r.want}


def plan_dict(p: SyncPlan) -> dict:
    def items(lst):
        return [{"key": f"{i.row.system}/{i.row.stem}", "title": i.row.title, "bytes": i.bytes,
                 "files": list(i.files), "convert": i.convert.archive if i.convert else None,
                 "cover": (i.row.system, i.row.stem) in p.covers} for i in lst]
    return {"add": items(p.adds), "remove": items(p.removes), "copy_to_library": items(p.pulls),
            "skipped": [{"key": f"{r.system}/{r.stem}", "reason": why} for r, why in p.skipped],
            "free_before": p.free_before, "free_after": p.free_after, "seconds": p.seconds,
            "fits": p.fits, "warnings": p.warnings}


def _out(args, data, text: str) -> None:
    print(json.dumps(data, indent=2) if args.json else text)


def _find(rows: list[GameRow], keys: list[str]) -> list[GameRow]:
    by_key = {f"{r.system}/{r.stem}": r for r in rows}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise SystemExit("No such game: " + ", ".join(missing))
    return [by_key[k] for k in keys]


def _smart(s: Session, rows: list[GameRow], text: str) -> list[GameRow]:
    """The games of a list named by id or by name: a built-in one or one of the owner's."""
    found = smartlists.resolve(s.cfg, text)
    if found is None:
        names = [*catalog.SMART_LISTS, *(u.id for u in s.cfg.user_lists)]
        raise SystemExit(f"No such list: {text}. Lists: {', '.join(names)}")
    return catalog.smart(rows, found, s.cfg)


# ---------------------------------------------------------------- commands
def cmd_lists(s: Session, args) -> int:
    rows = s.rows()
    entries = [(sid, sl.title, True) for sid, sl in catalog.SMART_LISTS.items()]
    entries += [(u.id, u.name, False) for u in s.cfg.user_lists]
    data = [{"id": i, "name": n, "builtin": b, "count": len(catalog.smart(rows, i, s.cfg))} for i, n, b in entries]
    _out(args, data, "\n".join(f"{d['count']:>5}  {d['name']}  ({d['id']})" for d in data))
    return 0


def cmd_status(s: Session, args) -> int:
    rows = s.rows()
    info, card = s.info(), s.storage()
    plan = s.plan(rows)
    counts = {}
    for r in rows:
        counts[r.state] = counts.get(r.state, 0) + 1
    data = {
        "device": {"connected": s.device is not None, "serial": s.serial, "problem": s.problem,
                   **({k: v for k, v in asdict(info).items() if k != "serial"} if info else {})},
        "card": asdict(card) if card else None,
        "internal": asdict(s.storage("internal")) if s.storage("internal") else None,
        "library": {"path": str(s.cfg.library) if s.cfg.library else None, "online": s.library.online},
        "games": counts, "pending": {"add": len(plan.adds), "remove": len(plan.removes)},
    }
    lines = []
    if info:
        where = "connected" if s.device else "not connected (last known state)"
        if info.transport == "card":
            lines.append(f"{info.model} · card or folder · {where}")
        else:
            lines.append(f"{info.model} · {info.battery}% · power: {info.power} · {info.transport} · {where}")
        if s.problem and not s.device:
            lines.append(s.problem)
    else:
        lines.append("No device" + (f" — {s.problem}" if s.problem else ""))
    if card:
        lines.append(f"Card: {human_size(card.free)} free of {human_size(card.total)}")
    lines.append(f"Library: {s.cfg.library or 'not set'}" + ("" if s.library.online else " (offline)"))
    lines.append("Games: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(counts.items())))
    lines.append(f"Pending: + {len(plan.adds)}  − {len(plan.removes)}")
    _out(args, data, "\n".join(lines))
    return 0


def cmd_games(s: Session, args) -> int:
    rows = s.rows()
    if args.system:
        known = sorted({r.system for r in rows} | set(s.library.systems()))
        if args.system not in known:
            raise SystemExit(f"No such system: {args.system}. Systems: {', '.join(known)}")
    if args.list:
        rows = _smart(s, rows, args.list)
    if args.system:
        rows = [r for r in rows if r.system == args.system]
    text = "\n".join(f"[{'x' if r.want else ' '}] {r.system:<12} {human_size(r.size):>9} "
                     f"{human_time(r.seconds):>7}  {r.state:<16} {r.title}  ({r.stem})" for r in rows)
    _out(args, [row_dict(r) for r in rows], text or "No games.")
    return 0


def cmd_tick(s: Session, args, want: bool) -> int:
    rows = s.rows()
    if getattr(args, "list", None):
        chosen = _smart(s, rows, args.list)
    else:
        chosen = _find(rows, args.keys)
    for r in chosen:
        s.store.set_wanted(s.serial, r.system, r.stem, None if want == r.on_device else want)
    _out(args, {"changed": len(chosen)}, f"{'Ticked' if want else 'Unticked'} {len(chosen)} games.")
    return 0


def _plan_text(p: SyncPlan) -> str:
    lines = []
    for title, items, sign in (("Copy to library", p.pulls, "→"), ("Remove", p.removes, "−"), ("Add", p.adds, "+")):
        if items:
            lines.append(f"{title} ({len(items)} games, {human_size(sum(i.bytes for i in items))}):")
            lines += [f"  {sign} {i.row.system:<12} {human_size(i.bytes):>9}  {i.row.title}"
                      + (f" — converted from {i.convert.archive} first" if i.convert else "")
                      + (" — its cover is copied to ES-DE if it has none" if i.row.key in p.covers else "")
                      for i in items]
    if p.skipped:
        lines.append("Skipped:")
        lines += [f"  ! {r.system:<12} {r.title} — {why}" for r, why in p.skipped]
    lines.append(f"Free space: {human_size(p.free_before)} now, {human_size(p.free_after)} after. "
                 f"About {max(1, p.seconds // 60)} min.")
    lines += p.warnings
    if not p.fits:
        lines.append("This does not fit on the card." if p.have_device else "No device connected.")
    return "\n".join(lines)


def cmd_plan(s: Session, args) -> int:
    p = s.plan()
    _out(args, plan_dict(p), "Nothing to do." if p.empty and not p.skipped else _plan_text(p))
    return 0


def _run_job(s: Session, args, start, resumed: bool | None = None, then_saves: bool = False) -> int:
    from .core.sync import Busy, JobOpen

    def show(e):
        if not args.json and e.state != "running":
            print(f"  [{e.state}] {e.label} {e.message}", file=sys.stderr)
    runner = s.runner(on_progress=show)
    try:
        job = start(runner)
        state = runner.run(job, resumed)
    except JobOpen:
        print(UNFINISHED)
        return 2
    except Busy as e:
        print(e)
        return 2
    steps = s.store.job_steps(job)
    failed = [st for st in steps if st.state == "failed"]
    data = {"state": state, "steps": len(steps),
            "failed": [{"kind": st.kind, **st.payload, "message": st.message} for st in failed]}
    text = f"Sync {state}: {len(steps) - len(failed)} of {len(steps)} steps succeeded."
    for st in failed:
        what = st.payload.get("name") or st.payload.get("stem") or st.payload.get("system", "")
        text += f"\n  failed: {st.kind} {what} — {st.message}"
    esde_note = next((st.message for st in steps if st.kind == "refresh" and st.state == "done"), "")
    if esde_note:
        data["esde"] = esde_note
        text += "\n" + esde_note + ("" if esde_note.endswith(".") else ".")
    text += f"\nLog: {s.log_path}"
    if then_saves and state == "done" and not s.is_card:      # a card has no emulators, so no saves; a sync does not touch saves; this only keeps the backup fresh
        from .core import saves
        data["saves"], line = saves.backup_after_sync(s)
        text += "\n" + line
    _out(args, data, text)
    return 0 if state == "done" and not failed else 1


UNFINISHED = ("An earlier sync was not finished. Run `jewelcase sync --resume` to carry on with it, "
              "or `jewelcase sync --discard` to drop what is left of it.")


def cmd_sync(s: Session, args) -> int:
    open_job = s.store.open_job(s.serial) if s.serial else None
    if open_job is not None:
        if args.discard:
            s.store.close_job(open_job, "cancelled")
            print("Discarded the unfinished sync. Your ticks are unchanged.")
            return 0
        if not args.resume:
            print(UNFINISHED)
            return 2
        if s.device is None:
            print("No device connected.")
            return 2
        return _run_job(s, args, lambda runner: open_job, resumed=True, then_saves=True)
    if args.resume or args.discard:
        print("There is no unfinished sync.")
        return 0
    p = s.plan()
    if args.copy_first:
        p = with_copy_first(p, {r.key for r, why in p.skipped if why == "no library copy"})
    if p.empty:
        _out(args, {"state": "nothing"}, "Nothing to do.")
        return 0
    if not args.yes:
        print(_plan_text(p))
        print("\nRun again with --yes to do this.")
        return 2
    if s.device is None:
        print("No device connected." + (f" {s.problem}" if s.problem else ""))
        return 2
    if not p.fits:
        print(_plan_text(p))
        return 2
    return _run_job(s, args, lambda runner: runner.start(p), then_saves=True)


def cmd_copy(s: Session, args) -> int:
    if s.device is None:
        print("No device connected.")
        return 2
    rows = [r for r in _find(s.rows(), args.keys) if r.on_device]
    return _run_job(s, args, lambda runner: runner.copy_to_library(rows))


def cmd_device(s: Session, args) -> int:
    """Show the device and its games folder; --games-folder sets it, --detect goes back to detection."""
    from .core import gamesfolder
    if args.games_folder is not None and args.detect:
        print("Use either --games-folder or --detect, not both.")
        return 2
    changed = ""
    if args.games_folder is not None or args.detect:
        try:
            if args.detect:
                gamesfolder.detect_again(s)
                changed = "Detected the games folder again."
            else:
                gamesfolder.set_folder(s, args.games_folder)
                changed = f"The games folder is now {s.profile.rom_root}."
        except gamesfolder.FolderError as e:
            print(e)
            return 2
        s.refresh()
    info, p = s.info(), s.profile
    how = gamesfolder.source(s) if s.serial else "detected"
    data = {"connected": s.device is not None, "serial": s.serial,
            "model": info.model if info else None, "kind": "card" if s.is_card else "handheld",
            "games_folder": p.rom_root if p else None, "games_folder_source": how if p else None,
            "game_lists": p.gamelists_dir if p else None}
    lines = [f"{info.model} · {'connected' if s.device else 'not connected (last known state)'}" if info
             else "No device" + (f" — {s.problem}" if s.problem else "")]
    lines.append(f"Games folder: {p.rom_root} ({'set by you' if how == 'set' else 'detected'})" if p
                 else "Games folder: not found")
    if p:
        lines.append("Game lists: " + (p.gamelists_dir or "none (play history is not available)"))
    if changed:
        lines.insert(0, changed)
    _out(args, data, "\n".join(lines))
    return 0


def cmd_gui(s: Session, args) -> int:
    from .ui.app import run
    return run(s, screenshot=getattr(args, "screenshot", None), selftest=getattr(args, "selftest", False),
               view=getattr(args, "view", None), theme=getattr(args, "theme", None))


def cmd_install_menu_entry(args) -> int:
    """Add the AppImage to the applications menu, or repair its entry and icon; the demo only names them."""
    desktop, icon = integrate.menu_entry_paths()
    if args.demo or args.demo_dir:
        written, said = False, "Demo: nothing was written."
    else:
        exe = integrate.appimage_path()
        if exe is None:
            said = "Only the AppImage can add itself to the applications menu."
            _out(args, {"written": False, "error": said}, said)
            return 2
        try:
            integrate.install_menu_entry(exe)
        except OSError as err:
            said = _not_added(err, desktop, icon)
            _out(args, {"written": False, "error": said}, said)
            return 2
        written, said = True, "Jewelcase was added to the applications menu."
    _out(args, {"written": written, "desktop": str(desktop), "icon": str(icon)}, f"{said}\n  {desktop}\n  {icon}")
    return 0


def _not_added(err: OSError, desktop: Path, icon: Path) -> str:
    """The sentence for a menu entry that could not be written: where, and the system's reason."""
    where = Path(err.filename) if err.filename else desktop.parent
    if where.parent in (desktop.parent, icon.parent):
        where = where.parent        # the entry, the icon or a temporary name of theirs: name the folder they go in
    return (f"Jewelcase could not be added to the applications menu: {where} could not be written "
            f"({err.strerror or err}).")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="jewelcase", description="Manage games and emulators on an Android handheld.")
    ap.add_argument("--version", action="version", version=f"jewelcase {__version__}")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--demo", action="store_true", help="use a made-up library and device")
    ap.add_argument("--demo-dir", metavar="DIR", help="keep the demo world in DIR between runs")
    ap.add_argument("--library", metavar="PATH", help="library folder for this run")
    ap.add_argument("--card", metavar="PATH", help="use this mounted SD card or folder as the device "
                                                   "(a handheld connected with adb is used instead if there is one)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="device, storage and library at a glance")
    g = sub.add_parser("games", help="list games")
    g.add_argument("--list", metavar="LIST", help="a smart list, by id or name (`jewelcase lists` shows them)")
    g.add_argument("--system")
    t = sub.add_parser("tick", help="mark games to be on the device (KEY is system/stem)")
    t.add_argument("keys", nargs="*", metavar="KEY")
    t.add_argument("--list", metavar="LIST", help="a smart list, by id or name (`jewelcase lists` shows them)")
    u = sub.add_parser("untick", help="mark games to leave the device")
    u.add_argument("keys", nargs="*", metavar="KEY")
    u.add_argument("--list", metavar="LIST", help="a smart list, by id or name (`jewelcase lists` shows them)")
    sub.add_parser("lists", help="the smart lists, built-in and your own, with their counts")
    sub.add_parser("plan", help="show what a sync would do")
    sy = sub.add_parser("sync", help="carry out the plan")
    sy.add_argument("--yes", action="store_true")
    sy.add_argument("--resume", action="store_true", help="carry on with a sync that was interrupted")
    sy.add_argument("--discard", action="store_true", help="drop what is left of an interrupted sync")
    sy.add_argument("--copy-first", action="store_true",
                    help="copy device-only games to the library before removing them")
    c = sub.add_parser("copy-to-library", help="copy games from the device into the library")
    c.add_argument("keys", nargs="+", metavar="KEY")
    sub.add_parser("emulators", help="list emulators on the device").add_argument(
        "--check-updates", action="store_true")
    sub.add_parser("inbox", help="list new downloads that look like games")
    cc = sub.add_parser("covers", help="where each game's cover comes from; --download fetches missing ones")
    cc.add_argument("--download", action="store_true", help="download covers for games that have none")
    cc.add_argument("--push", action="store_true",
                    help="copy covers to ES-DE on the handheld for games there that lack one")
    cv = sub.add_parser("convert", help="make the playable file of archive-only games now, beside the archive")
    cv.add_argument("keys", nargs="+", metavar="KEY")
    sv = sub.add_parser("saves", help="what is backed up; back up and restore game saves")
    svs = sv.add_subparsers(dest="saves_cmd")
    svs.add_parser("backup", help="copy changed saves from the device to the library now")
    svs.add_parser("versions", help="dated versions of one save").add_argument("key", metavar="KEY")
    svs.add_parser("devices", help="the handhelds whose saves are backed up in the library")
    rs = svs.add_parser("restore", help="put saves back on the device (the ones there are backed up first)")
    rs.add_argument("keys", nargs="*", metavar="KEY")
    rs.add_argument("--at", metavar="RUN", help="as of this backup run (default: the newest copies)")
    rs.add_argument("--emulator", metavar="NAME", help="all saves of one emulator")
    rs.add_argument("--all", action="store_true", help="every save")
    rs.add_argument("--from", dest="source", metavar="MODEL-SERIAL",
                    help="restore from another handheld's backups (`jewelcase saves devices` lists them)")
    rs.add_argument("--yes", action="store_true")
    es = sub.add_parser("esde", help="which emulator ES-DE opens each system's games with")
    ess = es.add_subparsers(dest="esde_cmd")
    use = ess.add_parser("use", help="choose the emulator for a system (ES-DE is restarted)")
    use.add_argument("system", metavar="SYSTEM")
    use.add_argument("label", metavar="LABEL", help="the emulator as ES-DE names it, as `jewelcase esde --json` lists")
    ess.add_parser("fix", help="switch systems whose emulator is not installed to one that is")
    dv = sub.add_parser("device", help="the device and its games folder; correct it when detection got it wrong")
    dv.add_argument("--games-folder", metavar="PATH", help="use this folder on the device as the games folder")
    dv.add_argument("--detect", action="store_true", help="forget the folder you set and detect it again")
    sub.add_parser("bios", help="which BIOS files are on the handheld, per system (it only looks)")
    sub.add_parser("install-menu-entry", help="add Jewelcase to the applications menu, or repair its entry and icon")
    gui = sub.add_parser("gui", help="open the window (the default)")
    gui.add_argument("--screenshot", metavar="PNG")
    gui.add_argument("--selftest", action="store_true")
    gui.add_argument("--view")
    gui.add_argument("--theme", choices=("light", "dark"))
    return ap


def main(argv: list[str] | None = None) -> int:
    integrate.use_host_libraries()                 # first, before any thread or program is started
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "gui"
    if cmd == "install-menu-entry":
        return cmd_install_menu_entry(args)        # no session: it needs no device, library or settings
    s = open_session(demo_dir=args.demo_dir, use_demo=args.demo, library=args.library, card=args.card)
    if cmd == "gui":
        return cmd_gui(s, args)
    if s.cfg.unreadable:
        print(s.cfg.UNREADABLE_TEXT, file=sys.stderr)
    s.connect()
    s.refresh()
    try:
        if cmd == "status":
            return cmd_status(s, args)
        if cmd == "device":
            return cmd_device(s, args)
        if cmd == "games":
            return cmd_games(s, args)
        if cmd in ("tick", "untick"):
            if not args.keys and not args.list:
                print("Name games or use --list.")
                return 2
            if args.keys and args.list:
                print("Name games or use --list, not both.")
                return 2
            return cmd_tick(s, args, cmd == "tick")
        if cmd == "lists":
            return cmd_lists(s, args)
        if cmd == "plan":
            return cmd_plan(s, args)
        if cmd == "sync":
            return cmd_sync(s, args)
        if cmd == "copy-to-library":
            return cmd_copy(s, args)
        if (cmd in ("emulators", "saves", "esde") or (cmd == "covers" and getattr(args, "push", False))) \
                and s.is_card:
            print("Not available for a card.")
            return 2
        if cmd == "emulators":
            from .core.emulators import cmd_emulators
            return cmd_emulators(s, args, _out)
        if cmd == "convert":
            from .core.convert import cmd_convert
            return cmd_convert(s, args, _out)
        if cmd == "covers":
            from .core.covers import cmd_covers
            return cmd_covers(s, args, _out)
        if cmd == "inbox":
            from .core.inbox import cmd_inbox
            return cmd_inbox(s, args, _out)
        if cmd == "saves":
            from .core.saves import cmd_saves
            return cmd_saves(s, args, _out)
        if cmd == "bios":
            from .core.bios import cmd_bios
            return cmd_bios(s, args, _out)
        if cmd == "esde":
            from .core.esde import cmd_esde
            return cmd_esde(s, args, _out)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code)
            return 2
        raise
    return 2


if __name__ == "__main__":
    sys.exit(main())
