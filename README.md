# Jewelcase

A desktop app for Linux that manages an Android gaming handheld the way iTunes managed an iPod:
your game library on the PC, the handheld plugged in, tick what belongs on it, press **Sync**.

Built for and tested on an Anbernic RG477V running ES-DE. Nothing in it is specific to that device:
any Android handheld that keeps ROMs in per-system folders should work.

Jewelcase was written entirely by Claude, Anthropic's AI model. A person decided what it should do and
tried it on their own handheld; they wrote none of the code. [How it was built](docs/how-this-was-built.md).

## What it does

- **Games** — one list of everything in your library and on the device, with size, play count, play
  time and last played. A tick means "should be on the device". Tick and untick, review, Sync.
- **Posters** — the same games as a grid of box covers instead of a list (the two buttons beside the
  search field, or Ctrl+1 and Ctrl+2), and a small cover at the start of each row in the list. Ticking,
  selecting, searching and the menu work the same in both. A system's page is headed by the system's image.
- **Smart lists** — Never Played, Not Played Lately, Most Played, Largest on Device, Device Only, and
  your own: "New Smart List…" at the end of that section. "Remove what I never played" is: open
  *Never Played*, select all, press Space, Sync.
- **Inbox** — new downloads that look like games, with the system worked out from the file itself.
  Import moves them into the library and queues them for the device, with a progress bar that covers
  both copying each file and verifying the copy.
- **Conversion** — a game that arrives as an archive is unpacked when it is imported (and made into a
  CHD or RVZ where the handheld uses those); the result is kept beside the archive. An archive already in
  the library can be ticked like any other game and is converted by the sync.
- **Emulators** — what is installed and which version; install, update, disable or remove. Which
  emulator ES-DE opens each system's games with, and a warning when an emulator has not been given its
  games folder yet.
- **BIOS** — whether the BIOS files the emulators need are on the handheld, and the right ones.
- **Saves** — saves, save states and emulator settings are copied to the library whenever the handheld
  is connected, earlier versions are kept, and one save, one emulator's saves or everything can be put
  back — also onto a different handheld.
- **Device** — battery, power source, storage, and how the last sync went. The games folder is shown
  with how it was chosen, and **Change…** corrects it. A mounted SD card or folder can be the device too.

## Install

Download `Jewelcase-x86_64.AppImage` from this repository's Releases page, make it executable, run it:

```sh
chmod +x Jewelcase-x86_64.AppImage
./Jewelcase-x86_64.AppImage
```

It needs `adb`. If `adb` is not installed, the Device page offers to download Google's
platform-tools into Jewelcase's own data folder. `7z`, `chdman` (from MAME's tools) and `dolphin-tool`
(from Dolphin) are optional: without `7z`, archives cannot be inspected, converted or used as proof of a
library copy; without `chdman` or `dolphin-tool`, a disc image is unpacked as it is instead of being made
into a CHD or an RVZ.

`./Jewelcase-x86_64.AppImage install-menu-entry`, or **Add to Applications Menu** in the window's menu,
adds Jewelcase to the applications menu. It writes `~/.local/share/applications/jewelcase.desktop` and
`~/.local/share/icons/hicolor/scalable/apps/jewelcase.svg`, and refreshes the icon cache with
`gtk-update-icon-cache` when that is installed. Run it again after moving the AppImage, or when the icon
shows broken, and it repairs both.

On the handheld, switch on **USB debugging** (Developer options) and tap **Allow** when it asks.

The AppImage is built on Ubuntu 22.04 and checked on Ubuntu 22.04 and 24.04, Debian 12 and 13,
Fedora, Arch, and openSUSE Leap 15.6 and Tumbleweed (`packaging/check-distros.sh`).

**Flatpak (built and checked, not yet in a release).** `packaging/build-flatpak.sh` needs `flatpak-builder`, the
freedesktop 26.08 SDK and the AppImage built first; it writes `dist/Jewelcase.flatpak`, and
`packaging/check-flatpak.sh` starts that bundle in a throwaway installation. The app id is
`dev.luismcastaneda.Jewelcase`. Inside a Flatpak, settings and data live under `~/.var/app/<id>/`, adb is downloaded into
the app's data folder, and `7z` and `chdman` are not available. Details: `packaging/flatpak/README.md`.

## First run

There is no setup dialog. With no library chosen yet, the window opens on a welcome screen that
lists the game folders it found and offers **Choose Library Folder…**. It expects one folder per
system (`psx/`, `ps2/`, `gba/` …), named the way ES-DE names them. Scanning reads names and sizes only.

