#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

source venv/bin/activate

python -m pip install -e '.[dev]'

rm -rf build dist
pyinstaller --clean --noconfirm allenedwards-cli.spec

cat <<MSG
Build complete.
Executable: dist/allenedwards

To create a universal2 binary, build once on Intel macOS and once on Apple Silicon,
then combine the two dist/allenedwards binaries with:
  lipo -create -output allenedwards-universal2 <intel_binary> <arm64_binary>
MSG
