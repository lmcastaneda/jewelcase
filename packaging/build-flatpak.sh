#!/usr/bin/env bash
# Build dist/Jewelcase.flatpak from dist/Jewelcase-x86_64.AppImage (see packaging/flatpak/README.md).
# The app is not installed: flatpak-builder works in .flatpak-build/ inside the project, and the
# result is a single-file bundle you install yourself with `flatpak install --user dist/Jewelcase.flatpak`.
# Needs flatpak, flatpak-builder and the org.freedesktop.Platform and Sdk 26.08 runtimes.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP_ID=local.jewelcase.Jewelcase
RUNTIME_VERSION=26.08
MANIFEST="$ROOT/packaging/flatpak/$APP_ID.json"
APPIMAGE="$ROOT/dist/Jewelcase-x86_64.AppImage"
BUILD="$ROOT/.flatpak-build"

command -v flatpak >/dev/null || { echo "flatpak is not installed. Install it from your distribution, then run this again."; exit 2; }
command -v flatpak-builder >/dev/null || {
  echo "flatpak-builder is not installed. Install it from your distribution (the package is usually called flatpak-builder), then run this again."
  exit 2
}
flatpak info "org.freedesktop.Sdk//$RUNTIME_VERSION" >/dev/null 2>&1 || {
  echo "The Flatpak SDK is missing. Install it with: flatpak install flathub org.freedesktop.Sdk//$RUNTIME_VERSION org.freedesktop.Platform//$RUNTIME_VERSION"
  exit 2
}
flatpak info "org.freedesktop.Platform//$RUNTIME_VERSION" >/dev/null 2>&1 || {
  echo "The Flatpak runtime is missing. Install it with: flatpak install flathub org.freedesktop.Platform//$RUNTIME_VERSION"
  exit 2
}
[ -f "$APPIMAGE" ] || { echo "Build the AppImage first: packaging/build-appimage.sh"; exit 2; }

mkdir -p "$ROOT/dist"
# --force-clean empties .flatpak-build/build; --disable-rofiles-fuse avoids needing FUSE.
flatpak-builder --force-clean --disable-rofiles-fuse --disable-cache \
    --repo="$BUILD/repo" --state-dir="$BUILD/state" "$BUILD/build" "$MANIFEST"
flatpak build-bundle "$BUILD/repo" "$ROOT/dist/Jewelcase.flatpak" "$APP_ID"
ls -lh "$ROOT/dist/Jewelcase.flatpak"
echo "Install it with: flatpak install --user $ROOT/dist/Jewelcase.flatpak"