When a device connects, Jewelcase finds its ROM folder and ES-DE's game lists by itself and shows what
it found on the device page.

## How it behaves

Jewelcase follows the [elementary Human Interface Guidelines](https://docs.elementary.io/hig):

- **Undo instead of warnings.** Ticking many games, importing downloads, disabling an emulator or
  discarding an unfinished sync happens at once and offers **Undo** in a toast. It only asks first
  where there is no way back: removing an emulator, and the review before a sync.
- **Feedback in the right place.** A toast confirms what you just did; a bar under the header says
  what needs attention (an interrupted sync, the library disk missing, changes that do not fit);
  a notification tells you when a sync finishes while you are elsewhere.
- **Closing does not lose work.** Close the window during a sync and it finishes in the background,
  notifies you, then quits. Start Jewelcase again meanwhile and the same window comes back.
- **Preferences apply as you change them**; there is nothing to confirm.
- **It remembers** its size, the view you were in, list or grid, the sort order and which sidebar
  sections are collapsed.
- **One long job at a time.** A sync, an import that converts, and "Convert to CHD" exclude each other,
  also across Jewelcase windows and commands; the buttons of the others are off and say why. Closing the
  window during any of them lets it finish in the background, then Jewelcase quits.
- **A settings file that cannot be read is kept.** Jewelcase then starts with the defaults, says so, and
  keeps the unreadable file as `config.toml.broken` before it saves anything.

## The rules it follows

These are the same rules that were worked out by hand before the app existed.

- **Nothing leaves the device without a verified copy in the library.** Before deleting a file,
  Jewelcase proves the library has the same bytes: a hash it already checked, or hashing both sides, or
  hashing the file inside a `.7z`/`.zip` of the same name. If it cannot prove it, the game stays.
  For a game that exists only on the device, the review sheet offers to copy it to the library first.
- **Nothing happens before you see it.** Sync opens a review of every game to add and remove.
- **Every copy is checked.** A file is pushed under a temporary name, hashed on the device, and only
  then given its real name. It never overwrites a different file that is already there.
- **Play history is kept.** Play counts and times are read from ES-DE and stored on the PC, so they
  survive a game leaving the device.
- **A sync can be interrupted.** Unplug the handheld to play and plug it back in: the sync waits and
  carries on. Removals also wait while a game is running on the device. If the device goes away
  while the window is closed, the sync pauses; next time a bar offers **Review and Resume…**, which
  shows what is left before doing anything. Resuming proves every library copy again, and respects
  ticks you changed in the meantime.
- **A remembered hash is only trusted while the file is unchanged.** If a file's size or
  modification time differs from when it was hashed, on the device or in the library, it is hashed
  again before anything is deleted.
- **One sync at a time.** A second window or command refuses to start while one is running.
- **A game is all of its files.** A `.cue` with its tracks, an `.m3u` with its discs. A file that
  another game on the device still uses is kept.
- **The library disk is treated gently.** No background hashing; a file is hashed when a sync needs
  it, once, and the result is remembered until the file changes.
- **It stays in its lane on the device.** It writes only inside the ROM folders, ES-DE's game lists,
  ES-DE's own systems file (each backed up first) and ES-DE's covers folder (adding a cover that is
  missing, never replacing one), and installs or removes apps when you ask.
- **The network is used only when you ask**: to check an emulator's own release page, to download
  an APK from it, to fetch adb, and, if you switch it on, to download covers. Requests carry nothing
  about you.


## Posters

Jewelcase shows the pictures ES-DE already has. It downloads covers only if you switch that on.

- **Covers** are the ones ES-DE's scraper saved on the handheld (`ES-DE/downloaded_media/<system>/covers`).
  When the handheld is connected, new and changed covers are copied to the PC, so the grid also works
  while the handheld is away. A game ES-DE has no cover for gets a plain tile with its title; scrape it
  in ES-DE and the cover appears here the next time the handheld is read.
- **System images** come from the theme ES-DE is set to, or else from the theme built into ES-DE.
- Games that are not on the handheld are drawn paler.
- The pictures are kept in `~/.cache/jewelcase/art/`. Deleting that folder loses nothing: ES-DE's covers
  fill in again from the handheld, and downloaded ones are fetched again if downloading is switched on.
- **In the list**, each game's cover is shown small at the start of its title; a game without one gets
  a card in its system's colour.
- **Downloaded covers (off by default).** Preferences, *Download covers for games that have none*, fetches
  missing covers in the background after the handheld or library is read; *Download Missing Covers* in the
  header menu does it once; `jewelcase covers --download` does it from the command line. They come from the
  libretro thumbnail collection on GitHub: each request names the game and its system, nothing else. A
  game with no picture under its name is asked for again after 30 days. Downloaded covers are kept in
  `~/.cache/jewelcase/art/downloaded/`, apart from ES-DE's.
- **What a download has to be.** Https only (a redirect that leaves https is refused), at most 5 MB, and
  a complete PNG or JPEG; anything else is not kept. A network failure is not counted as "no cover": the
  run stops and automatic downloads wait an hour, while the menu entry and the command always try.
- **Covers for new games.** When a sync adds a game that has no cover in ES-DE on the handheld and Jewelcase
  has one, the cover is copied to `ES-DE/downloaded_media/<system>/covers`, checked before it gets its
  name. Nothing is ever replaced there, and a cover that fails never stops the game from being added. The
  review says which games get one. `jewelcase covers --push` does the same for games already on the handheld.

## Conversion

The library can keep a game as the archive it was downloaded as. The handheld needs the game itself.

- **When it happens.** Importing an archive from the Inbox converts it right after it lands, so a later
  sync only copies (Preferences: *Convert archives when they are imported*, on by default). If that
  conversion fails the archive is still imported and Jewelcase says why. Undo of the import also removes what
  the conversion made, as long as it is unchanged.
- **Tick it like any other game.** A game the library has only as a `.7z`, `.rar` or `.zip` shows as
  *Archive in library*. Ticked, it is part of the next sync, and the review says it is converted first.
- **What it becomes.** The form the handheld's games of that system are kept in: a CHD for PlayStation 2,
  PlayStation, Saturn, Dreamcast and Sega CD discs (a DVD-type CHD for a PS2 `.iso`); an `.rvz` for a
  GameCube or Wii `.iso` when `dolphin-tool` is installed; the unpacked `.iso` for PSP and PlayStation 3.
  Text files in the archive are left out, and the game is named after its archive (`Game (USA).dec.iso`
  inside `Game (USA).7z` becomes `Game (USA).iso`).
- **Several discs.** An archive with two or more discs, on a system that takes `.m3u` playlists, is
  converted disc by disc and gets a playlist named after the archive listing the discs in order; the
  library shows one game. All files arrive or none.
- **What a multi-disc archive must be.** Every disc file has to be named by a cue or gdi sheet in the
  archive and every track a sheet names has to be there; otherwise the archive is refused with the names,
  never converted with a piece missing.
- **A disc image you already have.** A game the library keeps as a loose `.iso`, `.cue` (with its tracks)
  or `.gdi` can be made into a CHD (an `.rvz` for GameCube and Wii) on request: *Convert to CHD* in the
  games menu, or `jewelcase convert "ps2/Some Game (USA)"`. The original is kept; a sync never does this by
  itself.
- **Where it goes.** Into the library, beside the archive, and it stays there: the library then has the
  archive and the playable game, and the handheld's copy has an ordinary twin. The archive is not changed.
- **Checked.** An unpacked file is read back and compared with what came out of the archive; a CHD has
  to pass `chdman verify`. Until then the file carries a temporary name, so a conversion that fails or is
  paused leaves nothing behind.
- **Room.** The library needs room for the converted game. Making a CHD also unpacks the disc image
  into `~/.cache/jewelcase/convert` for as long as it takes.
- **Not yet.** An archive that already holds an `.m3u`; a multi-disc game the library already has as a
  playlist of loose discs. The RVZ path has only been run against a stand-in for `dolphin-tool`.

## ES-DE

ES-DE already knows the emulators; what it can get wrong is which one it launches, and where it looks.

- **Which emulator.** The Emulators page lists, per system, the emulator ES-DE opens games with, and
  lets you pick another installed one. If the one ES-DE would use is not installed, a bar offers the
  fix. Installing an emulator fills such a gap by itself (with **Undo**); a system that already works
  is never switched. Games that carry their own choice in ES-DE keep it.
- **Where it looks.** On a handheld whose ES-DE has its systems pointed at the card one by one, a
  system that is not in that list is added when a sync first puts a game of it on the card.
- **Restart.** ES-DE reads its files only when it starts, so Jewelcase restarts it after a sync that
  changed games and after changing an emulator. Never while a game is running: a sync then says that
  ES-DE shows the changes the next time it starts, and an emulator change waits for you to close the game.
- **Several changes, one restart.** *Fix All*, its Undo, and the automatic fix after an emulator is
  installed restart ES-DE once, not once per system. All changes are checked before anything is written;
  if one cannot be made, none is.
- **If the handheld drops part-way** through changing several systems, Jewelcase says which were already
  switched and which were not, and the Emulators page shows the real state.
- **Games folder access.** Some emulators are handed a game as an Android document link, which Android
  only lets them open after they were given that folder through their own folder picker. Jewelcase reads
  which folders each app holds and, for systems with games on the handheld or ticked to go there, says
  so: "aPS3e has not been given the PlayStation 3 folder yet. Open aPS3e on the handheld and choose that
  folder in the app." It shows on the Emulators page and in `jewelcase esde`. Jewelcase cannot grant
  the folder itself, and says nothing when the list of grants cannot be read.

## BIOS

Many emulators need a console's BIOS or firmware file. Jewelcase only looks: it never copies, downloads
or names a source for one.

- **Where it looks.** RetroArch's system folder (on internal storage and on the card), DuckStation's
  bios folder, folders with "bios" in their name, and installed emulators' own folders.
- **What it shows.** Per system that needs something: whether it is needed or optional, and whether it
  is *Verified* (a known checksum, whatever the file is called), *Incomplete* (the system needs several
  different files, as the Dreamcast and the Nintendo DS do, and only some are there; the missing names
  are listed), *Found, Not Recognised* (a BIOS-like name and size with an unknown checksum, as with many
  valid PS2 dumps) or *Missing*. Systems with games on the handheld come first. PlayStation 3 shows
  *Installed* when aPS3e's firmware folder can be seen.
- **When it looks.** Once per connection and when you press **Check Again**; never during a sync.
- **What it cannot know.** Which folder each emulator is set to read: each emulator still has to be
  pointed at the folder in its own settings.
- The list of names, sizes and checksums comes from libretro's public `System.dat`
  ([`src/jewelcase/data/bios.toml`](src/jewelcase/data/bios.toml)); add your own in `~/.config/jewelcase/bios.toml`.

## A card or a folder as the device

Some handhelds are not Android, and an Android handheld's card can be read in a card reader. Mount the
card and give Jewelcase its folder: **Use a Card or Folder…** on the Device page (shown when no handheld is
connected), `jewelcase --card /run/media/you/SDCARD <command>`, or `card = "…"` in the settings file.
**Stop Using Card** undoes it. A connected adb handheld is used instead when there is one.

- Games and ES-DE game lists are found as on a handheld (`/`, `/ROMs`, `/roms`, `/Roms`;
  `/ES-DE/gamelists`). Without ES-DE, sync works and the play columns stay empty.
- Everything the sync guarantees still applies: verified copies, no overwriting, pause and resume, proof
  before deletion. Copied files are flushed to the card and read back from it.
- A card has no apps: Emulators and Saves are hidden, there is no battery, and ES-DE is not restarted.
- A card is told apart by its filesystem's id, and Jewelcase checks before every step that the same card
  is still there: a pulled card whose mount folder stays behind, or another card at the same place,
  counts as gone, so nothing is written to the PC's own disk. Nothing is written to the card to mark it.
- Nothing outside the card is touched: a link on the card that leads out of it is never followed.
- An empty, freshly formatted card says what to do: make a folder named `ROMs` on it.
- `--card` and `--library` on the command line apply to that run only and are not saved.

## The games folder

Jewelcase detects the games folder itself. If it picked the wrong one, **Change…** on the Device page lists
the folders it considered and lets you type another path. The choice is kept for that device; **Detect
Again** goes back to automatic. A path that does not exist or holds no system folders is refused with
the reason, and the folder cannot be changed while a sync is unfinished. `jewelcase device` shows it.

## Saves

Games can be downloaded again; progress cannot. Jewelcase keeps copies of every save it can read.

- **When.** When the handheld connects, every hour while it stays connected, after each sync, before
  an emulator is removed, and when you press **Back Up Now**. Only changed files are copied, and each
  copy is compared with the device by hash.
- **Where.** `<library>/_saves/<model>-<serial>/latest/…` holds the newest copy of each file in the
  emulator's own folder shape; `…/history/…` holds the versions it replaced. They are plain files, so
  a save can also be put back by hand.
- **Emulator settings.** The settings files of RetroArch (with its per-core options), ARMSX2, AetherSX2,
  PPSSPP, Dolphin, Dolphin MMJR, Flycast, Redream, Yaba Sanshiro 2, Citra MMJ, Vita3K and aPS3e are
  copied the same way and listed as **Settings**. Only those files, matched by name: no BIOS, keys,
  firmware, databases, caches or overlays. Note that a settings file can hold logins and keys you typed into
  the emulator (an achievements account, an online or streaming key), and it is copied as it is. Switch this off in
  Preferences with *Back up emulator settings*.
- **How many versions.** Per save file: the 10 newest, the last one of each day for 30 days, and the
  last one of each month for good. Save states keep 2 older versions, settings the 5 newest and one per day for 30 days.
- **What it never does.** It never deletes anything on the handheld, and a save that disappears from
  the handheld, or is replaced by an empty one, keeps its copies in the library.
- **Restore.** Pick a save on the Saves page (or **Restore Save…** on a game's row) and choose a dated
  version; or **Restore All…** for one emulator or everything, from the newest copies or as they were
  at an earlier backup. The save on the handheld is backed up first, so every restore has **Undo**.
  The emulator has to be closed on the handheld.
- **Another handheld.** To put one handheld's saves and settings onto another (a replacement, or the
  same one after a reflash changed its serial), choose **Restore All…** and pick the handheld under
  *Restore from handheld*, which appears when the library holds more than one set. What is on the
  connected handheld is backed up into its own set first, so **Undo** works. A file that names another
  SD card is pointed at this handheld's card only when that is unambiguous; anything else is skipped and
  the list says why.
- **What Undo covers.** The files that were on the handheld before the restore and that the safety
  backup captured. A file the handheld did not have before cannot be taken away again, because nothing
  on the handheld is ever deleted.
- **What it cannot read.** There is no root, so only shared storage is reachable, and Android also
  refuses adb some folders and files inside an app's own folder. The Saves page says which emulators
  are *Protected*, *Partly Protected*, *Not Protected* or *Not Reachable*. On the RG477V, PPSSPP's
  saves become readable once its memory stick is moved to `/sdcard/PSP` in PPSSPP's settings.
- **Saves named by a game's ID** (PlayStation 2 save states, PSP save folders, GameCube `.gci` files)
  are linked to their game. Jewelcase reads each game's ID from its own file in the library (a few KB of an
  `.iso` or DVD-type `.chd`; `.rvz` needs `dolphin-tool`), only when the Saves page or a game's menu asks,
  and once per file. A linked save shows the game's title beside it, and the game's menu offers
  **Restore Save…**. CD-type CHDs cannot be read this way; a save whose ID matches no game stays as it was.

