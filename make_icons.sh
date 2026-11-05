#!/bin/zsh
# Turn icon.png into the platform icon files the build needs.
#
#   ./make_icons.sh [source.png]        # default: icon.png
#
# Produces:
#   Max2OSC.icns   macOS app icon
#   Max2OSC.ico    Windows executable icon
#   icon_256.png        window icon when running from source
#
# Run on macOS -- it uses sips and iconutil, which ship with the OS. The three
# outputs are committed, so Windows and Linux builds need no icon tooling.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
SRC="${1:-icon.png}"
[ -f "$SRC" ] || { echo "No $SRC. Supply a square PNG, 1024x1024 ideally." >&2; exit 1; }

W=$(sips -g pixelWidth  "$SRC" | awk '/pixelWidth/{print $2}')
H=$(sips -g pixelHeight "$SRC" | awk '/pixelHeight/{print $2}')
[ "$W" = "$H" ] || echo "warning: $SRC is ${W}x${H}, not square -- it will be squashed" >&2
[ "$W" -ge 512 ] || echo "warning: $SRC is only ${W}px; 1024 gives a crisper result" >&2

# --- macOS .icns ----------------------------------------------------------
SET=".build-iconset/Max2OSC.iconset"
rm -rf .build-iconset && mkdir -p "$SET"
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
            "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
  px="${spec%% *}"; name="${spec#* }"
  sips -z "$px" "$px" "$SRC" --out "$SET/$name.png" >/dev/null 2>&1
done
iconutil -c icns "$SET" -o Max2OSC.icns

# --- window icon for running from source ---------------------------------
sips -z 256 256 "$SRC" --out icon_256.png >/dev/null 2>&1

# --- menu bar icons -------------------------------------------------------
# Drawn separately: a template image takes its shape from alpha, so the app
# icon's opaque background would render as a solid block.
python3 "$HERE/make_icon_art.py" --menubar >/dev/null && echo "  menubar_idle.png / menubar_active.png"

# --- Windows .ico ---------------------------------------------------------
# An .ico is a small container; since Windows Vista its entries may be PNGs,
# so this needs no image library -- just the PNGs sips already produced.
mkdir -p .build-iconset/ico
for px in 16 32 48 64 128 256; do
  sips -z "$px" "$px" "$SRC" --out ".build-iconset/ico/$px.png" >/dev/null 2>&1
done
python3 - <<'PY'
import struct
sizes = [16, 32, 48, 64, 128, 256]
blobs = [open(f".build-iconset/ico/{s}.png", "rb").read() for s in sizes]
out = struct.pack("<HHH", 0, 1, len(sizes))          # reserved, type=icon, count
offset = 6 + 16 * len(sizes)
for s, blob in zip(sizes, blobs):
    out += struct.pack("<BBBBHHII",
                       0 if s == 256 else s,          # 0 means 256
                       0 if s == 256 else s,
                       0, 0, 1, 32, len(blob), offset)
    offset += len(blob)
out += b"".join(blobs)
open("Max2OSC.ico", "wb").write(out)
print(f"  Max2OSC.ico   {len(out)/1024:.0f} KB, {len(sizes)} sizes")
PY

rm -rf .build-iconset
echo "  Max2OSC.icns  $(du -k Max2OSC.icns | cut -f1) KB"
echo "  icon_256.png       $(du -k icon_256.png | cut -f1) KB"
echo "done -- rebuild with ./build_app.sh to pick them up"
