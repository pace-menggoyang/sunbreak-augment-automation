#!/usr/bin/env bash
# Vendors a relocatable copy of the Homebrew-installed tesseract binary,
# its full dylib dependency closure, and English tessdata into
# packaging/vendor/macos/tesseract/, for PyInstaller to bundle as
# resources/tesseract/ (see qurio-aug.spec and qurio_aug/tesseract_setup.py).
# Run this before `pyinstaller packaging/qurio-aug.spec`.
#
# Requires: brew install tesseract dylibbundler
set -euo pipefail

TESSERACT_PREFIX="$(brew --prefix tesseract)"
OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/packaging/vendor/macos/tesseract"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp "$TESSERACT_PREFIX/bin/tesseract" "$OUT_DIR/tesseract"

# dylibbundler copies the *full* dependency closure (not just tesseract's
# direct deps) and rewrites its load commands to be relocatable -- a bare
# `cp` of just the binary breaks at runtime on its hard-coded Homebrew
# paths (e.g. /opt/homebrew/lib/...) once it's not running from that
# install location anymore.
dylibbundler -od -b \
  -x "$OUT_DIR/tesseract" \
  -d "$OUT_DIR/libs" \
  -p "@executable_path/libs"

mkdir -p "$OUT_DIR/tessdata"
cp "$TESSERACT_PREFIX/share/tessdata/eng.traineddata" "$OUT_DIR/tessdata/"

echo "vendored tesseract -> $OUT_DIR"
"$OUT_DIR/tesseract" --version
