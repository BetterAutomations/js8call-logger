#!/bin/sh
# Copy a JS8Call AppImage into bundle/ and record its checksum.
#
#   ./refresh-bundle.sh /path/to/JS8Call-v3.0.3-x86_64.AppImage
#
# Nothing is downloaded here on purpose: the bundle is built on a connected
# machine and carried to the air-gapped one.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
src=${1:?usage: refresh-bundle.sh /path/to/JS8Call.AppImage}

[ -f "$src" ] || { echo "no such file: $src" >&2; exit 1; }
case "$(basename "$src")" in
    *.AppImage) ;;
    *) echo "not an AppImage: $src" >&2; exit 1 ;;
esac

mkdir -p "$here/bundle"
cp -f "$src" "$here/bundle/JS8Call.AppImage"
chmod +x "$here/bundle/JS8Call.AppImage"

( cd "$here/bundle" && sha256sum JS8Call.AppImage > JS8Call.AppImage.sha256 )
ls -l "$here/bundle/JS8Call.AppImage"
cat "$here/bundle/JS8Call.AppImage.sha256"
