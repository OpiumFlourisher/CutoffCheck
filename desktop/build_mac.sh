#!/bin/bash
# Builds "CutoffCheck.app" on macOS.
# Usage:  cd flaccheck && bash build_mac.sh
set -e
cd "$(dirname "$0")"

echo "==> Checking Homebrew"
if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh"; exit 1
fi

echo "==> Installing ffmpeg (needed at runtime)"
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

echo "==> Finding a Python with Tkinter"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c "import tkinter" 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  V=$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  echo "    Tkinter missing — installing python-tk@$V"
  brew install "python-tk@$V"
  PY=python3
fi
echo "    using $($PY --version)"

echo "==> Creating build environment"
rm -rf .venv
$PY -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q numpy matplotlib tkinterdnd2 pyinstaller

echo "==> Building app"
rm -rf build dist
pyinstaller --noconfirm --windowed \
  --name "CutoffCheck" \
  --osx-bundle-identifier com.local.cutoffcheck \
  --collect-all tkinterdnd2 \
  flaccheck_app.py

echo
echo "Done: dist/CutoffCheck.app"
echo "Move it to Applications with:"
echo "  mv \"dist/CutoffCheck.app\" /Applications/"