More places can be added in `~/.config/jewelcase/saves.toml`, in the shape of
[`src/jewelcase/data/saves.toml`](src/jewelcase/data/saves.toml).

## Command line

The same engine without the window, for scripts and for AI agents. Add `--json` for machine output.

```sh
jewelcase status                                           # device, storage, library, pending changes
jewelcase games --list never_played                        # or --system ps2
jewelcase untick --list never_played                       # or: jewelcase untick "ps2/God of War II (USA)"
jewelcase tick "gba/Golden Sun (USA, Europe)"                # or: jewelcase tick --list never_played
jewelcase plan                                             # what a sync would do
jewelcase sync --yes                                       # do it (add --copy-first for device-only games)
jewelcase sync --resume                                    # carry on with a sync that was interrupted
jewelcase sync --discard                                   # or drop what is left of it
jewelcase copy-to-library "ps2/Some Game (USA)"
jewelcase emulators --check-updates
jewelcase inbox
jewelcase lists                                            # every smart list with its count; --list takes your own lists too
jewelcase covers                                           # where covers come from; --download fetches missing ones, --push adds them on the handheld
jewelcase bios                                             # which BIOS files are on the handheld, per system
jewelcase device                                           # the device and its games folder; --games-folder PATH, --detect
jewelcase --card /run/media/you/SDCARD status              # a mounted card or folder as the device
jewelcase convert "ps2/Some Game (USA)"                    # make the playable file of an archive-only game now, no sync
jewelcase esde                                             # which emulator ES-DE opens each system's games with
jewelcase esde use ps2 "ARMSX2 (Standalone)"               # change it (the name as `jewelcase --json esde` lists it)
jewelcase esde fix                                         # switch systems whose emulator is not installed to one that is
jewelcase saves                                            # what is backed up, per emulator
jewelcase saves backup                                     # copy changed saves to the library now
jewelcase saves versions KEY                               # dated versions of one save (KEY as `jewelcase saves` prints it)
jewelcase saves restore KEY --at RUN --yes
jewelcase saves restore --emulator ARMSX2 --yes            # or --all; add --at RUN for an earlier backup
jewelcase saves devices                                    # the handhelds whose backups are in the library
jewelcase saves restore --all --from RG477V-ABC123 --yes   # from another handheld's set
jewelcase --demo gui                                       # try the window on made-up games, no device needed
jewelcase --version                                        # which version this is, for a bug report
jewelcase install-menu-entry                               # add the AppImage to the applications menu, or repair its entry and icon
```

