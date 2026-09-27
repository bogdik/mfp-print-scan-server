#!/usr/bin/env bash
# Builds the .deb from the repo working tree.
#
#   packaging/deb/build.sh [output-dir]
#
# Needs dpkg-deb (package dpkg-dev on Debian/Ubuntu). The result doesn't
# depend on the machine's architecture (Architecture: all) — dependencies
# are fetched from PyPI into the app's own venv on the *target* machine at
# install time, not bundled here.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT_DIR="${1:-$ROOT/dist}"
VERSION="$(tr -d ' \t\n\r' < "$ROOT/VERSION")"
PKG=mfp-print-scan-server

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STAGE="$WORK/$PKG"
PREFIX="$STAGE/opt/mfp-print-scan-server"
mkdir -p "$PREFIX" "$STAGE/DEBIAN" "$STAGE/lib/systemd/system"

echo "==> Copying application files"
cp -r "$ROOT/app" "$PREFIX/app"
cp "$ROOT/run.py" "$ROOT/requirements.txt" "$ROOT/config.example.ini" "$ROOT/start.sh" "$PREFIX/"
cp "$ROOT/README.md" "$ROOT/README.ru.md" "$ROOT/LICENSE" "$PREFIX/"
mkdir -p "$PREFIX/uploads"
find "$PREFIX" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "==> Writing control files"
sed "s/@VERSION@/$VERSION/" "$HERE/control" > "$STAGE/DEBIAN/control"
cp "$HERE/postinst" "$HERE/prerm" "$HERE/postrm" "$STAGE/DEBIAN/"
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"
cp "$HERE/mfp-print-scan-server.service" "$STAGE/lib/systemd/system/"

find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -not -path '*/DEBIAN/*' -exec chmod 0644 {} +
chmod 0755 "$PREFIX/start.sh"

mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT_FILE"
echo "==> Built $OUT_FILE"
