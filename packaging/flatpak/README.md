# Jewelcase as a Flatpak

Built from the AppImage: `packaging/build-appimage.sh` first, then `packaging/build-flatpak.sh`
(needs `flatpak-builder` and the `org.freedesktop.Sdk` and `Platform` 26.08 runtimes). The manifest
extracts the AppImage at build time and installs its tree under `/app/lib/jewelcase`, with `/app/bin/jewelcase`
as the launcher. Stripping is switched off: it breaks PyInstaller binaries. The runtime is 26.08, the
newest freedesktop runtime installed on the development machine; the AppImage ran against it unchanged.

## App id

The app id is `dev.luismcastaneda.Jewelcase`, and the developer id in the metainfo is `dev.luismcastaneda`.
The id is the name of the three files in this folder (the manifest, the desktop file and the metainfo)
and appears inside each of them; `packaging/build-flatpak.sh` and `tests/test_flatpak.py` carry it too.
Nothing in `src/` contains it: the app reads it from `FLATPAK_ID` at run time.

## Permissions (`finish-args`)

| Argument | Why |
|---|---|
| `--socket=wayland`, `--socket=fallback-x11` | The window: Wayland, or X11 when there is no Wayland. |
| `--share=ipc` | Qt's X11 shared memory. |
| `--device=all` | adb talks to the handheld through `/dev/bus/usb`. Flatpak has no narrower USB permission. Reading the device still needs the host's Android udev rules, as with the AppImage. |
| `--share=network` | adb over Wi-Fi, emulator release checks, the platform-tools download. It also lets the sandbox's adb reach an adb server already running on the host (port 5037). |
| `--filesystem=home` | Libraries, the downloads folder, archives to convert. |
| `--filesystem=/mnt`, `/media`, `/run/media` | A library or archive on another disk, and SD cards. Writing converted games beside their archive needs write access, so none of these is read-only. |
| `--talk-name=org.freedesktop.Notifications` | "Sync finished" notifications, sent with `notify-send`/`gdbus`, both in the runtime. |

## What differs from the AppImage

- Settings, the database and downloads live under `~/.var/app/dev.luismcastaneda.Jewelcase/{config,data,cache}`,
  not `~/.config/jewelcase` and friends. The library is chosen again on first run (the home folder is
  readable, so the old library folder is simply picked); old settings can be copied across.
- adb: the host's `adb` is not visible inside the sandbox, so the Device page offers to download Google's
  platform-tools into the app's data folder (it runs inside the sandbox). If a host adb server is
  already running, the sandbox's adb uses it; a different adb version on each side makes them restart
  each other's server.
- "Add to Applications Menu" is hidden: `integrate.appimage_path()` is None in a Flatpak. The Flatpak
  installs its own desktop file and icon.
- The desktop file is named after the app id and the app sets that as its desktop file name, so
  Wayland shells match the window to the icon.
- One instance: the socket moves to `$XDG_RUNTIME_DIR/app/<id>/`, which every launch of the app
  shares (the sandbox's `/tmp` is private, so the usual socket would not be found).
- The dock progress signal (`com.canonical.Unity.LauncherEntry`) may not get through the sandbox's bus
  filter; it is best effort and nothing breaks without it.
- `7z` and `chdman` are not in the runtime and the manifest does not bundle them, so inside the Flatpak
  archives cannot be inspected or converted and discs are not made into CHD (the app says "7z is not
  installed." and carries on). Bundling means adding a module that builds them from a source archive
  with a pinned sha256 (p7zip or 7-Zip for `7z`; MAME's `chdman` tool is a big build), which needs
  network access to fetch and hash; it was left out rather than guess a checksum.
  Running the host's tools with `flatpak-spawn --host` would need `--talk-name=org.freedesktop.Flatpak`,
  which is a sandbox escape, so it is not done.

## Built and checked

`packaging/build-flatpak.sh` builds the bundle with flatpak-builder 1.4.8 and Flatpak 1.16.6 against the
26.08 runtimes, from the AppImage unchanged and with this manifest as it is: `dist/Jewelcase.flatpak`
is about 39 MB and installs as about 155 MB.

`packaging/check-flatpak.sh [bundle]` checks a bundle without installing it for you. It installs the
bundle into a throwaway Flatpak user installation under `.flatpak-build/check` (`FLATPAK_USER_DIR`), with
`HOME` pointed there as well, so nothing appears in the menus, `~/.local/share/flatpak` or `~/.var/app`,
and it removes that folder when it ends. It prints one line per check and exits non-zero at the first
that fails:

- the bundle exists, and `flatpak` is installed;
- it installs, against the Platform runtime already installed on the machine;
- the desktop file, icon and metainfo are named after the app id, and the desktop file is exported;
- `flatpak run --env=QT_QPA_PLATFORM=offscreen dev.luismcastaneda.Jewelcase --demo gui --selftest` prints
  `selftest ok`, so the real bundle starts in its sandbox with the permissions above, on demo data.

`7z` and `chdman` are still not inside; see above.