With the AppImage, the same commands work as `./Jewelcase-x86_64.AppImage status`, and so on.

## Where things live

| What | Where |
|---|---|
| Settings | `~/.config/jewelcase/config.toml` |
| Your own emulator entries | `~/.config/jewelcase/emulators.toml` |
| Your own save folders | `~/.config/jewelcase/saves.toml` |
| Your own BIOS entries | `~/.config/jewelcase/bios.toml` |
| Save backups | `<library>/_saves/<model>-<serial>/` |
| Database (index, hashes, play history, sync journal) | `~/.local/share/jewelcase/jewelcase.db` |
| Sync log | `~/.local/share/jewelcase/logs/sync.log` |
| Game list backups | `~/.local/share/jewelcase/backups/<serial>/` |
| Downloaded APKs | `~/.cache/jewelcase/apk/` |
| Covers and system images copied from ES-DE | `~/.cache/jewelcase/art/` |
| Downloaded covers | `~/.cache/jewelcase/art/downloaded/` |
| Disc images while a CHD is made | `~/.cache/jewelcase/convert/` |
| Applications menu entry | `~/.local/share/applications/jewelcase.desktop` |
| Its icon | `~/.local/share/icons/hicolor/scalable/apps/jewelcase.svg` |

Extra systems can be added to the settings file:

