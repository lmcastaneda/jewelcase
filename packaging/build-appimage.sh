#!/usr/bin/env bash
# Build dist/Jewelcase-x86_64.AppImage inside an Ubuntu 22.04 container (glibc 2.35), so the result
# runs on that and anything newer. Needs podman and network access.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${JEWELCASE_BUILD_IMAGE:-docker.io/library/ubuntu:22.04}
mkdir -p "$ROOT/dist"
podman run --rm --security-opt label=disable -v "$ROOT":/src -w /src "$IMAGE" bash packaging/inside-container.sh
ls -lh "$ROOT/dist/Jewelcase-x86_64.AppImage"
# The finished app image lands in the dist folder.
