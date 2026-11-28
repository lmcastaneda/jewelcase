#!/usr/bin/env bash
# Runs inside the build container (see build-appimage.sh). Leaves nothing in the source tree but dist/.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Tools for the build, plus the display libraries Qt's plugins link to, so PyInstaller can find and
# bundle the ones that are not part of a base desktop (libxcb-cursor is the usual missing one).
apt-get install -y -qq --no-install-recommends \
    ca-certificates curl file binutils squashfs-tools desktop-file-utils \
    libgl1 libegl1 libfontconfig1 libfreetype6 libdbus-1-3 libglib2.0-0 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxcb-xfixes0 libxcb-sync1 libxcb-shm0 libxcb-render0 \
    libx11-xcb1 libwayland-client0 libwayland-cursor0 libwayland-egl1 >/dev/null

# The tools the build downloads, each at an exact version and checked against its sha256 before it
# runs. To move one, change both lines together (the release page lists each file's sha256).
UV_VERSION=0.13.10
UV_SHA256=bf65ac1a0af10f92c522d91ac096c7c0a39657647b155577e7836c2cbbb4526c
APPIMAGETOOL_VERSION=1.9.1
APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0

# fetch URL SHA256 DEST: download under a temporary name and give it its real name only once checked.
fetch() {
    curl -LsSf -o "$3.part" "$1"
    if ! echo "$2  $3.part" | sha256sum --check --status; then
        echo "checksum mismatch, stopping: $1" >&2
        rm -f "$3.part"
        exit 1
    fi
    mv "$3.part" "$3"
}
fetch "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" "$UV_SHA256" /tmp/uv.tar.gz
fetch "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage" "$APPIMAGETOOL_SHA256" /tmp/appimagetool
chmod +x /tmp/appimagetool

mkdir -p /tmp/uv-bin
tar -xzf /tmp/uv.tar.gz -C /tmp/uv-bin --strip-components=1
export PATH="/tmp/uv-bin:$PATH"
export UV_PROJECT_ENVIRONMENT=/tmp/venv UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy
uv python install 3.12 >/dev/null
uv sync --python 3.12 --no-dev --frozen
uv pip install --python /tmp/venv/bin/python pyinstaller >/dev/null

/tmp/venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
    --name jewelcase --onedir --windowed \
    --distpath /tmp/dist --workpath /tmp/build --specpath /tmp \
    --add-data "/src/src/jewelcase/data:jewelcase/data" \
    --collect-data certifi \
    /src/packaging/entry.py

# Libraries every desktop already has, and that must match the host's graphics stack: use the host's.
cd /tmp/dist/jewelcase/_internal
rm -f libstdc++.so.6 libgcc_s.so.1 libz.so.1 libfontconfig.so.1 libfreetype.so.6 libexpat.so.1 \
      libGL.so.1 libEGL.so.1 libGLX.so.0 libGLdispatch.so.0 libOpenGL.so.0 libdrm.so.2 libgbm.so.1 \
      libX11.so.6 libX11-xcb.so.1 libxcb.so.1 libglib-2.0.so.0 libgobject-2.0.so.0 libgio-2.0.so.0
# (libgthread-2.0 and libgmodule-2.0 stay bundled: openSUSE ships them as separate packages that a
#  minimal install lacks, and they are thin shims over the host's libglib.)

APPDIR=/tmp/Jewelcase.AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a /tmp/dist/jewelcase "$APPDIR/usr/bin/jewelcase"
install -m 755 /src/packaging/AppRun "$APPDIR/AppRun"
cp /src/packaging/jewelcase.desktop "$APPDIR/jewelcase.desktop"
cp /src/src/jewelcase/data/jewelcase.svg "$APPDIR/jewelcase.svg"
cp /src/src/jewelcase/data/jewelcase.svg "$APPDIR/.DirIcon"
desktop-file-validate "$APPDIR/jewelcase.desktop"

ARCH=x86_64 /tmp/appimagetool --appimage-extract-and-run --no-appstream "$APPDIR" /src/dist/Jewelcase-x86_64.AppImage
chmod 755 /src/dist/Jewelcase-x86_64.AppImage
chown "$(stat -c %u:%g /src)" /src/dist/Jewelcase-x86_64.AppImage 2>/dev/null || true
du -sh "$APPDIR" /src/dist/Jewelcase-x86_64.AppImage
