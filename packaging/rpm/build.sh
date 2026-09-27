#!/usr/bin/env bash
# Builds the .rpm from the repo working tree.
#
#   packaging/rpm/build.sh [output-dir]
#
# Needs rpmbuild (package rpm-build on Fedora/RHEL, rpm on openSUSE). Like
# the .deb, this is a noarch package — dependencies are fetched from PyPI
# into the app's own venv on the *target* machine at install time.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT_DIR="${1:-$ROOT/dist}"
VERSION="$(tr -d ' \t\n\r' < "$ROOT/VERSION")"
NAME=mfp-print-scan-server

TOPDIR="$(mktemp -d)"
trap 'rm -rf "$TOPDIR"' EXIT
mkdir -p "$TOPDIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

echo "==> Building source tarball"
SRC_DIR="$TOPDIR/SOURCES/$NAME-$VERSION"
mkdir -p "$SRC_DIR/packaging/rpm"
cp -r "$ROOT/app" "$SRC_DIR/app"
cp "$ROOT/run.py" "$ROOT/requirements.txt" "$ROOT/config.example.ini" "$ROOT/start.sh" "$SRC_DIR/"
cp "$ROOT/README.md" "$ROOT/README.ru.md" "$ROOT/LICENSE" "$SRC_DIR/"
cp "$HERE/mfp-print-scan-server.service" "$SRC_DIR/packaging/rpm/"
find "$SRC_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
tar -C "$TOPDIR/SOURCES" -czf "$TOPDIR/SOURCES/$NAME-$VERSION.tar.gz" "$NAME-$VERSION"
rm -rf "$SRC_DIR"

echo "==> Running rpmbuild"
rpmbuild --define "_topdir $TOPDIR" --define "_mfp_version $VERSION" \
    -bb "$HERE/mfp-print-scan-server.spec"

mkdir -p "$OUT_DIR"
find "$TOPDIR/RPMS" -name '*.rpm' -exec cp {} "$OUT_DIR/" \;
echo "==> Built:"
find "$OUT_DIR" -name "${NAME}-${VERSION}*.rpm"
