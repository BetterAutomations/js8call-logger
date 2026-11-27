#!/bin/sh
# Download the system packages an offline machine needs, for one Ubuntu suite.
#
#   ./tools/fetch-offline-packages.sh resolute     # 26.04  (Xubuntu 26.04)
#   ./tools/fetch-offline-packages.sh stonking     # 26.10  (Lubuntu 26.10)
#
# Nothing here is needed to *run* the logger: its python is standard library
# only and the decoder ships in bundle/. These are the packages a stock
# flavour does not install - the PulseAudio client tools (pactl, pacat, parec)
# and the rtl-sdr utilities (rtl_fm, rtl_test, rtl_biast) - plus their
# non-base dependencies. Packages with Priority required/important are left
# out on purpose: every install already has them, and a downloaded libc6 is
# the one thing you never want to force offline.
#
# Nothing is downloaded by install.sh; the bundle is built here, on a
# connected machine, and carried across.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
suite=${1:?usage: $0 <suite> [mirror]   (resolute = 26.04, stonking = 26.10)}
mirror=${2:-http://archive.ubuntu.com/ubuntu}
seeds="pulseaudio-utils rtl-sdr"
out=$here/bundle/debs/$suite

case $suite in
    resolute) release=26.04 ;;
    stonking) release=26.10 ;;
    *) release=$suite ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp"/lists/partial "$out"
: > "$tmp/status"
printf 'deb %s %s main universe\n' "$mirror" "$suite" > "$tmp/sources.list"

# A private apt: its own lists, its own empty dpkg status, so the dependency
# closure is what a *blank* machine would need, not this one.
apt="apt-get -o Dir::Etc::sourcelist=$tmp/sources.list
     -o Dir::Etc::sourceparts=/dev/null
     -o Dir::State::lists=$tmp/lists
     -o Dir::State::status=$tmp/status
     -o Dir::Cache::archives=$tmp/archives"
cache="apt-cache -o Dir::Etc::sourcelist=$tmp/sources.list
       -o Dir::Etc::sourceparts=/dev/null
       -o Dir::State::lists=$tmp/lists"

echo "suite $suite ($release), mirror $mirror"
$apt update >/dev/null 2>&1 || { echo "cannot read $mirror $suite" >&2; exit 1; }
$apt --print-uris --no-install-recommends -y install $seeds 2>/dev/null \
    | grep "^'" > "$tmp/uris" || true
[ -s "$tmp/uris" ] || { echo "no packages found for $seeds in $suite" >&2; exit 1; }

# name_version_arch.deb -> name
sed "s/'//g" "$tmp/uris" | awk -F/ '{print $NF}' | sed 's/_.*//' \
    | sort -u > "$tmp/names"
# shellcheck disable=SC2086
$cache show $(cat "$tmp/names") > "$tmp/show" 2>/dev/null || true

# Drop what every install already has (and what apt would fight over).
awk '/^Package:/ {p=$2}
     /^Essential: *yes/ {ess[p]=1}
     /^Priority:/ {pri[p]=$2}
     END {for (n in pri)
              if (!ess[n] && pri[n] != "required" && pri[n] != "important")
                  print n}' "$tmp/show" | sort > "$tmp/want"

echo "downloading $(wc -l < "$tmp/want") of $(wc -l < "$tmp/names") packages"
while read -r line; do
    url=$(printf '%s' "$line" | sed "s/^'//; s/'.*$//")
    deb=${url##*/}
    # Ubuntu escapes '+' and '~' in pool URLs; the deb does not escape them.
    deb=$(printf '%s' "$deb" | sed 's/%2b/+/g; s/%2B/+/g; s/%7e/~/g; s/%7E/~/g')
    name=$(printf '%s' "$deb" | sed 's/_.*//')
    grep -qx "$name" "$tmp/want" || continue
    if [ -s "$out/$deb" ]; then
        echo "  have $deb"
        continue
    fi
    echo "  get  $deb"
    curl -fsSL -o "$out/$deb.part" "$url" || { echo "failed: $url" >&2; exit 1; }
    mv "$out/$deb.part" "$out/$deb"
done < "$tmp/uris"

( cd "$out" && sha256sum ./*.deb > SHA256SUMS )
# name  version  priority  one-line description, read back from the debs that
# were actually downloaded - apt-cache also lists versions we did not take.
for deb in "$out"/*.deb; do
    printf '%s\t%s\t%s\t%s\n' \
        "$(dpkg-deb -f "$deb" Package)" \
        "$(dpkg-deb -f "$deb" Version)" \
        "$(dpkg-deb -f "$deb" Priority)" \
        "$(dpkg-deb -f "$deb" Description | head -n 1)"
done | sort > "$out/MANIFEST.txt"

echo
echo "bundle/debs/$suite: $(ls "$out"/*.deb | wc -l) debs, $(du -sh "$out" | cut -f1)"
echo "install on the air-gapped machine with:"
echo "  sudo dpkg -i bundle/debs/*.deb      # from the same release"
