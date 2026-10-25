#!/usr/bin/env bash
# Builds dist/SC-Hammer-x86_64.AppImage (the version is stamped inside, not in the file name, so
# a Steam shortcut or desktop entry pointing at it keeps working after an update).
#
# The AppImage is a prebuilt portable Python with Tk (python-appimage, manylinux2014) plus this
# app, packed as squashfs behind the standard AppImage type-2 runtime. Works on macOS or Linux:
#   - with squashfs-tools installed (mksquashfs/unsquashfs; `brew install squashfs` on macOS)
#   - or on x86_64 Linux without them, using appimagetool instead (e.g. on a Steam Deck).
# Needs curl and python3. Downloads are cached in build/cache.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
CACHE="$BUILD/cache"
APPDIR="$BUILD/AppDir"
DIST="$ROOT/dist"

PY_URL="${PY_URL:-https://github.com/niess/python-appimage/releases/download/python3.12/python3.12.15-cp312-cp312-manylinux2014_x86_64.AppImage}"
RUNTIME_URL="${RUNTIME_URL:-https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64}"
TOOL_URL="${TOOL_URL:-https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage}"

# VERSION can be set by the release workflow; otherwise it's the one in sc_hammer/__init__.py.
VERSION="${VERSION:-$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); import sc_hammer as m; print(m.__version__)")}"
OUT="$DIST/SC-Hammer-x86_64.AppImage"

fetch() {
	local url="$1" dest="$CACHE/$(basename "$1")"
	if [ ! -s "$dest" ]; then
		echo "Downloading $(basename "$url")" >&2
		curl -fL --retry 3 -o "$dest.part" "$url"
		mv "$dest.part" "$dest"
	fi
	echo "$dest"
}

# An AppImage's squashfs starts right after its ELF runtime: end of the section header table.
elf_size() {
	python3 -c '
import struct, sys
d = open(sys.argv[1], "rb").read(64)
shoff, = struct.unpack_from("<Q", d, 0x28)
shentsize, shnum = struct.unpack_from("<HH", d, 0x3A)
print(shoff + shentsize * shnum)' "$1"
}

have_squashfs() { command -v mksquashfs >/dev/null && command -v unsquashfs >/dev/null; }

mkdir -p "$CACHE" "$DIST"
rm -rf "$APPDIR"

PY_APPIMAGE="$(fetch "$PY_URL")"
echo "Unpacking Python"
if have_squashfs; then
	unsquashfs -q -n -o "$(elf_size "$PY_APPIMAGE")" -d "$APPDIR" "$PY_APPIMAGE" >/dev/null
elif [ "$(uname -s)" = Linux ]; then
	chmod +x "$PY_APPIMAGE"
	(cd "$BUILD" && rm -rf squashfs-root && "$PY_APPIMAGE" --appimage-extract >/dev/null)
	mv "$BUILD/squashfs-root" "$APPDIR"
else
	echo "Install squashfs-tools first (macOS: brew install squashfs)." >&2
	exit 1
fi

echo "Trimming Python"
PYLIB="$APPDIR/opt/python3.12/lib/python3.12"
rm -rf "$APPDIR"/AppRun "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/.DirIcon \
	"$APPDIR"/usr/share/applications "$APPDIR"/usr/share/metainfo "$APPDIR"/usr/share/icons \
	"$PYLIB"/{idlelib,ensurepip,lib2to3,pydoc_data,turtledemo,test,unittest/test} \
	"$PYLIB"/site-packages/{pip,pip-*,setuptools,setuptools-*,_distutils_hack,distutils-precedence.pth} \
	"$APPDIR"/usr/share/tcltk/tk8.6/demos "$APPDIR"/opt/python3.12/bin/pip*
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +

echo "Adding the app"
SHARE="$APPDIR/usr/share/sc-hammer"
mkdir -p "$SHARE" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -R "$ROOT/sc_hammer" "$SHARE/"
# Stamp the build's version into the packaged copy, so --version matches the release.
python3 - "$SHARE/sc_hammer/__init__.py" "$VERSION" <<'PY'
import re, sys
path, version = sys.argv[1], sys.argv[2]
src = open(path).read()
open(path, "w").write(re.sub(r'__version__ = "[^"]*"', '__version__ = "%s"' % version, src))
PY
find "$SHARE" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$ROOT/packaging/run.py" "$SHARE/run.py"
cp "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
chmod 755 "$APPDIR/AppRun"
cp "$ROOT/packaging/sc-hammer.desktop" "$APPDIR/"
cp "$ROOT/packaging/sc-hammer.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT/sc_hammer/assets/icon-256.png" "$APPDIR/sc-hammer.png"
cp "$ROOT/sc_hammer/assets/icon-256.png" \
	"$APPDIR/usr/share/icons/hicolor/256x256/apps/sc-hammer.png"
ln -sf sc-hammer.png "$APPDIR/.DirIcon"

echo "Packing $OUT"
rm -f "$OUT"
if have_squashfs; then
	RUNTIME="$(fetch "$RUNTIME_URL")"
	mksquashfs "$APPDIR" "$BUILD/app.squashfs" -noappend -all-root -no-xattrs \
		-comp zstd -b 1M -quiet >/dev/null
	cat "$RUNTIME" "$BUILD/app.squashfs" >"$OUT"
	rm -f "$BUILD/app.squashfs"
else
	TOOL="$(fetch "$TOOL_URL")"
	chmod +x "$TOOL"
	ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT"
fi
chmod 755 "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
