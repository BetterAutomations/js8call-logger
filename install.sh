#!/bin/sh
# Offline install for js8call-logger.
#
# Copies the tree, unpacks the bundled JS8Call AppImage (no FUSE needed) and
# links the command. Nothing is downloaded.
#
#   ./install.sh                     -> /opt/js8call-logger, needs sudo
#   ./install.sh --prefix ~/.local   -> user-local, no sudo
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
prefix=/opt
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix=${2:?--prefix needs a directory}; shift 2 ;;
        --prefix=*) prefix=${1#--prefix=}; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
dest=$prefix/js8call-logger
bindir=$prefix/bin
sudo=
if [ "$(id -u)" -ne 0 ]; then
    probe=$prefix
    while [ ! -e "$probe" ]; do probe=$(dirname "$probe"); done
    [ -w "$probe" ] || sudo=sudo
fi

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 10):
    sys.exit("python3 >= 3.10 required, found " + sys.version.split()[0])
PY

echo "installing to $dest"
$sudo mkdir -p "$dest" "$bindir"
$sudo rm -rf "$dest/js8call_logger"
$sudo cp -a "$here/js8call_logger" "$dest/"
$sudo cp -a "$here/tests" "$dest/"
$sudo cp -a "$here/README.md" "$here/FIXME.txt" "$dest/"
$sudo cp -a "$here/js8call-logger" "$dest/js8call-logger"

# The decoder: unpack once, so the install needs neither FUSE nor a network.
if [ -f "$here/bundle/JS8Call.AppImage" ]; then
    if [ ! -x "$dest/app/usr/bin/JS8Call" ]; then
        echo "unpacking JS8Call (59 MB AppImage)"
        tmp=$(mktemp -d)
        cp "$here/bundle/JS8Call.AppImage" "$tmp/JS8Call.AppImage"
        chmod +x "$tmp/JS8Call.AppImage"
        ( cd "$tmp" && ./JS8Call.AppImage --appimage-extract >/dev/null )
        $sudo rm -rf "$dest/app"
        $sudo mv "$tmp/squashfs-root" "$dest/app"
        rm -rf "$tmp"
    fi
    $sudo chmod +x "$dest/app/AppRun" 2>/dev/null || true
    decoder=$dest/app/usr/bin/JS8Call
else
    echo "warning: bundle/JS8Call.AppImage missing - run refresh-bundle.sh first"
    decoder=
fi

# Launcher that knows where it lives.
$sudo tee "$bindir/js8call-logger" >/dev/null <<EOF
#!/bin/sh
PYTHONPATH="$dest\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m js8call_logger.cli "\$@"
EOF
$sudo chmod 755 "$bindir/js8call-logger"

# First-run config, only if the user has none yet.
home=${JS8CALL_LOGGER_HOME:-$HOME/js8call-logger}
mkdir -p "$home"
if [ ! -f "$home/config.json" ] && [ -n "$decoder" ]; then
    python3 - "$home/config.json" "$decoder" <<'PY'
import json, sys
path, decoder = sys.argv[1], sys.argv[2]
json.dump({"js8call_binary": decoder}, open(path, "w"), indent=2)
PY
    echo "wrote $home/config.json"
fi

echo "done. Next: js8call-logger doctor && js8call-logger start"