```toml
[systems.vectrex]
name = "Vectrex"
esde = "vectrex"
device_exts = [".vec", ".zip"]
color = "#556677"
```

## Working on it

```sh
uv sync
uv run jewelcase --demo gui               # the window on demo data
uv run pytest                             # everything that needs no device
uv run pytest -m device tests/device -s   # against a connected handheld
packaging/build-appimage.sh               # needs podman; writes dist/Jewelcase-x86_64.AppImage
packaging/check-distros.sh                # launches it on each distro
packaging/build-flatpak.sh                # needs flatpak-builder and the SDK; writes dist/Jewelcase.flatpak
packaging/check-flatpak.sh                # starts that bundle in a throwaway installation, on demo data
```

The device tests marked `readonly` never write to the handheld. The `scratch` test writes only
inside `_jewelcase_scratch` on the card and deletes that folder afterwards.

Layout: `src/jewelcase/core` is all the logic and never imports the window; `src/jewelcase/cli.py` and
`src/jewelcase/ui` are thin shells over it. The handheld is reached only through the `Device` interface
(`core/device.py`): `AdbDevice` is the real one, `FakeDevice` runs the tests and the demo.

## How it is looked after

Claude also does most of the upkeep. It reads every issue, answers it, and fixes small, clear problems
through pull requests that merge by themselves once the tests and an independent review pass. Larger
changes, anything about security or releases, and every pull request from a person wait for the
maintainer. [CONTRIBUTING.md](CONTRIBUTING.md) explains how; security problems go through
[SECURITY.md](SECURITY.md).

## Licence

Jewelcase is free software under the [GNU General Public License](LICENSE), version 3 or later.

- Jewelcase contains no games, no BIOS files and no firmware, and does not say where to get them.
- It is a personal project and is not affiliated with Anbernic, ES-DE, libretro or any emulator it lists.
  It has been used on one handheld; keep a backup of anything you cannot replace.
- The BIOS names, sizes and checksums in `src/jewelcase/data/bios.toml` come from
  [libretro-database](https://github.com/libretro/libretro-database) (CC BY-SA 4.0).
- The AppImage also carries Python, Qt for Python (PySide6, LGPL-3.0) and certifi (MPL-2.0), each
  under its own licence.

## Later

1. More emulators in the catalogue.
2. Building and publishing the Flatpak under a real app id, with `7z` and `chdman` inside it.
3. Reading game IDs from CD-type CHDs; converting a multi-disc game the library has as loose discs.
4. Restoring a single save from another handheld's set in the window (the command line can).
5. Polish for syncing over Wi-Fi only.
