#!/usr/bin/env bash
# Launch the AppImage headless on each major distro and report. Each container gets only the base
# graphics and font libraries that a desktop install of that distro already has.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP="$ROOT/dist/Jewelcase-x86_64.AppImage"
[ -f "$APP" ] || { echo "Build it first: packaging/build-appimage.sh"; exit 2; }

DEB='export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends libgl1 libegl1 libfontconfig1 libxkbcommon0 libdbus-1-3 libglib2.0-0 ca-certificates >/dev/null 2>&1'
RPM='dnf install -y -q mesa-libGL mesa-libEGL fontconfig libxkbcommon dbus-libs glib2 >/dev/null 2>&1'
ARCH='pacman -Sy --noconfirm --needed libglvnd fontconfig libxkbcommon dbus glib2 >/dev/null 2>&1'
SUSE='zypper -n -q install Mesa-libGL1 Mesa-libEGL1 fontconfig libxkbcommon0 libdbus-1-3 libglib-2_0-0 >/dev/null 2>&1'

TARGETS=(
  "docker.io/library/ubuntu:22.04|$DEB"
  "docker.io/library/ubuntu:24.04|$DEB"
  "docker.io/library/debian:12|$DEB"
  "docker.io/library/debian:13|$DEB"
  "docker.io/library/fedora:latest|$RPM"
  "docker.io/library/archlinux:latest|$ARCH"
  "docker.io/opensuse/leap:15.6|$SUSE"
  "docker.io/opensuse/tumbleweed:latest|$SUSE"
)
[ $# -gt 0 ] && TARGETS=("$@")

fail=0
printf '%-40s %-8s %s\n' "distro" "result" "detail"
for target in "${TARGETS[@]}"; do
  image=${target%%|*}
  setup=${target#*|}
  out=$(timeout "${JEWELCASE_CHECK_TIMEOUT:-420}" podman run --rm --security-opt label=disable -v "$ROOT/dist":/app:ro "$image" sh -c "
      $setup
      glibc=\$(ldd --version 2>&1 | head -1 | grep -o '[0-9.]*\$')
      cd /tmp && QT_QPA_PLATFORM=offscreen /app/Jewelcase-x86_64.AppImage --appimage-extract-and-run --demo gui --selftest 2>&1 | tail -5
      echo \"glibc \$glibc\"" 2>&1)
  if echo "$out" | grep -q '^selftest ok'; then
    result=pass
  else
    result=FAIL
    fail=1
  fi
  detail=$(echo "$out" | grep -E '^(https|glibc|games|pictures)' | tr '\n' ' ')
  [ "$result" = FAIL ] && detail=$(echo "$out" | tail -3 | tr '\n' ' ' | cut -c1-200)
  [ "$result" = FAIL ] && [ -z "$out" ] && detail="no output: the container did not finish in time (package mirror?)"
  printf '%-40s %-8s %s\n' "${image#docker.io/}" "$result" "$detail"
done
exit $fail
