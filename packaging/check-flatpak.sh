#!/usr/bin/env bash
# Check dist/Jewelcase.flatpak (or the bundle given as the first argument) without installing it for you.
# The bundle goes into a throwaway Flatpak user installation under .flatpak-build/check, with HOME
# pointed there too, so nothing lands in your menus, ~/.local/share/flatpak or ~/.var/app. The app then
# runs headless on demo data, against the real Platform runtime and with its real permissions.
# Prints one line per check and exits non-zero at the first one that fails.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP_ID=dev.luismcastaneda.Jewelcase
BUNDLE=${1:-$ROOT/dist/Jewelcase.flatpak}
CHECK="$ROOT/.flatpak-build/check"

pass() { echo "pass  $*"; }
fail() { echo "FAIL  $*"; exit 1; }

[ -f "$BUNDLE" ] || fail "bundle: $BUNDLE does not exist (build it with packaging/build-flatpak.sh)"
pass "bundle: $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"
command -v flatpak >/dev/null || fail "flatpak: not installed"
pass "flatpak: $(flatpak --version)"

# The throwaway installation and home. The XDG_* variables would otherwise lead back to the real home.
rm -rf "$CHECK"
mkdir -p "$CHECK/home"
trap 'rm -rf "$CHECK"' EXIT
export FLATPAK_USER_DIR="$CHECK/flatpak"
export HOME="$CHECK/home"
unset XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_STATE_HOME

out=$(flatpak install --user --noninteractive -y --bundle "$BUNDLE" 2>&1) \
  || fail "install: $(echo "$out" | tail -1)"
flatpak info --user "$APP_ID" >/dev/null 2>&1 || fail "install: $APP_ID is not in the throwaway installation"
pass "install: $APP_ID into $FLATPAK_USER_DIR"

# The desktop file, icon and metainfo must carry the app id exactly, or menus and stores miss them.
files="$(flatpak info --user --show-location "$APP_ID")/files/share"
for f in "applications/$APP_ID.desktop" "icons/hicolor/scalable/apps/$APP_ID.svg" "metainfo/$APP_ID.metainfo.xml"; do
  [ -f "$files/$f" ] || fail "files: $f is missing from the app"
done
[ -f "$FLATPAK_USER_DIR/exports/share/applications/$APP_ID.desktop" ] || fail "files: the desktop file was not exported"
pass "files: desktop file, icon and metainfo named $APP_ID, desktop file exported"

out=$(timeout 300 flatpak run --env=QT_QPA_PLATFORM=offscreen "$APP_ID" --demo gui --selftest 2>&1)
status=$?
echo "$out" | grep -q '^selftest ok' \
  || fail "selftest: exit $status, $(echo "$out" | tail -3 | tr '\n' ' ' | cut -c1-300)"
pass "selftest: $(echo "$out" | grep '^selftest ok')"
